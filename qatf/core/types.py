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
