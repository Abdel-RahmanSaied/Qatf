"""Shared dependencies and response mapping for the routers.

No pipeline logic. Anything here is either plumbing or a security boundary.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException, Request, status

from ..core.config import Settings
from ..core.errors import SourceNotFound, SourceOutsideMediaRoot
from ..jobs import RUNNING_STATES, Job, JobState, JobStore
from .schemas import ClipModel, ClipOutput, JobOptions, JobResponse


def get_store(request: Request) -> JobStore:
    return request.app.state.store


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def resolve_source(raw: str, settings: Settings) -> Path:
    """Resolve a caller-supplied path inside the media root.

    This is a security boundary, not a convenience. Without it a POST body
    naming ../../etc/passwd would have the server transcribe any file the
    process can read. Absolute paths must still land inside the root."""
    root = settings.media_root
    candidate = Path(raw)
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not resolved.is_relative_to(root):
        raise SourceOutsideMediaRoot(f"path must be inside QATF_MEDIA_ROOT ({root})")
    if not resolved.is_file():
        raise SourceNotFound(f"no such file: {raw}")
    return resolved


def require_job(store: JobStore, job_id: str) -> Job:
    job = store.get(job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such job: {job_id}")
    return job


def reject_if_running(job: Job) -> None:
    if JobState(job.state) in RUNNING_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"job is {job.state} — cancel it first")


def safe_output_path(job: Job, root: Path, name: str) -> Path:
    """Resolve a download name inside the job's own output directory."""
    out_dir = job.out_dir(root).resolve()
    path = (out_dir / name).resolve()
    if not path.is_relative_to(out_dir) or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"no such clip: {name}")
    return path


def to_response(store: JobStore, job: Job) -> JobResponse:
    out_dir = job.out_dir(store.root)
    outputs = []
    for name in job.outputs:
        path = out_dir / name
        outputs.append(ClipOutput(
            name=name,
            size_bytes=path.stat().st_size if path.exists() else 0,
            url=f"/jobs/{job.id}/clips/{name}",
        ))
    return JobResponse(
        id=job.id,
        state=JobState(job.state),
        message=job.message,
        error=job.error,
        video=job.video,
        source=job.source,
        options=JobOptions(**job.options),
        created_at=job.created_at,
        updated_at=job.updated_at,
        language=job.language,
        device=job.device,
        word_count=job.word_count,
        transcript_cached=job.transcript_cached,
        clips=[ClipModel(**c) for c in job.clips],
        outputs=outputs,
    )
