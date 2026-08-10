"""Stage 4c — turn detections into a crop path.

Deterministic. No model, ever — the same rule stage 4 lives under, and for the
same reason. Stage 4b says *a face is here and that one is talking*, which is a
semantic claim; this file turns it into geometry under limits the detector never
sees. Semantic in, geometric out, exactly as `snap` takes MM:SS in and word
boundaries out.

That split is what keeps stage 5 model-free. `encode` receives numbers.

If the framing lags the subject or whips across the frame, the bug is here. If
the wrong person is framed, the bug is in stage 4b or its active-speaker model.
Diagnose them separately.
"""

from __future__ import annotations

import math
from collections import defaultdict

from ..core.constants import (
    TRACK_DEADZONE,
    TRACK_EMIT_FPS,
    TRACK_MAX_KEYFRAMES,
    TRACK_MAX_PAN,
    TRACK_SAMPLE_FPS,
    TRACK_SHOT_JUMP,
    TRACK_SMOOTH_SECONDS,
    TRACK_SPEAKING_MIN,
)
from ..core.types import Clip, Detection, Keyframe, Track

#: Once the window is moving it keeps moving until it is well inside the
#: deadzone, rather than stopping the instant it crosses the edge. Without the
#: hysteresis the crop chatters in and out at exactly the deadzone boundary.
_SETTLE_RATIO = 1.0 / 3.0


def crop_width(src_w: int, src_h: int) -> float:
    """Width of the 9:16 crop window, as a fraction of source width.

    The normalised twin of the `crop=w='min(iw,ih*9/16)'` in `encode.filtergraph`
    — kept here rather than imported so this module needs nothing from stage 5.
    A 16:9 source gives ~0.316; a source already narrower than 9:16 gives 1.0."""
    if src_w <= 0 or src_h <= 0:
        raise ValueError(f"source dimensions must be positive, got {src_w}x{src_h}")
    return min(1.0, (src_h * 9.0 / 16.0) / src_w)


def subject(candidates: list[Detection]) -> Detection | None:
    """Which face to frame at one sampled instant.

    Speaking wins when the active-speaker model is confident about anyone;
    otherwise the largest face does. Size is a deliberately crude fallback, and
    it is the only rule available on the `fast` tier, which reports 0.0 speaking
    for everything — that tier will sometimes frame the listener, which is the
    documented cost of choosing it."""
    usable = [d for d in candidates if math.isfinite(d.cx) and d.w > 0]
    if not usable:
        return None
    talking = [d for d in usable if d.speaking >= TRACK_SPEAKING_MIN]
    if talking:
        return max(talking, key=lambda d: d.speaking)
    return max(usable, key=lambda d: d.w * d.h)


def _observations(dets: list[Detection], clip: Clip) -> list[tuple[float, float]]:
    """Clip-relative (t, cx), one per sampled instant, sorted."""
    by_time: dict[float, list[Detection]] = defaultdict(list)
    for d in dets:
        if not math.isfinite(d.t):
            continue
        if clip.start <= d.t <= clip.end:
            by_time[d.t].append(d)

    out: list[tuple[float, float]] = []
    for t in sorted(by_time):
        chosen = subject(by_time[t])
        if chosen is not None:
            out.append((t - clip.start, chosen.cx))
    return out


def _segment(obs: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split the observations wherever the subject jumps far enough to be a cut
    rather than a movement. Smoothing across a hard cut would invent a pan that
    is not in the source."""
    if not obs:
        return []
    segments = [[obs[0]]]
    # strict=False: pairing each element with its successor is exactly what the
    # length mismatch is for. `edits.diff` uses strict=True for the opposite
    # reason — there a length mismatch is the bug being caught.
    for prev, cur in zip(obs, obs[1:], strict=False):
        if abs(cur[1] - prev[1]) > TRACK_SHOT_JUMP:
            segments.append([cur])
        else:
            segments[-1].append(cur)
    return segments


def _smooth(seg: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Centred moving average over TRACK_SMOOTH_SECONDS.

    Removes detector jitter before the deadzone sees it — denoise first, then
    decide whether the subject actually moved."""
    if len(seg) < 3:
        return list(seg)
    half = TRACK_SMOOTH_SECONDS / 2.0
    out = []
    for t, _ in seg:
        window = [cx for u, cx in seg if abs(u - t) <= half]
        out.append((t, sum(window) / len(window)))
    return out


def _drive(segments: list[list[tuple[float, float]]], duration: float,
           lo: float, hi: float, crop_w: float) -> list[Keyframe]:
    """Walk the smoothed targets and emit the path the crop window actually takes.

    Three rules, in order: hold still inside the deadzone, never travel faster
    than TRACK_MAX_PAN, and re-anchor instantly at a shot change — a cut is the
    one time an instant jump is correct, because the source already jumped.

    Both limits scale by `crop_w`, because they are budgets against what the
    viewer sees rather than against the source frame."""
    deadzone = TRACK_DEADZONE * crop_w
    settle = deadzone * _SETTLE_RATIO
    max_pan = TRACK_MAX_PAN * crop_w
    step = 1.0 / TRACK_EMIT_FPS
    keys: list[Keyframe] = []
    cur = _clamp(segments[0][0][1], lo, hi)
    moving = False
    t = 0.0

    for index, seg in enumerate(segments):
        if index > 0:
            # A cut. Snap rather than pan, and emit nothing in between: sendcmd
            # holds the previous value until the next command, so the hold is
            # implicit and the jump lands on exactly one frame.
            # `max` keeps the emitted times monotonic when a segment is shorter
            # than one emit step.
            t = max(t, seg[0][0])
            cur = _clamp(seg[0][1], lo, hi)
            moving = False
            keys.append(Keyframe(round(t, 4), round(cur, 5)))
            t += step

        seg_end = seg[-1][0] if index + 1 < len(segments) else duration
        while t <= seg_end + 1e-9:
            target = _clamp(_at(seg, t), lo, hi)
            gap = target - cur
            if moving:
                if abs(gap) <= settle:
                    moving = False
            elif abs(gap) > deadzone:
                moving = True
            if moving:
                reach = max_pan * step
                cur = _clamp(cur + _clamp(gap, -reach, reach), lo, hi)
            keys.append(Keyframe(round(t, 4), round(cur, 5)))
            t += step

    return keys


def _at(seg: list[tuple[float, float]], t: float) -> float:
    """Linear interpolation inside one segment; hold at either end."""
    if t <= seg[0][0]:
        return seg[0][1]
    if t >= seg[-1][0]:
        return seg[-1][1]
    for (t0, a), (t1, b) in zip(seg, seg[1:], strict=False):
        if t0 <= t <= t1:
            span = t1 - t0
            return a if span <= 0 else a + (b - a) * (t - t0) / span
    return seg[-1][1]


def _clamp(value: float, lo: float, hi: float) -> float:
    return lo if value < lo else hi if value > hi else value


def solve(dets: list[Detection], clip: Clip, crop_w: float, *,
          detector: str = "", tier: str = "") -> Track:
    """Detections in, crop path out. The whole of stage 4c.

    Falls back to a static centre crop when nothing usable was found, which is
    never an error — a dark frame, a turned head and a back-of-head shot all
    legitimately produce no face. It is reported on the Track rather than
    logged and forgotten, because fifty silently centre-cropped clips is the
    failure mode that matters."""
    lo, hi = crop_w / 2.0, 1.0 - crop_w / 2.0
    if lo > hi:                      # source already narrower than 9:16
        lo = hi = 0.5

    obs = _observations(dets, clip)
    if not obs:
        return Track(keyframes=[Keyframe(0.0, 0.5)], detector=detector,
                     tier=tier, coverage=0.0, fallback=True)

    segments = [_smooth(seg) for seg in _segment(obs)]
    keys = _drive(segments, clip.duration, lo, hi, crop_w)

    expected = max(1.0, clip.duration * TRACK_SAMPLE_FPS.get(tier, 1.0))
    return Track(
        keyframes=keys[:TRACK_MAX_KEYFRAMES],
        detector=detector,
        tier=tier,
        coverage=round(min(1.0, len(obs) / expected), 4),
        fallback=False,
    )


def sanitise(track: Track, crop_w: float, duration: float) -> Track:
    """Make any track safe to render, wherever it came from.

    Enforced here rather than only at the HTTP layer because the risk is a crop
    window off the edge of the frame — an ffmpeg failure — not a bad request.
    The CLI reading a hand-edited `track-NN.json` is held to the same rule.

    Finiteness is checked *before* any comparison: NaN defeats `<` and `>` both,
    so a NaN would sail through a range check written the obvious way. Same trap
    as `edits.diff`.

    What is deliberately *not* enforced: the pan speed limit. An out-of-frame
    crop breaks the render, so it is corrected; a fast pan is a taste decision,
    and a human asking for a hard reframe is making the same kind of deliberate
    call as a human editing a plan."""
    lo, hi = crop_w / 2.0, 1.0 - crop_w / 2.0
    if lo > hi:
        lo = hi = 0.5

    clean: list[Keyframe] = []
    seen: set[float] = set()
    for k in track.keyframes[:TRACK_MAX_KEYFRAMES]:
        if not (math.isfinite(k.t) and math.isfinite(k.cx)):
            continue
        if k.t < 0.0 or k.t > duration:
            continue
        t = round(k.t, 4)
        if t in seen:
            continue
        seen.add(t)
        clean.append(Keyframe(t, _clamp(k.cx, lo, hi)))

    clean.sort(key=lambda k: k.t)
    if not clean:
        return Track(keyframes=[Keyframe(0.0, 0.5)], detector=track.detector,
                     tier=track.tier, coverage=0.0, fallback=True)

    track.keyframes = clean
    return track
