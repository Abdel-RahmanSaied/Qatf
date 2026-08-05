"""Foundation layer. Everything else depends on this; it depends on nothing.

    config.py     deployment settings read from the environment
    constants.py  product decisions (9:16, caption budget, snap margins)
    errors.py     the QatfError hierarchy
    types.py      Word, Transcript, Clip
    utils.py      subprocess, timestamps, slugs, logging

The dependency direction is one-way and worth keeping that way:

    api -> jobs -> pipeline -> core

Nothing in here may import from `pipeline`, `jobs` or `api`, and nothing here
knows what HTTP is.
"""

from __future__ import annotations

from .config import DEFAULT_MODEL, Settings, get_settings
from .constants import (
    BLOCK_SECONDS,
    CAPTION_MAX_CHARS,
    CAPTION_MAX_WORDS,
    SNAP_LEAD,
    SNAP_TAIL,
    TARGET_H,
    TARGET_W,
    VIDEO_SUFFIXES,
)
from .errors import QatfError
from .types import Clip, Transcript, Word, clips_from_dicts, clips_to_dicts
from .utils import check_ffmpeg, ffmpeg_available, log, run, slugify, ts_ass, ts_human

__all__ = [
    "Settings", "get_settings", "DEFAULT_MODEL",
    "TARGET_W", "TARGET_H", "BLOCK_SECONDS", "CAPTION_MAX_WORDS", "CAPTION_MAX_CHARS",
    "SNAP_LEAD", "SNAP_TAIL", "VIDEO_SUFFIXES",
    "QatfError",
    "Word", "Transcript", "Clip", "clips_to_dicts", "clips_from_dicts",
    "run", "check_ffmpeg", "ffmpeg_available", "ts_ass", "ts_human", "slugify", "log",
]
