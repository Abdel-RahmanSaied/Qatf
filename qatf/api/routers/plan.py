"""Transcript and plan — the hand-edit round trip.

`--plan-only` used to write a plan.json that nothing could read back. This is
the endpoint pair that makes reviewing and editing a plan real.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status

from ... import pipeline
from ...core.errors import EmptyPlan, NoTranscript
from ...core.types import clips_from_dicts, clips_to_dicts
from ...jobs import JobState, JobStore
from ..deps import get_store, reject_if_running, require_job, to_response
from ..openapi import NO_PLAN, NO_TRANSCRIPT, NOT_FOUND, RUNNING, merge
from ..schemas import (
    ClipModel,
    JobOptions,
    JobResponse,
    PlanUpdate,
    TranscriptResponse,
)

router = APIRouter(prefix="/jobs/{job_id}", tags=["plan"])


@router.get(
    "/transcript",
    response_model=TranscriptResponse,
    operation_id="getTranscript",
    summary="Word-level transcript",
    response_description="Every word with its start and end, plus the detected language.",
    responses=merge(NOT_FOUND, NO_TRANSCRIPT),
)
def get_transcript(job_id: str, store: JobStore = Depends(get_store)) -> TranscriptResponse:
    """The cached transcript, once stage 2 has run.

    These word timings are the only acoustic truth in the system — stage 4 snaps
    every cut point onto one of them. If a clip opens mid-syllable, this is where
    to look first, not the plan.

    Read from `<work>/words-<model>-<lang>.json`. The cache key includes the
    Whisper size and the forced language, so `?language=ar` after an English run
    re-transcribes rather than silently reusing the wrong transcript. Fixups are
    deliberately *not* in the key: they are applied on read, so editing them
    never orphans the cache.
    """
    job = require_job(store, job_id)
    transcript = store.transcript_for(job)
    if transcript is None:
        raise NoTranscript("no transcript yet")
    return TranscriptResponse(
        language=transcript.language,
        language_probability=transcript.language_probability,
        word_count=len(transcript.words),
        words=[w.__dict__ for w in transcript.words],
    )


@router.get(
    "/plan",
    response_model=list[ClipModel],
    operation_id="getPlan",
    summary="The current plan",
    response_description="Clips in output order, boundaries already snapped.",
    responses=merge(NOT_FOUND, {404: {"description": "the job has no plan yet"}}),
)
def get_plan(job_id: str, store: JobStore = Depends(get_store)) -> list[ClipModel]:
    """What stage 3 picked, after stage 4 snapped it.

    Available from `state: planned` onwards. Durations here can exceed `max_len`
    by a couple of seconds — snapping moves boundaries outward to the nearest
    word edge, and that is the intended behaviour, not drift.
    """
    job = require_job(store, job_id)
    if not job.clips:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no plan yet")
    return [ClipModel(**c) for c in job.clips]


@router.put(
    "/plan",
    response_model=list[ClipModel],
    operation_id="replacePlan",
    summary="Replace the plan with a hand-edited one",
    response_description="The stored plan — re-snapped, so the boundaries you get "
                         "back will differ slightly from the ones you sent.",
    responses=merge(NOT_FOUND, RUNNING, NO_TRANSCRIPT),
)
def put_plan(job_id: str, body: PlanUpdate,
             store: JobStore = Depends(get_store)) -> list[ClipModel]:
    """Replace the plan wholesale, then `POST /render`.

    **Leave `snap` on.** With it true (the default) your edited boundaries are
    moved back onto real Whisper word times. Hand-typed seconds are semantic
    guesses exactly like the model's, and skipping the snap is how clips end up
    opening mid-syllable. Turn it off only when you are deliberately setting
    acoustic cut points yourself.

    There is no partial update — send the clips you want, in the order you want
    them numbered. The job moves back to `planned`, so a previously-rendered job
    can be re-cut without re-transcribing.
    """
    job = require_job(store, job_id)
    reject_if_running(job)

    clips = clips_from_dicts([c.model_dump() for c in body.clips])
    if body.snap:
        transcript = store.transcript_for(job)
        if transcript is None:
            raise NoTranscript("cannot snap: no transcript for this job yet")
        clips = [pipeline.snap(c, transcript.words) for c in clips]

    updated = store.update(job_id, clips=clips_to_dicts(clips),
                           state=JobState.planned.value, error=None,
                           message="plan replaced — POST /render to encode")
    return [ClipModel(**c) for c in updated.clips]


@router.post(
    "/render",
    response_model=JobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="renderPlan",
    summary="Encode the current plan",
    response_description="The job, in state `queued`. Poll `GET /jobs/{job_id}`.",
    responses=merge(NOT_FOUND, RUNNING, NO_PLAN),
)
def render_job(job_id: str,
               options: JobOptions | None = Body(
                   None,
                   description="optional full replacement of the job's options. "
                               "Only the stage-5 fields take effect here "
                               "(reframe, codec, resolution, ten_bit, crf, font, "
                               "captions, per_line) — the transcript and the plan "
                               "already exist.",
               ),
               store: JobStore = Depends(get_store)) -> JobResponse:
    """Stage 5 alone, over whatever plan the job currently holds.

    No model call and no re-transcription, which is what makes iterating on
    framing, font or codec cheap: `PUT` a plan, render, look at it, render again
    at a different `crf`.

    **Re-rendering deletes the previous clips first.** Download anything you want
    to keep before calling this again.

    Passing `options` replaces the job's options object wholesale — send the full
    set, not a patch.
    """
    job = require_job(store, job_id)
    reject_if_running(job)
    if not job.clips:
        raise EmptyPlan("job has no plan to render")
    if options is not None:
        store.update(job_id, options=options.model_dump())
    store.submit_render(job_id)
    return to_response(store, require_job(store, job_id))
