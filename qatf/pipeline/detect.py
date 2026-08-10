"""Stage 4b — find the faces, and (later) decide which one is talking.

The only place in the pipeline where a vision model runs. It emits observations
and nothing else: no crop geometry, no clip awareness, no 9:16. `framing` turns
what this produces into a window, deterministically, which is what keeps stage 5
model-free.

The cache is keyed to the **video**, not to the plan, and covers a set of spans
that grows as more of the video is asked for. That asymmetry is the point:
detections belong to the footage, so moving a cut in the plan editor re-solves
the crop path from disk in milliseconds instead of re-running a detector.

NOT YET IMPLEMENTED: the active-speaker model. Every Detection this module
emits carries `speaking=0.0`, so `framing.subject` always takes its
largest-face fallback. The tiers below therefore differ only in sample rate and
detector choice today — `best` is not yet meaningfully better than `balanced` at
picking the right person in a two-shot. Say so before claiming multi-speaker
support.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path

from ..core.constants import TRACK_SAMPLE_FPS, TRACK_TIERS
from ..core.errors import CommandFailed, DetectorNotAvailable, FFmpegNotFound
from ..core.types import Clip, Detection, detections_from_dicts, detections_to_dicts
from ..core.utils import binary, log, probe_duration, probe_video, slugify

#: Width the frames are scaled to before detection. Detection does not need 4K —
#: a face is a face at 640 wide — and decoding at full resolution is most of the
#: cost on a ProRes master.
DETECT_WIDTH = 640

#: The one detector. `cv2.FaceDetectorYN` ships in every opencv-python wheel on
#: both majors — objdetect/face.hpp is byte-identical between the 4.x and 5.x
#: branches — and its weights are vendored beside this file, so there is nothing
#: to download and nothing to degrade to.
#:
#: A `haar` detector lived here briefly. It was removed not merely because
#: OpenCV 5.0 moved CascadeClassifier out to opencv_contrib, but because the one
#: property it existed for is gone: NEITHER 5.x wheel ships a cascade XML, so
#: "works with no download" stopped being true of it on any install.
DETECTORS = ("yunet",)
TIER_DETECTOR = {"fast": "yunet", "balanced": "yunet", "best": "yunet"}

#: Vendored YuNet weights, 232,589 bytes. MIT, Copyright (c) 2020 Shiqi Yu —
#: LICENSE.yunet sits beside the file and must travel with any copy of it.
BUNDLED_MODEL = (Path(__file__).resolve().parent
                 / "assets" / "face_detection_yunet_2023mar.onnx")

#: Detector confidence floor. UNMEASURED. The OpenCV default is 0.9; 0.6 trades
#: false positives for recall on profile and low-light faces, which matters on
#: the field audio this project targets. It is the number most likely to produce
#: a visibly wrong crop, because a large false positive is exactly what
#: `framing.subject`'s largest-face fallback will happily frame.
YUNET_SCORE = 0.6

#: How far either side of a clip to detect. A cut moved by a second in the plan
#: editor then still lands inside already-cached footage.
SPAN_MARGIN = 2.0


# -- pure span arithmetic; no cv2, no I/O ----------------------------------

def merge_spans(spans: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Sort and coalesce. Touching spans merge — [0,5] and [5,9] are one span."""
    ordered = sorted((a, b) for a, b in spans if b > a)
    out: list[tuple[float, float]] = []
    for a, b in ordered:
        if out and a <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return out


def missing(covered: list[tuple[float, float]],
            wanted: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """The parts of `wanted` no previous run has already detected.

    This is the whole of approach ③: never re-detect footage, never detect
    footage no clip asks for."""
    gaps: list[tuple[float, float]] = []
    for a, b in merge_spans(wanted):
        cursor = a
        for ca, cb in covered:
            if cb <= cursor:
                continue
            if ca >= b:
                break
            if ca > cursor:
                gaps.append((cursor, min(ca, b)))
            cursor = max(cursor, cb)
            if cursor >= b:
                break
        if cursor < b:
            gaps.append((cursor, b))
    return merge_spans(gaps)


def clip_spans(clips: list[Clip], duration: float | None = None,
               margin: float = SPAN_MARGIN) -> list[tuple[float, float]]:
    """Clip boundaries widened by `margin` and clamped at zero."""
    spans = [(max(0.0, c.start - margin),
              c.end + margin if duration is None else min(duration, c.end + margin))
             for c in clips]
    return merge_spans(spans)


def sample_times(spans: list[tuple[float, float]], fps: float) -> list[float]:
    """Sample instants inside `spans`, on a grid anchored at t=0.

    Anchoring matters: a later run that extends a span has to land on the same
    instants, or the cache fills with pairs of samples a millisecond apart that
    `framing` would then read as two observations of one moment."""
    if fps <= 0:
        raise ValueError(f"sample fps must be positive, got {fps}")
    step = 1.0 / fps
    out: list[float] = []
    for a, b in merge_spans(spans):
        k = int(a / step)
        if k * step < a:
            k += 1
        t = k * step
        while t <= b + 1e-9:
            out.append(round(t, 3))
            t = (k := k + 1) * step
    return out


# -- cache ------------------------------------------------------------------

def cache_key(video: Path) -> str:
    """A short identity for the footage plus the settings that shape a detection.

    The video part is (resolved path, size, mtime) rather than a content hash:
    a `stat()` is free where reading 75GB of ProRes is not, and the two kinds of
    error are not symmetric. Over-triggering — a move, a copy, a touch — costs
    one re-detection. Under-triggering ships fifty clips cropped to where a
    *different* video's face was, with `Track.fallback` False, so nothing flags
    it. Bias the key toward re-detecting.

    DETECT_WIDTH and YUNET_SCORE are in here for the reason `asr.cache_path`
    carries the Whisper size: a knob that does not reach the key is a knob that
    silently does nothing on a warm work directory, and YUNET_SCORE is the one
    documented as most likely to produce a visibly wrong crop."""
    try:
        st = video.stat()
        identity = f"{video.resolve()}|{st.st_size}|{int(st.st_mtime)}"
    except OSError:
        # No file to stat means detection is about to fail anyway; key on the
        # path alone rather than raising from a pure naming function.
        identity = str(video)
    raw = f"{identity}|w={DETECT_WIDTH}|score={YUNET_SCORE}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def cache_path(work: Path, video: Path, detector: str, tier: str) -> Path:
    """Where one video's detections live.

    `video` is not optional, and that is the point: the previous signature keyed
    only on detector and tier, so a second video rendered into the same output
    directory found every span already "covered" and inherited the first one's
    face positions.

    detector and tier are slugified for the same reason `asr.cache_path` does
    it: these reach a filename, and `tier` arrives from a request body. The key
    is a hex digest, so it needs no slugifying to be path-safe."""
    return (work /
            f"faces-{slugify(detector)}-{slugify(tier)}-{cache_key(video)}.json")


def read_cache(path: Path) -> tuple[list[tuple[float, float]], list[Detection]]:
    if not path.exists():
        return [], []
    raw = json.loads(path.read_text(encoding="utf-8"))
    spans = [(float(a), float(b)) for a, b in raw.get("spans", [])]
    return merge_spans(spans), detections_from_dicts(raw.get("detections", []))


def write_cache(path: Path, spans: list[tuple[float, float]],
                dets: list[Detection]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "spans": [[round(a, 3), round(b, 3)] for a, b in merge_spans(spans)],
        "detections": detections_to_dicts(sorted(dets, key=lambda d: d.t)),
    }), encoding="utf-8")
    return path


# -- the detector itself ----------------------------------------------------

def _load_cv2():
    try:
        import cv2
    except ImportError as exc:
        raise DetectorNotAvailable(
            "subject tracking needs OpenCV — install it with "
            "`pip install -e \".[track]\"`, or pick --reframe crop"
        ) from exc
    # OpenCV 5 prints `setPreferableTarget Targets are not supported by the new
    # graph engine for now` to stderr on every FaceDetectorYN.create(). It is
    # harmless, it fires once per span, and in CLI output it reads as an error.
    try:
        cv2.utils.logging.setLogLevel(cv2.utils.logging.LOG_LEVEL_ERROR)
    except AttributeError:      # older builds expose no logging namespace
        pass
    return cv2


def _opencv_distributions() -> list[str]:
    """Which opencv wheels are installed. Failure-path diagnostic only.

    All four share the `cv2` namespace and upstream says to install exactly one.
    Two of them together produces "Failed to parse ONNX model", which points
    nowhere near the cause."""
    from importlib import metadata
    known = ("opencv-python", "opencv-python-headless",
             "opencv-contrib-python", "opencv-contrib-python-headless")
    installed = {d.metadata["Name"] for d in metadata.distributions()}
    return [n for n in known if n in installed]


def yunet_model() -> Path | None:
    """Where the YuNet weights are, or None.

    QATF_YUNET_MODEL wins outright: if it names a file that is not there, this
    returns None rather than falling back to the bundled copy, because an
    override that silently does nothing is worse than one that fails. Otherwise
    the vendored weights, which is the normal path and needs no configuration."""
    named = os.environ.get("QATF_YUNET_MODEL", "")
    if named:
        candidate = Path(named).expanduser()
        # resolve() so the path that gets logged and reported is canonical rather
        # than whatever shape the environment happened to supply. Not a security
        # boundary — this variable is set by whoever runs the process, never by a
        # request body — which is why it is allowed to point anywhere at all.
        return candidate.resolve() if candidate.exists() else None
    return BUNDLED_MODEL if BUNDLED_MODEL.exists() else None


def _open_detector(name: str, size: tuple[int, int]):
    """Build a detector. Raises rather than degrading to a weaker one.

    With a single detector there is nothing to degrade *to*, which is the
    configuration that best satisfies the never-silently-degrade rule. The
    signature keeps `name` because `cache_path` is keyed on it, and that key is
    what stops a future second detector reading this one's cache."""
    cv2 = _load_cv2()
    if name != "yunet":
        raise DetectorNotAvailable(
            f"unknown detector {name!r}, expected one of {DETECTORS}")
    model = yunet_model()
    if model is None:
        raise DetectorNotAvailable(
            f"the YuNet weights are missing. They ship with the package at "
            f"{BUNDLED_MODEL}; if QATF_YUNET_MODEL is set it must name a file "
            f"that exists (currently {os.environ.get('QATF_YUNET_MODEL', '')!r})"
        )
    try:
        return ("yunet", cv2.FaceDetectorYN.create(
            str(model), "", size, YUNET_SCORE, 0.3, 5000))
    except cv2.error as exc:
        # "Failed to parse ONNX model" is the symptom of two opencv wheels
        # sharing the cv2 namespace, and it points nowhere near that cause.
        dists = _opencv_distributions()
        extra = (f" — {len(dists)} OpenCV distributions are installed ({', '.join(dists)}); "
                 f"they share the cv2 namespace and exactly one may be installed"
                 if len(dists) > 1 else "")
        raise DetectorNotAvailable(f"could not load {model}: {exc}{extra}") from exc


def probe_detector(tier: str) -> str:
    """Check a tier can actually run, and say which detector it would use.

    For preflight. Building the detector is the only honest test — OpenCV can be
    importable while its cascade file is missing, and yunet can be selected with
    no model on disk."""
    if tier not in TRACK_TIERS:
        raise DetectorNotAvailable(
            f"unknown tracking tier {tier!r}, expected one of {TRACK_TIERS}")
    name = TIER_DETECTOR[tier]
    _open_detector(name, (DETECT_WIDTH, DETECT_WIDTH))
    return name


def _frames(video: Path, span: tuple[float, float], fps: float,
            width: int, height: int):
    """Decode one span at `fps`, scaled down, as raw BGR frames.

    One ffmpeg per span rather than a seek per sample: seeking is slow and, on
    long-GOP footage, lands on the wrong frame often enough to smear the
    timeline the whole feature depends on.

    `sample_times` supplies both the seek point and every label, so a span
    extended later by a plan edit re-samples the same instants instead of
    filling the cache with pairs of samples milliseconds apart.

    Labelling from the grid, not by accumulating `start + index / fps`, is the
    part that is easy to get wrong: `start` is rounded, so walking from it
    diverges from the true grid within a few frames. Measured — spans (2.37, 5)
    and (2.0, 5) at 3fps agreed on 2.667 and then produced 3.334 and 3.333 for
    the same instant, which is precisely the duplicate-observation bug the
    anchoring exists to prevent.

    Failure is loud in both directions it can go quiet: a non-zero exit raises
    rather than reading as "no faces here", because `detections_for` records
    whatever comes back as covered and a silent empty result poisons the cache
    for every later run."""
    a, b = span
    grid = sample_times([span], fps)
    if not grid:
        return
    start = grid[0]
    cmd = [binary("ffmpeg"), "-v", "error", "-ss", f"{start:.3f}", "-i", str(video),
           "-t", f"{b - start:.3f}", "-vf", f"fps={fps},scale={width}:{height}",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-"]

    # stderr goes to a temp file, never a pipe. Nothing reads it until the
    # decode is over, and a damaged long-GOP source emits `error` lines fast
    # enough to fill the ~64KB pipe buffer — at which point ffmpeg blocks on the
    # write, stops producing frames, and `wait()` below never returns.
    with tempfile.TemporaryFile() as errors:
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errors)
        except FileNotFoundError as exc:
            raise FFmpegNotFound(f"{cmd[0]} not found on PATH") from exc

        size = width * height * 3
        index = 0
        drained = False
        try:
            while True:
                buf = proc.stdout.read(size)
                if len(buf) < size:
                    drained = True
                    break
                # grid[index] where the grid reaches; the fallback only covers
                # ffmpeg handing back more frames than the span asked for.
                yield (grid[index] if index < len(grid)
                       else round(start + index / fps, 3)), buf
                index += 1
        finally:
            proc.stdout.close()
            if not drained:
                # The consumer stopped early or raised. ffmpeg still has the
                # rest of the span to decode and would block on a closed pipe.
                proc.kill()
            proc.wait()

        if proc.returncode != 0:
            errors.seek(0)
            tail = "\n".join(errors.read().decode("utf-8", "replace")
                             .strip().splitlines()[-15:])
            raise CommandFailed(
                f"ffmpeg failed decoding {a:.3f}s-{b:.3f}s of {video}\n{tail}")


def detect_span(video: Path, span: tuple[float, float], fps: float,
                detector: str, src_w: int, src_h: int) -> list[Detection]:
    """Run the detector over one span. Coordinates come back normalised.

    Boxes are NOT guaranteed to sit inside the frame — a face straddling an edge
    comes back with a negative x — so the normalised centres this emits can fall
    outside [0, 1]. That is real, not a bug, and `framing` clamps the crop window
    anyway. Do not add an assertion here; it would be a false invariant that
    fails on exactly the footage this feature exists for."""
    _load_cv2()
    import numpy as np

    width = DETECT_WIDTH if src_w > DETECT_WIDTH else src_w
    height = max(2, int(round(src_h * width / src_w)) & ~1)
    _, engine = _open_detector(detector, (width, height))
    # A frame that disagrees with the configured input size RAISES — YuNet does
    # not resize and does not warn. What actually guarantees agreement is the
    # `scale={width}:{height}` in `_frames`; this call states the invariant.
    engine.setInputSize((width, height))

    out: list[Detection] = []
    for t, buf in _frames(video, span, fps, width, height):
        # frombuffer gives a read-only array; detect() accepts one, so no copy.
        frame = np.frombuffer(buf, dtype=np.uint8).reshape(height, width, 3)
        # detect() returns (retval, faces) where faces is None — not an empty
        # array — when nothing is found, and retval is NOT a face count (it is 1
        # on an empty frame). Never branch on the first element.
        _, faces = engine.detect(frame)
        boxes = [(f[0], f[1], f[2], f[3], float(f[14]))
                 for f in (faces if faces is not None else [])]
        for x, y, w, h, score in boxes:
            out.append(Detection(
                t=round(t, 3),
                cx=float((x + w / 2) / width),
                cy=float((y + h / 2) / height),
                w=float(w / width),
                h=float(h / height),
                score=float(score),
                speaking=0.0,        # no active-speaker model yet; see module docstring
            ))
    return out


def detections_for(video: Path, clips: list[Clip], work: Path, *,
                   tier: str = "balanced",
                   detector: str | None = None) -> tuple[list[Detection], str]:
    """The stage 4b entry point. Returns (detections, detector actually used).

    Only the spans no previous run covered are detected; everything else is
    served from `faces-<detector>-<tier>.json`."""
    if tier not in TRACK_TIERS:
        raise DetectorNotAvailable(
            f"unknown tracking tier {tier!r}, expected one of {TRACK_TIERS}")
    name = detector or TIER_DETECTOR[tier]
    fps = TRACK_SAMPLE_FPS[tier]

    meta = probe_video(video)
    src_w, src_h = int(meta["width"]), int(meta["height"])

    path = cache_path(work, video, name, tier)
    covered, cached = read_cache(path)
    # Clamped to the real duration: a span reaching past the last frame decodes
    # to nothing and would then be written to the cache as covered, so the
    # margin around a final clip would look permanently detected-and-empty.
    wanted = clip_spans(clips, probe_duration(video))
    todo = missing(covered, wanted)

    if not todo:
        log(f"stage 4b: {len(cached)} detections from cache ({name}, {tier})")
        return cached, name

    fresh: list[Detection] = []
    for span in todo:
        log(f"stage 4b: detecting {span[0]:.1f}s-{span[1]:.1f}s with {name} at {fps}fps")
        fresh += detect_span(video, span, fps, name, src_w, src_h)

    dets = cached + fresh
    write_cache(path, covered + todo, dets)
    log(f"stage 4b: +{len(fresh)} detections, {len(dets)} cached total")
    return dets, name
