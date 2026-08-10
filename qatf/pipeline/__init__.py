"""The five stages. Only two are AI, and each stage is one module.

    audio.py     1. demux audio             ffmpeg
    asr.py       2. transcribe + word times faster-whisper
    fixups.py    2b. substitutions by value text only, never timestamps
    edits.py     2c. corrections by position text only, never timestamps
    select.py    3. pick highlight clips    the configured provider
    cuts.py      4. snap to word bounds     local, deterministic
    detect.py    4b. find faces             OpenCV, cached against the video
    framing.py   4c. solve the crop path    local, deterministic
    captions.py  5a. ASS generation         local
    encode.py    5b. reframe + burn         ffmpeg

CORE INVARIANT — the model never emits precise timing. It sees the transcript in
~12s blocks and returns MM:SS; `cuts.snap` moves those onto real Whisper word
times. Semantic boundaries from the model, acoustic boundaries from Whisper,
never mixed. Stages 1, 4 and 5 must stay model-free — the file layout is there
to make violating that obvious in a diff.

Front ends (`qatf.cli`, `qatf.api`) import from here and must contain no
pipeline logic of their own.
"""

from __future__ import annotations

from ..core.types import Clip, Detection, Track, Transcript, Word, track_to_dict
from . import asr, audio, captions, cuts, detect, edits, encode, fixups, framing, select
from .asr import (
    DEVICES,
    cache_path,
    compute_type_for,
    cuda_device_count,
    read_cache,
    resolve_device,
    transcribe,
    transcribe_cached,
    write_cache,
)
from .audio import DENOISE_FILTER, audio_path, extract_audio
from .captions import build_ass, group_words
from .cuts import snap, within_duration, words_in
from .detect import detections_for
from .encode import REFRAME_MODES, clip_stem, filtergraph, render, render_all
from .framing import crop_width, sanitise, solve
from .select import build_transcript_blocks, parse_response, pick_clips

__all__ = [
    "asr", "audio", "captions", "cuts", "detect", "encode", "framing", "select",
    "extract_audio", "audio_path", "DENOISE_FILTER", "fixups", "edits",
    "transcribe", "transcribe_cached", "cache_path", "read_cache", "write_cache",
    "DEVICES", "resolve_device", "cuda_device_count", "compute_type_for",
    "pick_clips", "build_transcript_blocks", "parse_response",
    "snap", "words_in", "within_duration", "plan_clips",
    "build_ass", "group_words",
    "detections_for", "solve", "crop_width", "sanitise", "track_clips",
    "render", "render_all", "filtergraph", "clip_stem", "REFRAME_MODES",
    "Clip", "Detection", "Track", "Transcript", "Word",
]


def plan_clips(words: list[Word], n: int, lo: int, hi: int,
               model: str | None = None, settings=None) -> list[Clip]:
    """Stages 3 and 4 together: propose, then snap, then drop what the snap
    pushed out of range.

    Deliberately goes through the modules rather than the re-exported names, so
    a test that patches `pipeline.select.pick_clips` is actually honoured.

    `settings` is threaded rather than read from the process — see
    `select.pick_clips`."""
    clips = select.pick_clips(words, n, lo, hi, model=model, settings=settings)
    clips = [cuts.snap(c, words) for c in clips]
    return cuts.within_duration(clips, lo, hi)


def track_clips(video, clips: list[Clip], work, src: tuple[int, int], *,
                tier: str = "balanced") -> list[Track]:
    """Stages 4b and 4c together: detect, then solve one crop path per clip.

    Same shape as `plan_clips`, and here for the same reason — the front ends
    parse input and report progress, they do not compute geometry.

    Each solved track is written to `<work>/track-NN.json`. Nothing reads those
    back yet; they exist so a framing decision is reviewable, which is the whole
    point of making the track an artifact rather than a temporary."""
    import json

    dets, detector = detect.detections_for(video, clips, work, tier=tier)
    crop_w = framing.crop_width(*src)
    tracks = []
    for index, clip in enumerate(clips, 1):
        track = framing.solve(dets, clip, crop_w, detector=detector, tier=tier)
        (work / f"track-{index:02d}.json").write_text(
            json.dumps(track_to_dict(track)), encoding="utf-8")
        tracks.append(track)
    return tracks
