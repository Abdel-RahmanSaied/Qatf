"""Pipeline data types.

Plain dataclasses on purpose: the CLI must not need pydantic. The pydantic
mirrors of these live in `qatf.schemas` and exist only to describe the HTTP wire
format.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    """One word with acoustic boundaries from Whisper. These are the only
    timings in the system that are real — see the core invariant."""

    text: str
    start: float
    end: float


@dataclass
class Transcript:
    words: list[Word] = field(default_factory=list)
    language: str | None = None
    language_probability: float | None = None
    #: the device transcription actually ran on, which is not always the one
    #: requested — see `pipeline.asr.load_model`
    device: str | None = None
    compute_type: str | None = None

    def __len__(self) -> int:
        return len(self.words)

    def __bool__(self) -> bool:
        return bool(self.words)


@dataclass
class Detection:
    """One sampled observation from stage 4b — where a face was, and how much
    the audio says that face was the one talking.

    Coordinates are normalised to the source frame (0..1), never pixels: the
    cache then survives a resolution change, and the same detections drive a
    1080p render and a `source` render alike.

    `t` is absolute in the source video, because the cache is keyed to the video
    and has to stay valid when a plan edit moves every clip boundary."""

    t: float
    cx: float
    cy: float
    w: float
    h: float
    score: float = 0.0
    #: 0..1 from the active-speaker model. Always 0.0 on the `fast` tier, which
    #: runs no such model — `framing.subject` falls back to size there.
    speaking: float = 0.0


@dataclass
class Keyframe:
    """Where the crop window centre sits at time `t`.

    `t` is clip-relative, for the same reason ASS cue times are: `-ss` before
    `-i` is a fast seek and resets timestamps to 0. `cx` stays normalised to
    source width, so one track renders at any output resolution."""

    t: float
    cx: float


@dataclass
class Track:
    """A solved crop path for one clip — the output of stage 4c.

    Cached at `<work>/track-NN.json` and hand-editable, so a framing the solver
    got wrong is correctable without re-running the detector. `coverage` and
    `fallback` are how that review gets prioritised: a clip at 0.2 coverage is
    one to look at, and `fallback` means nothing was found at all and the render
    is a plain centre crop."""

    keyframes: list[Keyframe] = field(default_factory=list)
    detector: str = ""
    tier: str = ""
    #: fraction of sampled instants that produced a confident subject
    coverage: float = 0.0
    #: True when the whole clip fell back to centre — never an error, but worth
    #: surfacing rather than shipping fifty silently mis-framed clips
    fallback: bool = False

    def __bool__(self) -> bool:
        return bool(self.keyframes)


@dataclass
class Clip:
    """A proposed clip. `start`/`end` are semantic guesses until `snap` has run
    over them, whether they came from the model or from a human editing a plan."""

    start: float
    end: float
    title: str
    hook: str = ""
    why: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return self.end - self.start


def clips_to_dicts(clips: Iterable[Clip]) -> list[dict]:
    return [asdict(c) for c in clips]


def clips_from_dicts(data: Iterable[dict]) -> list[Clip]:
    """Rebuild a plan from JSON — the hand-edit round trip."""
    return [
        Clip(
            start=float(item["start"]),
            end=float(item["end"]),
            title=str(item.get("title", "clip")),
            hook=str(item.get("hook", "")),
            why=str(item.get("why", "")),
            score=float(item.get("score", 0.0)),
        )
        for item in data
    ]


def words_to_dicts(words: Iterable[Word]) -> list[dict]:
    return [asdict(w) for w in words]


def words_from_dicts(data: Iterable[dict]) -> list[Word]:
    return [Word(text=d["text"], start=float(d["start"]), end=float(d["end"]))
            for d in data]


def detections_to_dicts(dets: Iterable[Detection]) -> list[dict]:
    return [asdict(d) for d in dets]


def detections_from_dicts(data: Iterable[dict]) -> list[Detection]:
    """Rebuild the face cache. Tolerant of a missing `speaking`, because a cache
    written on the `fast` tier has no active-speaker column at all."""
    return [
        Detection(
            t=float(d["t"]),
            cx=float(d["cx"]),
            cy=float(d["cy"]),
            w=float(d["w"]),
            h=float(d["h"]),
            score=float(d.get("score", 0.0)),
            speaking=float(d.get("speaking", 0.0)),
        )
        for d in data
    ]


def track_to_dict(track: Track) -> dict:
    return asdict(track)


def track_from_dict(data: dict) -> Track:
    """Rebuild a track — the hand-edit round trip for framing.

    Nothing is validated here. `framing.sanitise` owns that, and it owns it
    because the risk is a crop window off the edge of the frame, not a bad
    request: the CLI reading an edited file has to be held to the same rule as
    a PUT."""
    return Track(
        keyframes=[Keyframe(t=float(k["t"]), cx=float(k["cx"]))
                   for k in data.get("keyframes", [])],
        detector=str(data.get("detector", "")),
        tier=str(data.get("tier", "")),
        coverage=float(data.get("coverage", 0.0)),
        fallback=bool(data.get("fallback", False)),
    )
