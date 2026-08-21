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
    #: stage 0 — downloading a URL source. Its own state rather than folded into
    #: `extracting` because it is the one stage whose duration depends on someone
    #: else's network, and a client watching a job sit still deserves to know
    #: whether it is waiting on YouTube or on ffmpeg.
    fetching = "fetching"
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
    JobState.queued, JobState.fetching, JobState.extracting, JobState.transcribing,
    JobState.selecting, JobState.rendering,
})

#: the same set as bare strings, for comparing against `Job.state`
RUNNING_STATE_VALUES = frozenset(s.value for s in RUNNING_STATES)


@dataclass
class Job:
    id: str
    video: str
    source: str                      # "upload" | "path" | "youtube"
    options: dict
    #: the URL a `source="youtube"` job was created from. Kept so the job record
    #: says where the media came from after stage 0 has replaced `video` with a
    #: local path — otherwise a finished job cannot answer "which video was
    #: this?" once the download is deleted. Empty for the other two sources.
    url: str = ""
    #: which `transcripts` row this job's transcript actually landed in.
    #:
    #: Recorded rather than re-derived, because there are now TWO key shapes —
    #: `words-<model>-<lang>-<seed>` for Whisper and `subs-<lang>-<track>` for a
    #: caption track — and which one a job used depends on a runtime decision
    #: (were captions available?) that no amount of reading `options` can
    #: reconstruct. A job that fell back from captions to Whisper would
    #: otherwise be looked up under the key it did not use, and
    #: `GET /transcript` would answer 409 on a job that plainly has one.
    #:
    #: Empty on records written before this field existed; `transcript_for`
    #: falls back to deriving the Whisper key, which is what those jobs used.
    transcript_key: str = ""
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
    #: stage 0 download progress, as `{"downloaded_bytes", "total_bytes",
    #: "file_index"}` — or None on a job that never fetched.
    #:
    #: None rather than a zeroed dict on purpose: "this job downloaded nothing"
    #: and "this job has downloaded 0 bytes so far" are different facts, and only
    #: a `source="youtube"` job can ever be in the second. A client that cannot
    #: tell them apart draws an empty progress bar on an upload.
    #:
    #: A plain dict for the same reason `clips` is: the record round-trips
    #: through `asdict`/`json.dumps`, and a nested dataclass buys nothing there.
    #: Absent on records written before this field existed, which read as None.
    fetch_progress: dict | None = None
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
