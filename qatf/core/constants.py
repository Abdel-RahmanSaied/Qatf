"""Fixed values that are part of the product, not the deployment.

Anything here is a decision. Anything tunable per-host lives in `qatf.config`.
"""

from __future__ import annotations

# 9:16, the only aspect ratio this tool exists to produce.
TARGET_W = 1080
TARGET_H = 1920

# The model sees the transcript at this resolution and no finer. See the core
# invariant in CLAUDE.md — coarse blocks are what stop it inventing timings.
BLOCK_SECONDS = 12.0

# Caption budget. Both limits are enforced; word count alone overflows the frame
# on long words (4 x 12-char words at 82px is wider than 1080px).
CAPTION_MAX_WORDS = 4
CAPTION_MAX_CHARS = 22

# How far outside the chosen word boundary a cut opens and closes.
SNAP_LEAD = 0.15
SNAP_TAIL = 0.35

VIDEO_SUFFIXES = frozenset({
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".wav", ".m4a",
})
