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
#: Default caption typeface.
#:
#: A product decision, so it lives here and NOT as a literal in each caller —
#: it was written in six places (both front ends, `build_ass`, `encode.render`,
#: `safe_font`'s fallback and the web UI) and changing four of them left the
#: other two rendering Arial, which is the exact drift a shared constant exists
#: to stop. The web UI keeps its own copy because it cannot import Python; that
#: one is a declared mirror.
#:
#: Noto Sans Arabic rather than Arial: it is present in the render image
#: (`fc-match` resolves it exactly, no fallback) and carries Latin as well as
#: Arabic, so one default serves both scripts. Arial has no Arabic coverage, so
#: it reached libass and came back as whatever fontconfig chose — silently.
DEFAULT_FONT = "Noto Sans Arabic"

#: Caption line budget. Both are enforced — word count alone overflows the frame
#: on long words.
#:
#: The character budget is a FUNCTION OF THE FONT SIZE and has to move with it.
#: Usable width is 1080 minus the two 90px margins = 900px; average advance is
#: roughly half the em, so 82px fitted ~22 characters and 64px fits ~28. When
#: `captions.FONT_SIZE` changes, recompute this or lines will either overflow
#: the frame or waste half of it.
#:
#: Wider lines are also calmer lines: at 22 characters a real clip turned over
#: 35 caption lines in 46 seconds, about one every 1.3s. Short-form editors
#: settle around 32-42 characters for that reason.
CAPTION_MAX_WORDS = 5
CAPTION_MAX_CHARS = 28

# How far outside the chosen word boundary a cut opens and closes.
SNAP_LEAD = 0.15
SNAP_TAIL = 0.35

#: The tail to use when a word's `end` is an upper bound rather than a
#: measurement — see `Transcript.timing_source`.
#:
#: Zero, and the zero is the whole point. A caption track gives one instant per
#: token, so the only honest end for word N is "no later than the start of word
#: N+1". Extending past that does not run into silence, it runs into the next
#: word: `SNAP_TAIL` would put the cut 0.35s after the following word has
#: already begun, slicing its first phoneme off. Cutting AT the bound lands in
#: the gap, which is exactly where a cut belongs.
#:
#: Keeping it a named constant rather than writing `0.0` at the call site is
#: deliberate: it is a product decision about what a bounded timing means, and
#: the next person to see a bare 0.0 would "fix" it back to SNAP_TAIL.
SNAP_TAIL_BOUNDED = 0.0

#: Hosts a caller-supplied URL may name. A SECURITY BOUNDARY, not a
#: convenience list — see `pipeline.fetch.validate_url`.
#:
#: yt-dlp accepts `file://` and ships extractors for a thousand sites, so an
#: unvalidated URL out of a `POST` body is a local-file read and an
#: outbound-request primitive in one field. This is the URL twin of
#: `media_root`: name what is allowed, refuse the rest.
#:
#: Deliberately only YouTube. Widening it is a decision someone should have to
#: make on purpose, in a diff, with this comment in front of them.
YOUTUBE_HOSTS = frozenset({
    "youtube.com", "www.youtube.com", "m.youtube.com",
    "music.youtube.com", "youtu.be", "www.youtu.be",
})

#: The shape a caller-supplied language tag must have. A BOUNDARY, and it
#: guards two different sinks that would each be happy with different garbage:
#:
#: - the transcript cache FILENAME, where `../../x` escapes the work directory
#: - yt-dlp's `subtitleslangs`, where every entry is fullmatched as a REGEX, so
#:   `(` crashes stage 0 outright and `.*` silently matches EVERY caption track
#:   — handing stage 2' a machine translation in place of the original ASR
#:
#: Letters, digits and hyphens only, so nothing here is a metacharacter and
#: nothing is a path separator. It lives in `core` because both front ends must
#: agree: `api/schemas.py` states it on the wire, `pipeline/fetch.py` enforces
#: it where the risk actually is, and the CLI gets the check for free. Copied
#: into a second place it is a rule that will disagree with itself.
LANGUAGE_TAG_PATTERN = r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$"

#: How far outside [min_len, max_len] a clip may still land, in SECONDS.
#:
#: Absolute rather than proportional, because what stage 4 adds is absolute:
#: SNAP_LEAD + SNAP_TAIL (0.5s) plus at most a word of boundary movement. The
#: old `hi * 1.4` admitted a 72.8s clip for `--max-len 52`, which defeats the
#: only reason anyone passes 52 — landing under the 60s Shorts ceiling once
#: snapping has had its say. Anything beyond this is the model overrunning its
#: instruction, not snapping drifting.
DURATION_SLACK = 2.0

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

#: Shot-change detection, in two parts because one number cannot do it.
#:
#: A jump is read as a cut when it clears BOTH of these. The distance floor
#: alone was the original rule, and it is wrong the moment the sample interval
#: moves: the tiers sample 8x apart, so 0.25-of-frame-width between adjacent
#: samples is a plausible cut at `balanced` (0.33s apart), ordinary walking
#: speed at `fast` (1s apart) and hypersensitive at `best` (0.125s apart). A
#: presenter crossing the frame would have been re-anchored on every sample at
#: `fast` — a step-per-second stutter, which is precisely what TRACK_MAX_PAN
#: and the deadzone exist to prevent, and the re-anchor path consults neither.
#:
#: So the speed term carries the tier dependence and the floor stops jitter at a
#: short interval reading as a cut. At `balanced` the two agree at 0.25, which
#: is why this is a generalisation of the old rule rather than a new one.
#:
#: The `fast` tier consequently cannot see a cut that moves the subject less
#: than 0.75 of frame width, because at one sample per second that is
#: indistinguishable from someone running. It smooths across those instead. That
#: is the deliberate direction to be wrong in: a missed cut costs one bounded
#: pan, a false one costs a whip on every walking step.
TRACK_SHOT_JUMP = 0.25          # floor: never a cut below this, whatever the gap
TRACK_SHOT_SPEED = 0.75         # frame widths per second no subject moves at

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
