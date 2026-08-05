"""Stage 5b — reframe to 9:16, burn captions, encode.

Both filtergraphs are verified to produce 1080x1920 with correct duration.
Neither tracks the subject; see "Reframing is static" in CLAUDE.md.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..core.constants import CAPTION_MAX_WORDS, TARGET_H, TARGET_W
from ..core.types import Clip, Word
from ..core.utils import run, slugify
from .captions import build_ass

REFRAME_MODES = ("crop", "blur")

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
    has to fit inside 9/16 of the height, so height drives the size."""
    if mode == "crop":
        return even(min(src_w, round(src_h * 9 / 16))), even(src_h)
    # blur: the source must fit within the width, so the frame is as tall as
    # 16/9 of that width
    return even(src_w), even(round(src_w * 16 / 9))


def filtergraph(mode: str, ass_path: Path | None,
                width: int = TARGET_W, height: int = TARGET_H) -> str:
    """9:16 reframe. 'crop' for centred talking heads, 'blur' when the subject
    moves or the framing is wide."""
    if mode == "crop":
        base = (
            f"[0:v]crop=w='min(iw,ih*9/16)':h=ih:x='(iw-out_w)/2':y=0,"
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

    # ffmpeg filter args need ':' and '\' escaped inside the graph
    escaped = str(ass_path).replace("\\", "/").replace(":", r"\:")
    return base + f";[v0]ass='{escaped}'[v]"


def render(video: Path, clip: Clip, ass_path: Path | None,
           out_path: Path, mode: str, crf: int = 20,
           fps: float | None = None,
           width: int = TARGET_W, height: int = TARGET_H,
           codec: str = "h264", ten_bit: bool = False) -> Path:
    """Encode one clip.

    `fps=None` preserves the source frame rate, which is the right default.
    Forcing a round 30 on NTSC-rate footage (30000/1001 = 29.97) makes ffmpeg
    duplicate roughly one frame every 33 seconds — invisible in a still, a
    periodic micro-stutter in motion. This used to be hardcoded.

    `ten_bit` keeps 10-bit precision end to end. Worth it from a 10-bit source
    (ProRes is 4:2:2 10-bit): the extra headroom suppresses banding in gradients
    like sky and skin, even though the output chroma is still 4:2:0."""
    if codec not in CODECS:
        raise ValueError(f"unknown codec {codec!r}, expected one of {tuple(CODECS)}")
    spec = CODECS[codec]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        # -ss BEFORE -i is a fast seek, and resets timestamps to 0 — which is
        # why ASS cue times are written relative to the clip start.
        "-ss", f"{clip.start:.3f}",
        "-i", str(video),
        "-t", f"{clip.duration:.3f}",
        "-filter_complex", filtergraph(mode, ass_path, width, height),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", spec["encoder"], "-preset", "medium", "-crf", str(crf),
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


def render_all(video: Path, clips: list[Clip], words: list[Word], out_dir: Path,
               work: Path, *, mode: str = "crop", font: str = "Arial",
               captions: bool = True, per_line: int = CAPTION_MAX_WORDS,
               crf: int = 20, fps: float | None = None,
               width: int = TARGET_W, height: int = TARGET_H,
               codec: str = "h264", ten_bit: bool = False,
               on_clip: Callable[[int, int, Clip, Path], None] | None = None,
               should_stop: Callable[[], bool] | None = None) -> list[Path]:
    """Render a whole plan.

    `should_stop` is checked between clips only — it cannot interrupt an ffmpeg
    call already in flight."""
    outputs: list[Path] = []
    total = len(clips)
    for i, clip in enumerate(clips, 1):
        if should_stop and should_stop():
            break
        stem = clip_stem(i, clip)
        ass = (build_ass(clip, words, work / f"{stem}.ass", per_line=per_line, font=font)
               if captions else None)
        out = render(video, clip, ass, out_dir / f"{stem}.mp4", mode,
                     crf=crf, fps=fps, width=width, height=height,
                     codec=codec, ten_bit=ten_bit)
        outputs.append(out)
        if on_clip:
            on_clip(i, total, clip, out)
    return outputs
