"""Stage 5b — reframe to 9:16, burn captions, encode.

Both filtergraphs are verified to produce 1080x1920 with correct duration.
Neither tracks the subject; see "Reframing is static" in CLAUDE.md.

Performance, measured (4 clips at 1080x1920, captions burned in, 16 cores):
the encoder is the only thing worth tuning here. The filter chain is noise —
the `ass` filter is 2.1% of a render, `flags=lanczos` costs nothing over
bicubic, `+faststart` is free — and running clips concurrently returns only
1.21x, because the encoder already saturates the machine. See `PRESETS`.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core.constants import CAPTION_MAX_WORDS, TARGET_H, TARGET_W
from ..core.types import Clip, Track, Word
from ..core.utils import run, slugify
from .captions import build_ass

#: `track` is `crop` with a moving x. It is deliberately a third mode rather
#: than a change to `crop`: every number in docs/quality.md was measured against
#: the two static graphs, and a mode that did not exist cannot invalidate them.
REFRAME_MODES = ("crop", "blur", "track")

#: Named output sizes. `source` is resolved per-video from the crop region, so
#: it is not in this table.
RESOLUTIONS = {
    "1080p": (1080, 1920),      # what every platform actually delivers
    "1440p": (1440, 2560),
    "4k": (2160, 3840),
}

#: Per-codec encoder flags. H.265 needs `-tag:v hvc1` or QuickTime, Safari and
#: iOS refuse to play the file — it is the single most common HEVC mistake, and
#: the failure is silent (the file is valid, it just will not open).
CODECS = {
    "h264": {
        "encoder": "libx264",
        "profile": "high",
        "pix_fmt": "yuv420p",
        "pix_fmt_10bit": "yuv420p10le",
        "profile_10bit": "high10",
        "extra": [],
    },
    "h265": {
        "encoder": "libx265",
        "profile": "main",
        "pix_fmt": "yuv420p",
        "pix_fmt_10bit": "yuv420p10le",
        "profile_10bit": "main10",
        # hvc1 branding for Apple playback; quiet the x265 banner on every clip
        "extra": ["-tag:v", "hvc1", "-x265-params", "log-level=error"],
    },
}

#: H.265: ~40% smaller than H.264 at equal quality, and the better archival
#: choice. It costs encode time — measured 3.06x libx264 at the same preset —
#: which is why `--preset` matters more now than it did.
#:
#: YouTube accepts HEVC. Some Instagram and TikTok upload paths still prefer
#: H.264, so `--codec h264` remains one flag away.
DEFAULT_CODEC = "h265"

#: Encoder speed presets, slowest first. Both x264 and x265 take these names.
#:
#: The one real performance lever in stage 5. Measured at 1080x1920:
#:
#:      preset      h264     h265    h265/h264
#:      medium     2.67s    8.18s      3.06x
#:      fast       2.42s    5.71s      2.36x
#:      faster     2.01s    5.13s      2.55x
#:      veryfast   1.57s    5.09s      3.25x
#:
#: So `veryfast` is 1.61x faster than `medium` on h265 — the lever is worth
#: more now that h265 is the default.
PRESETS = ("veryslow", "slower", "slow", "medium", "fast", "faster",
           "veryfast", "superfast", "ultrafast")

#: `medium` stays the default deliberately: a clip is rendered once and watched
#: many times, so the default should not trade quality for minutes. Reach for a
#: faster preset while iterating on framing, fonts or captions.
DEFAULT_PRESET = "medium"


def parse_resolution(value: str) -> tuple[int, int] | None:
    """`1080p` / `4k` / `1216x2160` -> (w, h). `source` -> None (resolve later)."""
    key = value.strip().lower()
    if key == "source":
        return None
    if key in RESOLUTIONS:
        return RESOLUTIONS[key]
    if "x" in key:
        w, _, h = key.partition("x")
        try:
            return even(int(w)), even(int(h))
        except ValueError:
            pass
    raise ValueError(
        f"unknown resolution {value!r}. Use source, "
        f"{', '.join(RESOLUTIONS)}, or WxH like 1216x2160"
    )


def even(n: int) -> int:
    """H.264/H.265 with 4:2:0 chroma require even dimensions."""
    return n - (n % 2)


def native_size(src_w: int, src_h: int, mode: str) -> tuple[int, int]:
    """The 9:16 output size that resamples the source as little as possible.

    For `crop` that is the crop region itself — a 3840x2160 source yields a
    1215x2160 slice, so 1216x2160 (rounded to even) is every source pixel with
    no scaling at all. For `blur` the constraint is the full frame width, which
    has to fit inside 9/16 of the height, so height drives the size.

    `track` takes the same window as `crop` and only moves it, so it takes the
    same size. Falling through to the `blur` branch here would have asked for a
    3840x6826 output on a 4K source."""
    if mode in ("crop", "track"):
        return even(min(src_w, round(src_h * 9 / 16))), even(src_h)
    # blur: the source must fit within the width, so the frame is as tall as
    # 16/9 of that width
    return even(src_w), even(round(src_w * 16 / 9))


def crop_x_px(cx: float, src_w: int, src_h: int) -> int:
    """Normalised subject centre -> the crop filter's left edge, in pixels.

    `crop`'s x is an edge, not a centre, so the half-width comes off here. The
    result is clamped to the frame and forced even: at 4:2:0 an odd offset
    lands the luma and chroma planes half a chroma sample apart, which shows up
    as colour shimmering along vertical edges while the window pans."""
    out_w = even(min(src_w, round(src_h * 9 / 16)))
    x = int(round(cx * src_w - out_w / 2))
    x = max(0, min(x, src_w - out_w))
    return x - (x % 2)


def write_sendcmd(track: Track, path: Path, src_w: int, src_h: int) -> Path:
    """Write the crop path as an ffmpeg sendcmd script.

    One command per keyframe; sendcmd holds the previous value until the next
    one, so the gaps a shot change leaves are correct rather than missing.

    The values are integers this file computed — nothing from the track reaches
    the script as text — so a hand-edited track cannot inject sendcmd syntax.
    The escaping that does matter is on the *path*, in `filtergraph`."""
    lines = [f"{k.t:.3f} crop x {crop_x_px(k.cx, src_w, src_h)};"
             for k in track.keyframes]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _escape_path(path: Path) -> str:
    """Escape a path for use inside a single-quoted filter argument.

    **libavfilter tokenizes filter arguments twice** — once in the graph parser,
    then again in `av_opt_set_from_string` on the filter's own options — and an
    escape has to survive both passes. That is why the two characters here are
    treated differently, and the asymmetry is not a typo:

      ':'  ->  '\\:'      one backslash. Inside single quotes it is literal at
                          level 1 and consumed as an escape at level 2.
      "'"  ->  "'\\\\\\''"  the close-escape-reopen idiom, with the backslash
                          itself escaped so a backslash survives level 1 and is
                          still there to protect the quote at level 2.

    Writing the shell idiom `'\\''` — correct for one level — passes the graph
    parser and is then re-read as a quote by the second pass, which DELETES the
    apostrophe: `-o "Ahmed's clips/"` became `Ahmeds clips/`, libass failed to
    open the file, and every clip died at stage 5 with exit 234. Verified
    against ffmpeg 7.1.1 in both directions.

    Used by the `ass` filter and by `sendcmd`, so a regression here breaks
    captions and subject tracking together."""
    return (str(path).replace("\\", "/")
            .replace("'", r"'\\\''")
            .replace(":", r"\:"))


def filtergraph(mode: str, ass_path: Path | None,
                width: int = TARGET_W, height: int = TARGET_H,
                cmd_path: Path | None = None, x0: int | None = None) -> str:
    """9:16 reframe. 'crop' for centred talking heads, 'blur' when the subject
    moves or the framing is wide, 'track' to follow the speaker.

    `blur` measured 1.8x the render time of `crop` — `gblur=sigma=32` over a
    full frame is the expense — for a result that also carries ~3x fewer subject
    pixels. It is slower AND softer; pick it only when the framing needs it.

    `track` takes the crop window from stage 4c. Two shapes: with `cmd_path` it
    is driven per-frame by sendcmd; with `x0` alone it is a static crop at a
    measured offset, which is what a single-keyframe track means and costs
    exactly what `crop` costs."""
    if mode == "crop":
        base = (
            f"[0:v]crop=w='min(iw,ih*9/16)':h=ih:x='(iw-out_w)/2':y=0,"
            f"scale={width}:{height}:flags=lanczos,setsar=1[v0]"
        )
    elif mode == "track":
        if cmd_path is None and x0 is None:
            raise ValueError("track mode needs cmd_path or x0")
        # x0 seeds the window so frame 0 is already framed; without it the first
        # frame renders at x=0 until the first command lands.
        start_x = x0 if x0 is not None else 0
        prefix = f"sendcmd=f='{_escape_path(cmd_path)}'," if cmd_path else ""
        base = (
            f"[0:v]{prefix}crop=w='min(iw,ih*9/16)':h=ih:x={start_x}:y=0,"
            f"scale={width}:{height}:flags=lanczos,setsar=1[v0]"
        )
    elif mode == "blur":
        base = (
            "[0:v]split=2[bg][fg];"
            f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},gblur=sigma=32[bgb];"
            f"[fg]scale={width}:-2:flags=lanczos[fgs];"
            "[bgb][fgs]overlay=(W-w)/2:(H-h)/2,setsar=1[v0]"
        )
    else:
        raise ValueError(f"unknown reframe mode: {mode!r}, expected one of {REFRAME_MODES}")

    if ass_path is None:
        return base + ";[v0]null[v]"
    return base + f";[v0]ass='{_escape_path(ass_path)}'[v]"


def render(video: Path, clip: Clip, ass_path: Path | None,
           out_path: Path, mode: str, crf: int = 20,
           fps: float | None = None,
           width: int = TARGET_W, height: int = TARGET_H,
           codec: str = DEFAULT_CODEC, ten_bit: bool = False,
           preset: str = DEFAULT_PRESET,
           cmd_path: Path | None = None, x0: int | None = None) -> Path:
    """Encode one clip.

    `preset` is the encoder speed/quality trade and the only knob that
    measurably moves render time — see PRESETS. It matters more since h265
    became the default, because libx265 is ~3x libx264 at the same preset.

    `fps=None` preserves the source frame rate, which is the right default.
    Forcing a round 30 on NTSC-rate footage (30000/1001 = 29.97) makes ffmpeg
    duplicate roughly one frame every 33 seconds — invisible in a still, a
    periodic micro-stutter in motion. This used to be hardcoded.

    `ten_bit` keeps 10-bit precision end to end. Worth it from a 10-bit source
    (ProRes is 4:2:2 10-bit): the extra headroom suppresses banding in gradients
    like sky and skin, even though the output chroma is still 4:2:0."""
    if codec not in CODECS:
        raise ValueError(f"unknown codec {codec!r}, expected one of {tuple(CODECS)}")
    if preset not in PRESETS:
        raise ValueError(f"unknown preset {preset!r}, expected one of {PRESETS}")
    spec = CODECS[codec]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        # -ss BEFORE -i is a fast seek, and resets timestamps to 0 — which is
        # why ASS cue times are written relative to the clip start.
        "-ss", f"{clip.start:.3f}",
        "-i", str(video),
        "-t", f"{clip.duration:.3f}",
        "-filter_complex", filtergraph(mode, ass_path, width, height,
                                       cmd_path=cmd_path, x0=x0),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", spec["encoder"], "-preset", preset, "-crf", str(crf),
        "-pix_fmt", spec["pix_fmt_10bit"] if ten_bit else spec["pix_fmt"],
        "-profile:v", spec["profile_10bit"] if ten_bit else spec["profile"],
        *spec["extra"],
    ]
    if fps:
        cmd += ["-r", str(fps)]
    cmd += [
        "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
        "-movflags", "+faststart",
        str(out_path),
    ]
    run(cmd)
    return out_path


def clip_stem(index: int, clip: Clip) -> str:
    return f"{index:02d}-{slugify(clip.title)}"


def _track_args(track: Track | None, cmd_path: Path,
                src: tuple[int, int]) -> tuple[Path | None, int]:
    """(sendcmd file, initial x) for one clip.

    A track with one keyframe or none needs no sendcmd file: a window that never
    moves is a static crop, and rendering it as one costs exactly what `crop`
    costs. That is the common case whenever the detector found nothing, so the
    fallback path is also the cheap path."""
    if track is None or not track.keyframes:
        return None, crop_x_px(0.5, *src)
    x0 = crop_x_px(track.keyframes[0].cx, *src)
    if len(track.keyframes) == 1:
        return None, x0
    return write_sendcmd(track, cmd_path, *src), x0


def render_all(video: Path, clips: list[Clip], words: list[Word], out_dir: Path,
               work: Path, *, mode: str = "crop", font: str = "Arial",
               captions: bool = True, per_line: int = CAPTION_MAX_WORDS,
               crf: int = 20, fps: float | None = None,
               width: int = TARGET_W, height: int = TARGET_H,
               codec: str = DEFAULT_CODEC, ten_bit: bool = False,
               preset: str = DEFAULT_PRESET,
               tracks: list[Track] | None = None,
               src: tuple[int, int] | None = None,
               on_clip: Callable[[int, int, Clip, Path], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> list[Path]:
    """Render a whole plan.

    Sequential on purpose. Rendering clips concurrently measured 1.21x on 16
    cores — the encoder already saturates them — which does not pay for a pool
    that would fight QATF_WORKERS, GPU memory, and the cancel checkpoint below.

    `should_stop` is checked between clips only — it cannot interrupt an ffmpeg
    call already in flight.

    `tracks` is one solved Track per clip, positionally. It is only read in
    `track` mode, and a missing or empty one degrades to a centre crop for that
    clip alone — one clip where no face was found must not fail the batch."""
    if mode == "track" and src is None:
        raise ValueError("track mode needs the source dimensions as src=(w, h)")
    outputs: list[Path] = []
    total = len(clips)
    for i, clip in enumerate(clips, 1):
        if should_stop and should_stop():
            break
        stem = clip_stem(i, clip)
        ass = (build_ass(clip, words, work / f"{stem}.ass", per_line=per_line, font=font)
               if captions else None)
        cmd_path, x0 = (_track_args(tracks[i - 1] if tracks and i <= len(tracks) else None,
                                    work / f"{stem}.cmd", src)
                        if mode == "track" else (None, None))
        out = render(video, clip, ass, out_dir / f"{stem}.mp4", mode,
                     crf=crf, fps=fps, width=width, height=height,
                     codec=codec, ten_bit=ten_bit, preset=preset,
                     cmd_path=cmd_path, x0=x0)
        outputs.append(out)
        if on_clip:
            on_clip(i, total, clip, out)
    return outputs
