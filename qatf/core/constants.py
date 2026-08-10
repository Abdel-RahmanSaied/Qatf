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

# --- Subject tracking (stages 4b/4c) ---------------------------------------
#
# EVERY NUMBER BELOW IS AN UNMEASURED STARTING VALUE. They are here because they
# are product decisions, not deployment ones, but none of them has been scored
# against real footage yet. Do not copy them into docs/quality.md until they
# have been — that file is for numbers somebody measured.

#: Cost tiers. Named, never probed, and never silently downgraded: asking for
#: `best` on a CPU-only host runs it slowly and says so. Same contract as
#: `--device cuda`, and for the same reason — a quiet downgrade turns a
#: comparison into a lie.
TRACK_TIERS = ("fast", "balanced", "best")
DEFAULT_TRACK_TIER = "balanced"

#: How often stage 4b looks at a frame, per tier. This is the whole cost knob:
#: detection time is linear in it.
TRACK_SAMPLE_FPS = {"fast": 1.0, "balanced": 3.0, "best": 8.0}

#: How often stage 4c emits a crop command. Fixed rather than matched to the
#: source rate: at 30/s a step is at most TRACK_MAX_PAN/30 of frame width, which
#: is below what an eye resolves as a jump, and it saves threading the probed
#: frame rate through two more call sites.
TRACK_EMIT_FPS = 30.0

#: Both of the next two are fractions of the *visible* frame — the crop window —
#: not of source width, and they are scaled by the crop width where they are
#: used. That distinction is not pedantic: on a 16:9 source the crop is 0.316 of
#: source width, so a deadzone expressed in source units lands 3.2x wider on
#: screen than it reads. Measured at the first attempt, where a nominal 0.05
#: deadzone let the subject sit 16% off-centre.

#: Ceiling on how fast the crop window may travel, in visible frame widths per
#: second. The limit exists because the detector is noisy and an unclamped path
#: whips across the frame on a single bad sample.
TRACK_MAX_PAN = 0.5

#: How far the subject may drift from the crop centre before the window moves at
#: all. Without it the frame breathes constantly on a stationary talking head —
#: a real camera operator holds still, and so should this.
TRACK_DEADZONE = 0.08

#: A jump larger than this between adjacent samples is read as a shot change,
#: not as motion. Smoothing across a hard cut invents a pan that is not in the
#: source, so the path re-anchors instead.
TRACK_SHOT_JUMP = 0.25

#: Above this, the active-speaker model decides who to frame; below it, `framing`
#: falls back to picking the largest face. The `fast` tier reports 0.0 for every
#: detection, so it always takes the fallback path.
TRACK_SPEAKING_MIN = 0.5

#: Width of the centred moving average, in seconds.
TRACK_SMOOTH_SECONDS = 0.8

#: Hard ceiling on keyframes in one track. At TRACK_EMIT_FPS this is ~11 minutes
#: of clip, well past any short. It is here for the same reason the plan has a
#: size limit: a track arrives over PUT, and an unbounded one is a denial of
#: service dressed as a hand edit.
TRACK_MAX_KEYFRAMES = 20_000

VIDEO_SUFFIXES = frozenset({
    ".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".mp3", ".wav", ".m4a",
})
