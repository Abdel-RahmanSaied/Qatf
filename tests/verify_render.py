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

import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

from _harness import check, report, section

from qatf.core.types import Clip, Detection
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
    subprocess.run(["ffmpeg", "-y", "-v", "error", *args], check=True)


def raw_frame(video: Path, t: float) -> bytes | None:
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{t}", "-i", str(video), "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
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


if shutil.which("ffmpeg") is None:
    print("ffmpeg not on PATH — this suite renders, so there is nothing to do")
    raise SystemExit(0)

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
                FACE_CACHE.write_bytes(resp.read())
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"  SKIP  could not fetch the test face ({exc})")

    if not FACE_CACHE.exists():
        pass
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

        # the cache is keyed to the video, so a second call must not re-detect
        again, _ = detect.detections_for(face_vid, [clip_b], WORK / "b", tier="balanced")
        check("the face cache is reused rather than recomputed",
              len(again) == len(dets_b))

        track_b = framing.solve(dets_b, clip_b, crop_w, detector=detector,
                                tier="balanced")
        check("the solved track covers the clip",
              not track_b.fallback and track_b.coverage > 0.8, str(track_b.coverage))
        t_b, c_b = render_pair(face_vid, clip_b, track_b, "b")
        score("real face", [face_centre(t_b, t) for t in PROBES],
              [face_centre(c_b, t) for t in PROBES])

print(f"\nartifacts left in {WORK} — open the two .mp4 pairs to look at them")
sys.exit(report())
