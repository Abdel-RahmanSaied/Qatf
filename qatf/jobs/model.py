"""Job state and record.

`JobState` lives here rather than in `api.schemas` because the job lifecycle is a
domain concept, not a wire format — `jobs` must never import from `api`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobState(str, Enum):
    queued = "queued"
    extracting = "extracting"
    transcribing = "transcribing"
    selecting = "selecting"
    planned = "planned"          # plan ready, awaiting review or render
    rendering = "rendering"
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


#: states in which a worker holds the job. Nothing may mutate it here, and on
#: startup anything still in one of these is failed — no worker survived.
RUNNING_STATES = frozenset({
    JobState.queued, JobState.extracting, JobState.transcribing,
    JobState.selecting, JobState.rendering,
})

#: the same set as bare strings, for comparing against `Job.state`
RUNNING_STATE_VALUES = frozenset(s.value for s in RUNNING_STATES)


@dataclass
class Job:
    id: str
    video: str
    source: str                      # "upload" | "path"
    options: dict
    state: str = JobState.queued.value
    message: str = ""
    error: str | None = None
    language: str | None = None
    #: device transcription actually ran on, once stage 2 has resolved it
    device: str | None = None
    word_count: int = 0
    transcript_cached: bool = False
    clips: list[dict] = field(default_factory=list)
    outputs: list[str] = field(default_factory=list)
    #: output name -> size in bytes, recorded once when the clip is written.
    #:
    #: Deriving this on read instead cost one stat() per clip per job per
    #: request, which is 75% of `GET /jobs` and scales with the job count on the
    #: endpoint clients poll. A rendered file does not change size afterwards,
    #: so the size belongs to the write, not the read.
    #:
    #: Absent on records written before this field existed; `api.deps` falls
    #: back to stat() for those rather than reporting a wrong number.
    output_sizes: dict[str, int] = field(default_factory=dict)
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATE_VALUES

    # -- on-disk layout, all relative to the store root --------------------

    def dir(self, root: Path) -> Path:
        return root / self.id

    def work_dir(self, root: Path) -> Path:
        return self.dir(root) / ".work"

    def out_dir(self, root: Path) -> Path:
        return self.dir(root) / "clips"

    def source_dir(self, root: Path) -> Path:
        return self.dir(root) / "source"
