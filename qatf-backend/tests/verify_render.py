"""Render-and-measure checks for stages 4b, 4c and 5b.

    python tests/verify_render.py

NOT one of the dependency-free suites. `smoke_pipeline.py`, `smoke_llm.py` and
`smoke_api.py` deliberately need no ffmpeg, GPU, key or network; this one needs
**ffmpeg**, and its second fixture needs **OpenCV** and one **network fetch**
(cached afterwards). It exists because of the working agreement that a change to
a filtergraph must be verified by rendering a clip and looking at a frame — this
automates the looking, so the check survives into the next refactor.

Two fixtures, deliberately different in what they can prove:

  A. synthetic   a red bar on a known path, detections hand-built.
                 Exercises framing + sendcmd + the filtergraph with NO detector,
                 so a failure here is geometry, never the model.
  B. real face   a public-domain headshot composited onto a known path.
                 Exercises YuNet as well, and scores detection against a
                 ground truth known in closed form.

Both render `crop` alongside `track` as a control, and both ASSERT THE CONTROL
FAILS to hold the subject. That is not decoration: the first version of fixture
A produced a source with no subject in it at all (ffmpeg's `drawbox` evaluates
`x` once at init, where `t` is undefined, so the expression silently yields NaN).
Track and crop both reported "subject absent" and it read exactly like a broken
feature. A control that cannot fail is measuring nothing.

Measure position, never pixel equality — see the RTL section in CLAUDE.md for
what byte-for-byte frame diffs did to a caption test.
"""

from __future__ import annotations

import http.client
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import score_transcript
from _harness import check, report, section

from qatf.core.errors import CommandFailed, FFmpegNotFound
from qatf.core.types import Clip, Detection
from qatf.core.utils import binary, check_ffmpeg
from qatf.pipeline import detect, encode, framing

WORK = Path(__file__).resolve().parent / ".render-check"
SRC_W, SRC_H = 1280, 720
OUT_W, OUT_H = 1080, 1920
DUR = 12.0
PROBES = (0.5, 3.0, 6.0, 9.0, 11.5)

#: Ginger Kerrick's NASA headshot. A US federal work, so public domain. Fetched
#: rather than committed: the repo has no business carrying a photo of a person
#: to test a crop with.
FACE_URL = ("https://commons.wikimedia.org/wiki/"
            "Special:FilePath/Ginger_Kerrick_NASA_Headshot.jpg?width=600")
FACE_CACHE = Path.home() / ".cache" / "qatf" / "face-test.jpg"


def ff(*args: str) -> None:
    # binary(), not the bare name: QATF_FFMPEG exists for hosts where ffmpeg is
    # installed and not on PATH, and the pipeline honours it. A suite that does
    # not resolve it the same way skips itself on exactly those hosts.
    subprocess.run([binary("ffmpeg"), "-y", "-v", "error", *args], check=True)


def raw_frame(video: Path, t: float) -> bytes | None:
    out = subprocess.run(
        [binary("ffmpeg"), "-v", "error", "-ss", f"{t}", "-i", str(video),
         "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        capture_output=True, check=True).stdout
    return out if len(out) >= OUT_W * OUT_H * 3 else None


def red_centre(video: Path, t: float) -> float | None:
    """Horizontal centre of the red pixels, 0..1 of output width.

    `raw_frame` yields **bgr24** — OpenCV's order, which `face_centre` needs —
    so red is the THIRD byte of each pixel, not the first. Reading it as rgb24
    here searches for blue, finds none, and reports the subject absent from both
    the tracked render and the control. That is what it did on the first run."""
    raw = raw_frame(video, t)
    if raw is None:
        return None
    total = weighted = 0
    for y in range(0, OUT_H, 8):
        row = y * OUT_W * 3
        for x in range(OUT_W):
            i = row + x * 3
            blue, green, red = raw[i], raw[i + 1], raw[i + 2]
            if red > 150 and green < 90 and blue < 90:
                total += 1
                weighted += x
    return None if not total else weighted / total / OUT_W


def face_centre(video: Path, t: float) -> float | None:
    """Where a detector finds the face in a RENDERED frame, 0..1 of width.

    A second, independent detector run — the track is not consulted, so this
    cannot confirm itself."""
    import cv2
    import numpy as np
    raw = raw_frame(video, t)
    if raw is None:
        return None
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(OUT_H, OUT_W, 3)
    det = cv2.FaceDetectorYN.create(str(detect.yunet_model()), "",
                                    (OUT_W, OUT_H), detect.YUNET_SCORE, 0.3, 5000)
    _, faces = det.detect(frame)
    if faces is None or not len(faces):
        return None
    f = max(faces, key=lambda f: f[2] * f[3])
    return float((f[0] + f[2] / 2) / OUT_W)


def render_pair(video: Path, clip: Clip, track, stem: str) -> tuple[Path, Path]:
    for mode in ("track", "crop"):
        encode.render_all(
            video, [clip], [], WORK / stem / mode, WORK / stem,
            mode=mode, captions=False, codec="h264", preset="veryfast",
            width=OUT_W, height=OUT_H,
            tracks=[track] if mode == "track" else None,
            src=(SRC_W, SRC_H) if mode == "track" else None)
    name = encode.clip_stem(1, clip) + ".mp4"
    return WORK / stem / "track" / name, WORK / stem / "crop" / name


def score(label: str, tracked: list, control: list) -> None:
    """The three assertions every fixture makes, in one place."""
    seen = [v for v in tracked if v is not None]
    ctrl = [v for v in control if v is not None]
    check(f"{label}: track holds the subject in every frame",
          len(seen) == len(tracked), f"{len(seen)}/{len(tracked)}")
    check(f"{label}: track keeps it near centre",
          bool(seen) and max(abs(v - 0.5) for v in seen) < 0.12,
          f"worst offset {max((abs(v - 0.5) for v in seen), default=float('nan')):.3f}")
    # If the control also passes, the fixture is not exercising anything.
    check(f"{label}: CONTROL — static crop loses the subject, proving the "
          f"measurement is real",
          len(ctrl) < len(control), f"crop held it in {len(ctrl)}/{len(control)}")


try:
    # check_ffmpeg, not shutil.which: it honours QATF_FFMPEG and it actually
    # RUNS the binary. `which` reported nothing on a host where ffmpeg was
    # configured by env, and this suite — the only one that catches what a
    # dimension check misses — exited 0 without rendering anything.
    check_ffmpeg()
except FFmpegNotFound as exc:
    print(f"{exc}\nthis suite renders, so there is nothing to do")
    raise SystemExit(0) from None

if WORK.exists():
    shutil.rmtree(WORK)
WORK.mkdir(parents=True)

# ---------------------------------------------------------------- fixture A
section("fixture A — synthetic bar, solver and filtergraph only")
# overlay, not drawbox: drawbox evaluates x once at init where `t` is undefined,
# so an expression there silently draws nothing at all. See the module docstring.
bar = WORK / "a.mp4"
ff("-f", "lavfi", "-i", f"color=c=0x303030:s={SRC_W}x{SRC_H}:d={DUR}:r=30",
   "-f", "lavfi", "-i", f"color=c=red:s=90x160:d={DUR}:r=30",
   "-filter_complex", "[0][1]overlay=x='300+t*60':y=280:eval=frame",
   "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(bar))

bar_true = lambda t: (300.0 + 60.0 * t + 45) / SRC_W          # noqa: E731
clip_a = Clip(0.0, DUR, "bar")
crop_w = framing.crop_width(SRC_W, SRC_H)
dets_a = [Detection(t=i / 3.0, cx=bar_true(i / 3.0), cy=0.5, w=90 / SRC_W,
                    h=160 / SRC_H, score=0.95)
          for i in range(int(DUR * 3) + 1)]
track_a = framing.solve(dets_a, clip_a, crop_w, detector="synthetic", tier="balanced")
check("solver produced a moving path", not track_a.fallback and len(track_a.keyframes) > 1,
      f"{len(track_a.keyframes)} keyframes")
t_a, c_a = render_pair(bar, clip_a, track_a, "a")
score("synthetic", [red_centre(t_a, t) for t in PROBES],
      [red_centre(c_a, t) for t in PROBES])

# --------------------------------------------------------------- the decoder
section("stage 4b decode — ffmpeg only, no detector")
# Between fixture A (no ffmpeg subprocess of its own) and fixture B (needs
# OpenCV) sat the one function with neither: `_frames` drives ffmpeg directly
# rather than through `core.utils.run`, so it re-implements the failure handling
# that module owns. On a host without OpenCV — which is most of them until
# someone installs the extra — none of this was exercised at all.
_span, _fps = (2.37, 5.0), 3.0
_got = [t for t, _ in detect._frames(bar, _span, _fps, 160, 90)]
check("decode lands on the zero-anchored grid, not on the span edge",
      _got and _got[0] == 2.667, f"first instant {_got[0] if _got else None}")
# The bug this catches is invisible in one run: labelling by accumulation from a
# rounded start gave 3.334 here and 3.333 for the same instant in the span
# below, so `framing._observations` saw one moment as two.
_wider = [t for t, _ in detect._frames(bar, (2.0, 5.0), _fps, 160, 90)]
check("a re-sampled span reproduces the same instants exactly",
      all(t in _wider for t in _got if t >= 2.0),
      f"{sorted(set(_got) - set(_wider))} drifted")
check("every decoded frame is the size the detector was configured for",
      all(len(b) == 160 * 90 * 3
          for _, b in detect._frames(bar, (0.0, 1.0), _fps, 160, 90)))

# A failed decode must RAISE. Returning [] reads as "no faces here", and
# `detections_for` writes that span to the cache as covered — so one bad decode
# permanently convinces every later run that the footage holds no subject.
_corrupt = WORK / "not-a-video.mp4"
_corrupt.write_bytes(b"this is not a video" * 100)
try:
    list(detect._frames(_corrupt, (0.0, 2.0), _fps, 160, 90))
    check("a failed decode raises rather than reading as 'no faces'", False,
          "returned empty")
except CommandFailed:
    check("a failed decode raises rather than reading as 'no faces'", True)

# No timeout and no kill() in here means a hang, not a failure.
_gen = detect._frames(bar, (0.0, DUR), 8.0, 160, 90)
next(_gen)
_gen.close()
check("abandoning the decode mid-span does not deadlock on wait()", True)

_saved = os.environ.get("QATF_FFMPEG")
os.environ["QATF_FFMPEG"] = str(WORK / "no-such-ffmpeg.exe")
try:
    list(detect._frames(bar, (0.0, 1.0), _fps, 160, 90))
    _typed = False
except FFmpegNotFound:
    _typed = True
except FileNotFoundError:
    _typed = False          # raw OSError escaping the pipeline is the bug
finally:
    if _saved is None:
        os.environ.pop("QATF_FFMPEG", None)
    else:
        os.environ["QATF_FFMPEG"] = _saved
check("a missing ffmpeg is FFmpegNotFound, not a raw FileNotFoundError", _typed)

# ------------------------------------------------------- score_transcript.py
section("score_transcript.speech_intervals — a failed ffmpeg must raise")
# speech_intervals used to trust silencedetect's stderr unconditionally: no
# silence_start/silence_end lines parses as ZERO silences, so a failed ffmpeg
# call (bad path, unsupported codec, missing binary) read as the WHOLE file
# being speech — a failed measurement rendering as a clean result, on exactly
# the metric that exists to catch that shape of bug (uncovered_speech leans on
# this being honest). Lives here, not in smoke_pipeline.py, because it needs a
# real ffmpeg to actually fail against.
_bad_audio = WORK / "not-audio.wav"
_bad_audio.write_bytes(b"not a real wav file, just plain bytes" * 200)
# probe_duration is checked first in speech_intervals and already raises on
# its own failure — a garbage file usually fails there too, which would make
# this check pass for the WRONG reason (the pre-existing duration guard, not
# the silencedetect-returncode check this is actually verifying). Fake a
# valid duration so the run reaches the real ffmpeg silencedetect call, which
# then genuinely fails on non-audio bytes.
_real_probe_duration = score_transcript.probe_duration
score_transcript.probe_duration = lambda *a, **k: 5.0
try:
    score_transcript.speech_intervals(_bad_audio)
    check("speech_intervals raises rather than reporting a full-file speech span",
          False, "returned a speech list instead of raising")
except CommandFailed:
    check("speech_intervals raises rather than reporting a full-file speech span", True)
finally:
    score_transcript.probe_duration = _real_probe_duration

# ---------------------------------------------------------------- fixture B
section("fixture B — real face, full stage 4b chain")
try:
    import cv2  # noqa: F401
    have_cv2 = True
except ImportError:
    have_cv2 = False

if not have_cv2:
    print("  SKIP  OpenCV not installed — `pip install -e \".[track]\"` to run this")
elif detect.yunet_model() is None:
    print("  SKIP  no YuNet weights found")
else:
    if not FACE_CACHE.exists():
        FACE_CACHE.parent.mkdir(parents=True, exist_ok=True)
        try:
            req = urllib.request.Request(FACE_URL, headers={
                "User-Agent": "qatf-tests/0.1 (render verification; +https://example.invalid)"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
            # Validate BEFORE it reaches the permanent cache. A 200 carrying a
            # rate-limit page or a captive-portal interception is still a 200,
            # and writing it here poisons ~/.cache/qatf for good: every later
            # run takes the exists() fast path, ffmpeg rejects the HTML, and the
            # suite dies with a traceback instead of the SKIP below. Nothing
            # re-fetches.
            if len(body) < 4096 or not body.startswith(b"\xff\xd8\xff"):
                print(f"  SKIP  the fetched test face is not a JPEG "
                      f"({len(body)} bytes, starts {body[:8]!r})")
            else:
                FACE_CACHE.write_bytes(body)
        except (urllib.error.URLError, TimeoutError, OSError,
                http.client.HTTPException) as exc:
            # HTTPException, not just OSError: http.client.IncompleteRead — a
            # truncated read part-way through the download — derives from
            # HTTPException and would otherwise escape as a traceback rather
            # than the intended skip.
            print(f"  SKIP  could not fetch the test face ({exc})")

    if not FACE_CACHE.exists():
        # A skip here means the ONLY fixture that exercises YuNet, the
        # detection-against-ground-truth score and the face-cache-key checks
        # did not run — and `report()` counts passes and failures, not skips,
        # so the suite exits 0 and reads as a clean run. Offline that is the
        # right behaviour; on CI it is the job silently not testing the feature
        # it exists for, so CI sets QATF_REQUIRE_FIXTURES and gets a failure.
        if os.environ.get("QATF_REQUIRE_FIXTURES"):
            check("fixture B ran (QATF_REQUIRE_FIXTURES is set)", False,
                  f"the test face is unavailable at {FACE_CACHE} and could not "
                  f"be fetched — the whole stage-4b detection path was skipped")
    else:
        FACE_W = 300
        # the detector put the face centre at 0.4729 of the headshot's width
        face_true = lambda t: (250.0 + 55.0 * t + FACE_W * 0.4729) / SRC_W   # noqa: E731
        face_vid = WORK / "b.mp4"
        ff("-f", "lavfi", "-i", f"color=c=0x203040:s={SRC_W}x{SRC_H}:d={DUR}:r=30",
           "-loop", "1", "-i", str(FACE_CACHE),
           "-filter_complex",
           f"[1:v]scale={FACE_W}:-1[f];"
           f"[0:v][f]overlay=x='250+55*t':y=120:eval=frame:shortest=1",
           "-t", str(DUR), "-c:v", "libx264", "-preset", "veryfast",
           "-pix_fmt", "yuv420p", str(face_vid))

        clip_b = Clip(0.0, DUR, "face")
        dets_b, detector = detect.detections_for(face_vid, [clip_b], WORK / "b",
                                                 tier="balanced")
        check("the detector found the face", len(dets_b) > 0, f"{len(dets_b)} detections")
        if dets_b:
            worst = max(abs(d.cx - face_true(d.t)) for d in dets_b if d.t <= DUR)
            check("detected position matches ground truth", worst < 0.02,
                  f"worst error {worst:.4f} of frame width")

        # The cache is keyed to the video, so a second call must not re-detect.
        # Counting detections cannot show that — detection is deterministic, so
        # the counts match whether the cache was consulted or ignored. Count the
        # calls instead: this check has to be able to fail, which is the same
        # rule the crop control in `score` is held to.
        _real_detect_span = detect.detect_span
        _calls = []
        detect.detect_span = lambda *a, **k: (_calls.append(1)
                                              or _real_detect_span(*a, **k))
        try:
            again, _ = detect.detections_for(face_vid, [clip_b], WORK / "b",
                                             tier="balanced")
        finally:
            detect.detect_span = _real_detect_span
        check("the face cache is reused rather than recomputed",
              not _calls and len(again) == len(dets_b),
              f"{len(_calls)} detector runs on the second call")

        # A different video must NOT inherit these detections. Same work dir,
        # same tier — only the footage differs, which is exactly the collision
        # the cache key was missing.
        other = WORK / "b-other.mp4"
        ff("-f", "lavfi", "-i", f"color=c=0x101010:s={SRC_W}x{SRC_H}:d=2:r=30",
           "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", str(other))
        check("a second video in the same work directory gets its own cache",
              detect.cache_key(other, detector, "balanced")
              != detect.cache_key(face_vid, detector, "balanced"))

        track_b = framing.solve(dets_b, clip_b, crop_w, detector=detector,
                                tier="balanced")
        check("the solved track covers the clip",
              not track_b.fallback and track_b.coverage > 0.8, str(track_b.coverage))
        t_b, c_b = render_pair(face_vid, clip_b, track_b, "b")
        score("real face", [face_centre(t_b, t) for t in PROBES],
              [face_centre(c_b, t) for t in PROBES])

print(f"\nartifacts left in {WORK} — open the two .mp4 pairs to look at them")
sys.exit(report())
