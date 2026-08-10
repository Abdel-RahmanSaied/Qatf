"""Small shared helpers. No pipeline logic and no state."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys
import threading
import time

from .errors import CommandFailed, FFmpegNotFound

#: Binaries whose location can be overridden, for hosts where ffmpeg is
#: installed but not on PATH. `QATF_FFMPEG=/opt/ffmpeg/bin/ffmpeg` is safer than
#: rewriting PATH, which on a bad expansion takes every other tool with it.
_BIN_ENV = {"ffmpeg": "QATF_FFMPEG", "ffprobe": "QATF_FFPROBE"}


def binary(name: str) -> str:
    """Resolve a tool name to its configured path, or leave it for PATH lookup."""
    var = _BIN_ENV.get(name)
    return os.environ.get(var) or name if var else name


def run(cmd: list[str], quiet: bool = True) -> None:
    """Run a subprocess, raising with useful context on failure."""
    cmd = [binary(cmd[0]), *cmd[1:]]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise FFmpegNotFound(f"{cmd[0]} not found on PATH") from exc
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or "").strip().splitlines()[-15:])
        raise CommandFailed(f"command failed: {shlex.join(cmd)}\n{tail}")


def check_ffmpeg() -> None:
    """Fail at startup rather than three minutes into a transcription."""
    exe = binary("ffmpeg")
    try:
        subprocess.run([exe, "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        where = f"at {exe}" if exe != "ffmpeg" else "on PATH"
        raise FFmpegNotFound(
            f"ffmpeg not found {where} — install it, or point QATF_FFMPEG at it"
        ) from exc


def probe_video(path) -> dict:
    """Width/height of the first video stream, via ffprobe.

    Used to resolve `--resolution source`, which needs the real dimensions
    rather than an assumption about what the camera produced."""
    out = subprocess.run(
        [binary("ffprobe"), "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise CommandFailed(f"ffprobe failed on {path}: {(out.stderr or '').strip()}")
    try:
        w, h = out.stdout.strip().splitlines()[0].split("x")[:2]
        return {"width": int(w), "height": int(h)}
    except (IndexError, ValueError) as exc:
        raise CommandFailed(
            f"could not read dimensions from {path}: {out.stdout!r}") from exc


def probe_duration(path) -> float | None:
    """Container duration in seconds, or None when it cannot be determined.

    None rather than an exception: plenty of valid containers (a raw stream, a
    still-growing recording) simply do not carry one, and the callers use this
    to *clamp* a range. Not knowing the end of the video is a reason to skip the
    clamp, not to fail a render."""
    out = subprocess.run(
        [binary("ffprobe"), "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        return None
    try:
        value = float(out.stdout.strip().splitlines()[0])
    except (IndexError, ValueError):
        return None
    return value if value > 0 else None


#: `/healthz` answers this on every request, and `check_ffmpeg` spawns a
#: process. Measured under concurrent polling that put /healthz at p99 > 1s —
#: twenty times the endpoint that does real work — because process creation
#: dominates and serialises behind the threadpool.
#:
#: ffmpeg does not appear and disappear mid-run, so the answer is cached. The
#: TTL is short rather than permanent so a health check still self-heals if
#: ffmpeg is installed while the server is up.
#: `/healthz` answers this on every request, and `check_ffmpeg` spawns a
#: process. Measured under concurrent polling that put /healthz at p99 > 1s —
#: twenty times `GET /jobs/{id}`, which does real work.
#:
#: Two obvious fixes both failed, which is why this is written the way it is:
#:
#:   - A plain TTL cache still lets every thread arriving on a COLD cache spawn
#:     its own probe. 24 connections pointed at /healthz produced 24 concurrent
#:     spawns; p99 114ms.
#:   - Putting a lock around the probe made it WORSE (p99 199ms, max 357ms):
#:     one spawn instead of 24, but now 23 threads block on it. Serialising a
#:     slow thing is not the same as removing it.
#:
#: So the probe never runs on the request path. `create_app` primes it at
#: startup, and an expired entry is served stale while a daemon thread
#: refreshes — the answer is a health flag, and a 30-second-old one is worth
#: far more than a fresh one that costs a request 300ms.
FFMPEG_PROBE_TTL = 30.0
_ffmpeg_probe: tuple[float, bool] | None = None
_ffmpeg_refreshing = False
_ffmpeg_lock = threading.Lock()


def _probe_ffmpeg() -> bool:
    try:
        check_ffmpeg()
        return True
    except FFmpegNotFound:
        return False


def _refresh_ffmpeg() -> None:
    global _ffmpeg_probe, _ffmpeg_refreshing
    try:
        ok = _probe_ffmpeg()
        _ffmpeg_probe = (time.monotonic(), ok)
    finally:
        with _ffmpeg_lock:
            _ffmpeg_refreshing = False


def prime_ffmpeg_probe() -> bool:
    """Run the probe once, synchronously. Call at startup, off the request path."""
    global _ffmpeg_probe
    ok = _probe_ffmpeg()
    _ffmpeg_probe = (time.monotonic(), ok)
    return ok


def ffmpeg_available(max_age: float = FFMPEG_PROBE_TTL) -> bool:
    """Whether ffmpeg is genuinely runnable. Never blocks once primed.

    `max_age=0` forces a synchronous probe, which is what a test wants."""
    global _ffmpeg_refreshing
    if max_age <= 0:
        return prime_ffmpeg_probe()

    cached = _ffmpeg_probe
    if cached is None:                       # not primed — nothing to serve
        return prime_ffmpeg_probe()
    if time.monotonic() - cached[0] < max_age:
        return cached[1]

    # stale: hand back the old answer and refresh behind the caller's back
    with _ffmpeg_lock:
        if not _ffmpeg_refreshing:
            _ffmpeg_refreshing = True
            threading.Thread(target=_refresh_ffmpeg, name="qatf-ffmpeg-probe",
                             daemon=True).start()
    return cached[1]


def ts_ass(seconds: float) -> str:
    """Seconds -> ASS timestamp H:MM:SS.cc

    Rounds to centiseconds FIRST and decomposes after. Rounding at the end
    instead needs the carry handled at every level: the earlier version bumped
    seconds on a centisecond spill but never carried 60s into a minute, so
    59.999 formatted as the invalid `0:00:60.00`."""
    total_cs = int(round(max(0.0, seconds) * 100))
    total_s, cs = divmod(total_cs, 100)
    h, rem = divmod(total_s, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ts_human(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def mmss_to_seconds(value: str) -> float:
    parts = [int(p) for p in str(value).strip().split(":")]
    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2]
    if len(parts) == 2:
        return parts[0] * 60 + parts[1]
    return float(parts[0])


def slugify(text: str, maxlen: int = 48) -> str:
    """ASCII-only. A fully non-Latin title therefore slugs to "clip" — known
    limitation, fine while output names are internal."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return (slug[:maxlen] or "clip").rstrip("-")


def log(message: str) -> None:
    """Progress goes to stderr so stdout stays clean for piping."""
    print(message, file=sys.stderr)
