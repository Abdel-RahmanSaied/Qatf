"""Job lifecycle: create, list, inspect, cancel, delete.

The pipeline takes minutes, so creation returns 202 and a job id. Clients poll
GET /jobs/{id}.

Each route carries an explicit `operation_id`. FastAPI derives one from the
function name and path otherwise (`create_from_path_jobs_post`), which is what a
generated client's method ends up called — worth naming deliberately.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from starlette.concurrency import run_in_threadpool

from ...core.config import Settings
from ...core.constants import VIDEO_SUFFIXES
from ...core.errors import UnsupportedSource, UploadTooLarge
from ...jobs import RUNNING_STATES, JobState, JobStore
from ..deps import (
    get_settings,
    get_store,
    reject_if_running,
    require_job,
    resolve_source,
    to_response,
)
from ..openapi import (
    BAD_MEDIA,
    BAD_OPTIONS,
    NOT_FOUND,
    OUTSIDE_ROOT,
    RUNNING,
    SOURCE_MISSING,
    TOO_LARGE,
    merge,
)
from ..schemas import JobCreate, JobList, JobOptions, JobResponse

router = APIRouter(prefix="/jobs", tags=["jobs"])

UPLOAD_CHUNK = 1 << 20


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createJob",
    summary="Start a job from a server-side path",
    response_description="The job, in state `queued`. Poll `GET /jobs/{job_id}`.",
    responses=merge(OUTSIDE_ROOT, SOURCE_MISSING),
)
def create_from_path(body: JobCreate,
                     store: JobStore = Depends(get_store),
                     settings: Settings = Depends(get_settings)) -> JobResponse:
    """Start a job from a video already on the server.

    `path` resolves under `QATF_MEDIA_ROOT`. That is a **security boundary**, not
    a convenience: without it a body naming `../../etc/passwd` would have the
    server transcribe any file the process can read. Absolute paths are allowed
    but must still land inside the root.

    Returns immediately with `202`. The work happens on a background thread —
    read `state` and `message` from `GET /jobs/{job_id}` to follow it.

    Set `auto_render: false` to stop at `planned` and review the plan before
    spending render time.
    """
    video = resolve_source(body.path, settings)
    job = store.create(video, source="path", options=body.model_dump(exclude={"path"}))
    store.submit_pipeline(job.id)
    return to_response(store, job)


@router.post(
    "/upload",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="createJobFromUpload",
    summary="Start a job from an uploaded file",
    response_description="The job, in state `queued`. Poll `GET /jobs/{job_id}`.",
    responses=merge(BAD_MEDIA, TOO_LARGE, BAD_OPTIONS),
)
async def create_from_upload(
    file: UploadFile = File(..., description="source video"),
    options: str = Form(
        "{}",
        description="JobOptions as a **JSON string**, e.g. "
                    '`{"clips": 8, "language": "ar", "denoise": true}`. '
                    "A multipart body cannot carry a nested JSON object, so this "
                    "is a string field rather than the object it parses into.",
        examples=['{"clips": 8, "language": "ar", "denoise": true, "max_len": 52}'],
    ),
    store: JobStore = Depends(get_store),
    settings: Settings = Depends(get_settings),
) -> JobResponse:
    """Start a job from an uploaded video.

    Streamed to disk in 1 MB chunks and checked against `QATF_MAX_UPLOAD_MB` as
    it arrives, so an oversized upload is refused mid-stream rather than after
    the whole file has landed. A failed upload takes its job record with it —
    there is no half-written source left behind.
    """
    try:
        parsed = JobOptions(**json.loads(options or "{}"))
    except (json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"options is not valid JSON: {exc}") from exc

    name = Path(file.filename or "upload.mp4").name
    if Path(name).suffix.lower() not in VIDEO_SUFFIXES:
        raise UnsupportedSource(f"unsupported extension: {Path(name).suffix}")

    job = store.create(Path("<pending upload>"), source="upload",
                       options=parsed.model_dump())
    target = job.source_dir(store.root) / name
    target.parent.mkdir(parents=True, exist_ok=True)

    written = 0
    try:
        # `fh.write` is blocking, and this is an async endpoint — writing
        # directly would stall the event loop for the whole upload, so every
        # concurrent poll waits behind a 2 GB file. The threadpool hop costs
        # microseconds per megabyte and keeps the server answering.
        with target.open("wb") as fh:
            while chunk := await file.read(UPLOAD_CHUNK):
                written += len(chunk)
                if written > settings.max_upload_bytes:
                    raise UploadTooLarge(
                        f"upload exceeds QATF_MAX_UPLOAD_MB ({settings.max_upload_mb} MB)")
                await run_in_threadpool(fh.write, chunk)
    except BaseException:
        store.delete(job.id)          # no half-written source left behind
        raise
    finally:
        await file.close()

    job = store.update(job.id, video=str(target))
    store.submit_pipeline(job.id)
    return to_response(store, job)


@router.get(
    "",
    response_model=JobList,
    operation_id="listJobs",
    summary="List jobs",
    response_description="Jobs, newest first.",
)
def list_jobs(state: JobState | None = Query(
                  None,
                  description="return only jobs in this state",
                  examples=["done"]),
              store: JobStore = Depends(get_store)) -> JobList:
    """Every job the server knows about, newest first.

    Jobs are reloaded from disk at startup, so this survives a restart — but
    anything that was *running* at the time comes back as `failed: interrupted
    by a server restart`, because the thread pool holding it did not.
    """
    jobs = store.list(state=state.value if state else None)
    return JobList(jobs=[to_response(store, j) for j in jobs])


@router.get(
    "/{job_id}",
    response_model=JobResponse,
    operation_id="getJob",
    summary="Poll one job",
    response_description="The job's current state, plan and outputs.",
    responses=NOT_FOUND,
)
def get_job(job_id: str, store: JobStore = Depends(get_store)) -> JobResponse:
    """**This is the endpoint you poll.**

    `state` tells you where the pipeline is; `message` carries the human-readable
    step (`[2/5] transcribing with whisper large-v3 on cuda`). `outputs` grows
    during `rendering` rather than appearing all at once, so progress is visible
    clip by clip.

    Worth reading on the way past: `device` is what stage 2 **actually** used,
    which under `device: auto` may be `cpu` even on a GPU host — and
    `transcript_cached` tells you whether stage 2 ran at all.
    """
    return to_response(store, require_job(store, job_id))


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="deleteJob",
    summary="Delete a job and its files",
    response_description="Deleted. No body.",
    responses=merge(NOT_FOUND, RUNNING),
)
def delete_job(job_id: str, store: JobStore = Depends(get_store)) -> None:
    """Remove the job record, its work directory and its rendered clips.

    Refused with `409` while the job is running — cancel it first. Deleting the
    work directory takes the cached transcript with it, so a re-run transcribes
    from scratch.
    """
    job = require_job(store, job_id)
    reject_if_running(job)
    store.delete(job_id)


@router.post(
    "/{job_id}/cancel",
    response_model=JobResponse,
    operation_id="cancelJob",
    summary="Request cancellation",
    response_description="The job, with the cancel flag set. State changes to "
                         "`cancelled` when the worker next reaches a checkpoint.",
    responses=merge(NOT_FOUND, {409: {"description": "the job already finished"}}),
)
def cancel_job(job_id: str, store: JobStore = Depends(get_store)) -> JobResponse:
    """Cooperative cancel.

    The flag is checked **between stages and between clips**. It cannot interrupt
    an ffmpeg or Whisper call already running, so a cancel during stage 2 on a
    long file lands whenever transcription finishes — not straight away.

    The response comes back before the worker has necessarily noticed. Poll for
    `state: cancelled` to know it actually stopped.
    """
    job = require_job(store, job_id)
    if JobState(job.state) not in RUNNING_STATES:
        raise HTTPException(status.HTTP_409_CONFLICT, f"job is already {job.state}")
    store.request_cancel(job_id)
    return to_response(store, store.update(job_id, message="cancel requested"))
