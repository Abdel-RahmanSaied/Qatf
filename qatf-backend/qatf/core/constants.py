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

#: The `youtube` (pill) style's own face, used when a job leaves `font` unset.
#: Chosen 2026-10-16 by looking at real frames of a 4K Arabic job in seven
#: faces (Noto Sans/Kufi/Naskh Arabic, Tajawal, Almarai, IBM Plex Sans Arabic,
#: Lalezar) at the same size. NOT the `pop` default: every `pop` number in
#: CLAUDE.md, `CAPTION_MAX_CHARS` included, was measured on DEFAULT_FONT.
#:
#: Tajawal is not in Debian's font packages, so the Bold file ships in the repo
#: (`qatf-backend/fonts/`, SIL OFL 1.1) and the Dockerfile installs it. Only
#: Bold: the Style line draws Bold, and the measurer asks fontconfig for it.
#: Its measured widths matched libass's to 0.5px on the words checked.
PILL_FONT = "Tajawal"

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

#: Caption styles. Every one but `pop` is a word-positioned look in
#: CAPTION_LOOKS (below) — each word placed absolutely, the spoken one marked.
#: `pop` is the original single-line style and remains the fallback whenever
#: measurement is unavailable. Must list exactly CAPTION_LOOKS' keys + `pop`;
#: a check pins that, and the API schema's Literal repeats it by hand.
CAPTION_STYLES = ("box", "pill", "marker", "youtube", "pop")
DEFAULT_CAPTION_STYLE = "box"

#: Pill fill. A deepened saffron, and the depth is measured rather than chosen:
#: white on the product's existing highlight yellow (#E8A317) is 2.17:1, below
#: even the 3:1 large-text floor, which is why a saffron pill would have to keep
#: a black outline to stay legible. Outline-on-pill is the muddy combination,
#: and worse on Arabic than Latin — the caption stroke was already cut from 7px
#: to 4px because a heavy outline thickens Naskh's connected joins.
#:
#: #B4560A measures 4.91:1 against white, clears 4.5:1, and lets the active word
#: drop its outline entirely. #A34708 measures 6.07:1 and was rejected for
#: reading as its own colour rather than the product's accent.
PILL_FILL = "#B4560A"

#: The pill style's caption size, on the 1080x1920 ASS canvas. `pop` keeps
#: `captions.FONT_SIZE` (64).
#:
#: ASS Fontsize is not the em: libass fits the face's whole line height into
#: it (see `textlayout._libass_height`), so 64 drew Arabic letters only ~30px
#: tall, about a third of the frame width for a five-word line. 144 was picked
#: from real frames at 64 / 96 / 120 / 144. In Tajawal it gives about 3 words
#: per line, so lines turn over faster than they did at 64.
PILL_FONT_SIZE = 144

#: Outline around the letters on the pill style. 0 = none, chosen from real
#: frames at 0 / 2 / 4px: the drop shadow alone kept white Tajawal readable
#: over a white striped shirt. If footage turns out brighter than that, 2 is
#: the fallback that was previewed. `pop` keeps `captions.OUTLINE`.
PILL_OUTLINE = 0

#: Padding between the word's box and the capsule edge. Scaled with the size
#: (18/8 at 64), so the capsule keeps its proportions.
#:
#: PILL_PAD_X must stay above PILL_OUTLINE: the word on the layer beneath keeps
#: its outline, and the capsule is what paints over it, so padding at or under
#: the outline leaves a dark fringe around the active word. A check pins this.
PILL_PAD_X = 40
PILL_PAD_Y = 18

#: Space between words on a pill line, in canvas px. Must exceed PILL_PAD_X or
#: the active capsule covers the start of its neighbour (it did, once the font
#: scale was fixed). PAD + 12 = 52 was picked from real frames against 64 and
#: 80; at 52 the pill sits close to its neighbours without touching them.
PILL_WORD_GAP = PILL_PAD_X + 12

#: Opacity of a word that is not currently being spoken, as an ASS alpha byte.
#: ASS alpha is INVERTED — 0x00 is opaque, 0xFF is transparent — so 45% opacity
#: would be 0x8C, not 0x73.
#:
#: 0x00 = off: every word fully white and only the pill marks the spoken one,
#: chosen from real frames against dimming at 45%. When nonzero it applies to
#: the FILL ONLY (`\1a`), never the outline: a 45%-opaque dark outline on a
#: white shirt went the same grey as the fill and the word dissolved.
CAPTION_DIM_ALPHA = 0x00

#: Side margin used when solving a caption line, matching MarginL/MarginR on the
#: Style line. Usable width is TARGET_W - 2 * this = 900px at 1080 wide.
CAPTION_SIDE_MARGIN = 90

#: Bezier control-point offset for a quarter circle, as a fraction of the
#: radius. The standard circle-from-beziers constant; ASS has no rounded-rect
#: primitive, so the capsule is a hand-built path.
CAPSULE_KAPPA = 0.5523


# ---- caption looks --------------------------------------------------------
# A look is the whole recipe for one word-positioned caption style: size,
# words per line, where the line sits, stroke, shadow, glow, how the spoken
# word is marked, and how a line enters. `captions.build_ass_look` renders any
# of them through the same layout (`textlayout`), so the pill-fit rules — gap
# clears the highlight, capsule sized from ink, measured at the size drawn —
# hold for every look rather than being re-learned per style. `pop` is not a
# look: it is the original one-cue-per-line path and keeps its own constants.

from dataclasses import dataclass  # noqa: E402


@dataclass(frozen=True)
class CaptionLook:
    #: ASS Fontsize on the 1080x1920 canvas — the face's whole line height,
    #: not its em (see `textlayout._libass_height`).
    size: int
    #: most words on one line; the job's `per_line` can only lower it.
    max_words: int
    #: px between words. Must clear the highlight's overhang (pill padding,
    #: or the growth of a popped word) or the highlight covers a neighbour.
    gap: int
    #: line centre as a fraction of frame height. None = the original pill
    #: placement (MarginV 300), kept so that look renders byte-identically.
    y: float | None = None
    text: str = "#FFFFFF"
    outline: int = 0
    outline_color: str = "#101010"
    shadow: int = 3
    #: ASS alpha of the shadow (0x00 opaque .. 0xFF clear).
    shadow_alpha: int = 0x00
    #: \blur on every text event; with an outline it softens it into a glow.
    blur: float = 0.0
    #: fill alpha of words not being spoken (0x00 = no dimming).
    dim_alpha: int = 0x00
    #: how the spoken word is marked: "pill" (filled capsule), "marker"
    #: (squarer highlighter box), "color" (the word recoloured) or "none".
    highlight: str = "pill"
    hi_text: str = "#FFFFFF"
    hi_fill: str = "#B4560A"
    #: box corner radius as a fraction of its height; 0.5 is a capsule.
    hi_radius: float = 0.5
    hi_pad_x: int = 40
    hi_pad_y: int = 18
    #: outline on the spoken word; None keeps the base outline.
    hi_outline: int | None = 0
    #: outline colour of the spoken word (None = the base outline colour).
    #: With `hi_blur`, a wide coloured outline becomes a glow on that word only.
    hi_outline_color: str | None = None
    #: \blur on the spoken word alone (0 = the look's own `blur`).
    hi_blur: float = 0.0
    #: the spoken word grows to this % while it is spoken (100 = no pop).
    hi_scale: int = 100
    #: how a line arrives: "none", "fade", "pop" (85% -> 100%) or "bounce"
    #: (60% -> 110% -> 100%, for one-word captions).
    enter: str = "none"
    #: a rounded box behind the whole line, or None.
    box: str | None = None
    box_alpha: int = 0x40
    box_pad_x: int = 0
    box_pad_y: int = 0
    #: a line shorter than this (s) takes the next word too, so one-word
    #: captions do not flash past on a short function word.
    min_line: float = 0.0
    #: a spoken word shorter than this (s) keeps the previous word's
    #: highlight instead of flashing its own. 0 = every word highlighted.
    min_highlight: float = 0.0


#: Every word-positioned caption style, by `caption_style` key.
#:
#: Chosen 2026-10-17 from real renders of a 4K Arabic job (clip 07, eight
#: seconds, eight candidate looks side by side): `box` is the default, with
#: `pill`, `marker` and `youtube` offered. Rejected and NOT shipped, so
#: nobody rebuilds them blind: a Hormozi-style yellow-and-grow highlight, a
#: one-word bounce, a glow, and a minimal phrase style.
#:
#: Rules every look follows, from published short-form caption guides:
#: the line is centred 70% down the frame (`y`), above where TikTok (bottom
#: 25%) and Shorts (bottom 20%) put their buttons — the original pill sat at
#: ~81%, under them. 70% and not higher by request: 66% put the line
#: mid-chest on a seated talking head (2026-10-17). Reels covers its bottom
#: 35%, so on Reels the line overlaps that band at 70% as it already did at
#: 66%; 74% was tried and touches TikTok's line, so it is the floor. A
#: highlight shorter than 0.15s is not shown (`min_highlight`),
#: since about one word in ten in real Arabic speech is under 0.12s and a
#: highlight that lasts three frames reads as flicker; and `gap` clears the
#: highlight, measured on render.
YELLOW = "#FFD60A"
CAPTION_LOOKS: dict[str, CaptionLook] = {
    # The whole line on a dark see-through box, the spoken word in yellow —
    # TikTok's and CapCut's classic. Readable over any footage, because the
    # box, not a stroke, carries the contrast; so the letters need no outline.
    "box": CaptionLook(
        size=118, max_words=5, gap=26, y=0.70, outline=0, shadow=0,
        highlight="color", hi_text=YELLOW, hi_outline=None, enter="fade",
        box="#000000", box_alpha=0x48, box_pad_x=34, box_pad_y=24,
        min_highlight=0.15),
    # The saffron pill on the spoken word, with a stroke on the words around
    # it (the original pill had none and leaned on its drop shadow alone).
    "pill": CaptionLook(
        size=150, max_words=3, gap=44, y=0.70, outline=4, outline_color="#111111",
        shadow=2, shadow_alpha=0x60, highlight="pill", hi_fill=PILL_FILL,
        hi_pad_x=30, hi_pad_y=12, hi_outline=0, hi_scale=106, enter="pop",
        min_highlight=0.15),
    # A yellow highlighter box with black text on the spoken word — the
    # Submagic "Beasty" family.
    "marker": CaptionLook(
        size=150, max_words=3, gap=44, y=0.70, outline=4, outline_color="#111111",
        shadow=2, shadow_alpha=0x60, highlight="marker", hi_fill=YELLOW,
        hi_text="#111111", hi_radius=0.16, hi_pad_x=22, hi_pad_y=10, hi_outline=0,
        hi_scale=104, enter="pop", min_highlight=0.15),
    # The look chosen on 2026-10-16 (Tajawal 144, no outline, nothing dimmed,
    # 52px gap), kept as an option exactly as it was — including its low
    # placement — because it was asked for by name.
    "youtube": CaptionLook(
        size=PILL_FONT_SIZE, max_words=99, gap=PILL_WORD_GAP,
        outline=PILL_OUTLINE, dim_alpha=CAPTION_DIM_ALPHA,
        hi_pad_x=PILL_PAD_X, hi_pad_y=PILL_PAD_Y),
}
