"""Rendered clip listing and download."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path
from fastapi.responses import FileResponse

from ...jobs import JobStore
from ..deps import get_store, require_job, safe_output_path, to_response
from ..openapi import NOT_FOUND
from ..schemas import ClipOutput

router = APIRouter(prefix="/jobs/{job_id}/clips", tags=["outputs"])


@router.get(
    "",
    response_model=list[ClipOutput],
    operation_id="listClips",
    summary="List rendered clips",
    response_description="One entry per rendered file, with a ready-to-GET url.",
    responses=NOT_FOUND,
)
def list_clips(job_id: str, store: JobStore = Depends(get_store)) -> list[ClipOutput]:
    """The clips rendered so far.

    Populated incrementally during `state: rendering` — a partial list means the
    job is still working, not that it produced fewer clips than planned. It is
    final once the job reaches `done`.

    Names come from `slugify(clip.title)`, which is ASCII-only. An all-Arabic
    title therefore yields `02-clip.mp4` rather than a transliteration.
    """
    return to_response(store, require_job(store, job_id)).outputs


@router.get(
    "/{name}",
    operation_id="downloadClip",
    summary="Download one clip",
    response_class=FileResponse,
    response_description="The MP4.",
    responses={
        200: {
            "content": {"video/mp4": {}},
            "description": "The rendered clip, as a file download.",
        },
        **NOT_FOUND,
    },
)
def download_clip(job_id: str,
                  name: str = Path(..., description="filename from `GET /clips`",
                                   examples=["01-php-lsh-ayshh.mp4"]),
                  store: JobStore = Depends(get_store)) -> FileResponse:
    """Stream one rendered clip.

    `name` is resolved inside the job's own output directory and must still land
    there, so `..` and absolute paths are rejected as `404` rather than reaching
    the filesystem.
    """
    job = require_job(store, job_id)
    path = safe_output_path(job, store.root, name)
    return FileResponse(path, media_type="video/mp4", filename=path.name)
