"""The five stages. Only two are AI, and each stage is one module.

    audio.py     1. demux audio             ffmpeg
    asr.py       2. transcribe + word times faster-whisper
    select.py    3. pick highlight clips    Claude
    cuts.py      4. snap to word bounds     local, deterministic
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

from ..core.types import Clip, Transcript, Word
from . import asr, audio, captions, cuts, encode, fixups, select
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
from .encode import REFRAME_MODES, clip_stem, filtergraph, render, render_all
from .select import build_transcript_blocks, parse_response, pick_clips

__all__ = [
    "asr", "audio", "captions", "cuts", "encode", "select",
    "extract_audio", "audio_path", "DENOISE_FILTER", "fixups",
    "transcribe", "transcribe_cached", "cache_path", "read_cache", "write_cache",
    "DEVICES", "resolve_device", "cuda_device_count", "compute_type_for",
    "pick_clips", "build_transcript_blocks", "parse_response",
    "snap", "words_in", "within_duration", "plan_clips",
    "build_ass", "group_words",
    "render", "render_all", "filtergraph", "clip_stem", "REFRAME_MODES",
    "Clip", "Transcript", "Word",
]


def plan_clips(words: list[Word], n: int, lo: int, hi: int,
               model: str | None = None) -> list[Clip]:
    """Stages 3 and 4 together: propose, then snap, then drop what the snap
    pushed out of range.

    Deliberately goes through the modules rather than the re-exported names, so
    a test that patches `pipeline.select.pick_clips` is actually honoured."""
    clips = select.pick_clips(words, n, lo, hi, model=model)
    clips = [cuts.snap(c, words) for c in clips]
    return cuts.within_duration(clips, lo, hi)
