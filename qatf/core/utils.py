"""Small shared helpers. No pipeline logic and no state."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
import sys

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


def ffmpeg_available() -> bool:
    try:
        check_ffmpeg()
        return True
    except FFmpegNotFound:
        return False


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
