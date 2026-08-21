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
from . import (
    asr,
    audio,
    captions,
    cuts,
    detect,
    edits,
    encode,
    fetch,
    fixups,
    framing,
    health,
    select,
    subs,
)
from .asr import (
    DEVICES,
    cache_key,
    cache_path,
    compute_type_for,
    cuda_device_count,
    db_path,
    read_cache,
    resolve_device,
    transcribe,
    transcribe_cached,
    write_cache,
)
from .audio import DENOISE_FILTER, audio_path, extract_audio
from .captions import build_ass, font_available, font_warning, group_words
from .cuts import classify_duration, report_durations, snap, tail_for, words_in
from .detect import detections_for
from .encode import REFRAME_MODES, clip_stem, filtergraph, render, render_all
from .fetch import Fetched, is_url, validate_url
from .framing import crop_width, sanitise, solve
from .select import build_transcript_blocks, parse_response, pick_clips
from .subs import has_word_timings, to_transcript

__all__ = [
    "asr", "audio", "captions", "cuts", "detect", "encode", "framing", "select",
    "extract_audio", "audio_path", "DENOISE_FILTER", "fixups", "edits", "health",
    "transcribe", "transcribe_cached", "cache_key", "cache_path", "db_path",
    "read_cache", "write_cache",
    "DEVICES", "resolve_device", "cuda_device_count", "compute_type_for",
    "pick_clips", "build_transcript_blocks", "parse_response",
    "snap", "words_in", "classify_duration", "report_durations", "plan_clips",
    "tail_for",
    "fetch", "subs", "Fetched", "validate_url", "is_url",
    "has_word_timings", "to_transcript",
    "build_ass", "group_words", "font_available", "font_warning",
    "detections_for", "solve", "crop_width", "sanitise", "track_clips",
    "render", "render_all", "filtergraph", "clip_stem", "REFRAME_MODES",
    "Clip", "Detection", "Track", "Transcript", "Word",
]


def plan_clips(words: list[Word], n: int, lo: int, hi: int,
               model: str | None = None, settings=None,
               timing_source: str | None = None) -> list[Clip]:
    """Stages 3 and 4 together: propose, then snap. Returns the WHOLE plan.

    Nothing is dropped for length any more. `cuts.report_durations` logs the
    clips that missed the requested range and they stay in the plan, flagged,
    for the operator to judge — see `cuts.classify_duration` for why that
    reversed. A four-second miss is not worth discarding a clip nobody has
    looked at.

    Deliberately goes through the modules rather than the re-exported names, so
    a test that patches `pipeline.select.pick_clips` is actually honoured.

    `settings` is threaded rather than read from the process — see
    `select.pick_clips`.

    `timing_source` decides the snap tail and must be the transcript's own —
    see `cuts.tail_for`. It is threaded rather than inferred because the same
    decision has to be reproducible in `PUT /jobs/{id}/plan`, which re-snaps
    over this function's output; a round trip that used a different tail than
    the first pass would move the boundaries on every edit."""
    clips = select.pick_clips(words, n, lo, hi, model=model, settings=settings)
    tail = cuts.tail_for(timing_source)
    clips = [cuts.snap(c, words, tail=tail) for c in clips]
    cuts.report_durations(clips, lo, hi)
    return clips


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
