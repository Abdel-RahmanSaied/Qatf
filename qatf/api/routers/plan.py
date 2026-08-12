"""Transcript and plan — the hand-edit round trip.

`--plan-only` used to write a plan.json that nothing could read back. This is
the endpoint pair that makes reviewing and editing a plan real.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, status

from ... import pipeline
from ...core.errors import EmptyPlan, NoTranscript
from ...core.types import Word, clips_from_dicts, clips_to_dicts, words_from_dicts
from ...jobs import JobState, JobStore
from ...jobs.worker import baseline_words, caption_words
from ..deps import get_store, reject_if_running, require_job, to_response
from ..openapi import NO_PLAN, NO_TRANSCRIPT, NOT_FOUND, RUNNING, TIMING_LOCKED, merge
from ..schemas import (
    ClipModel,
    JobOptions,
    JobResponse,
    PlanUpdate,
    TranscriptResponse,
    TranscriptUpdate,
)

router = APIRouter(prefix="/jobs/{job_id}", tags=["plan"])


def _payload(transcript, words: list[Word], applied: int,
             stale: int) -> TranscriptResponse:
    return TranscriptResponse(
        language=transcript.language,
        language_probability=transcript.language_probability,
        word_count=len(words),
        edits_applied=applied,
        edits_stale=stale,
        words=[w.__dict__ for w in words],
    )


def _read(store: JobStore, job) -> TranscriptResponse:
    """The transcript as it will be captioned, read fresh from the cache."""
    transcript = store.transcript_for(job)
    if transcript is None:
        raise NoTranscript("no transcript yet")
    words, _blanked, applied, stale = caption_words(transcript, job.options,
                                                     job.work_dir(store.root))
    return _payload(transcript, words, applied, stale)


@router.get(
    "/transcript",
    response_model=TranscriptResponse,
    operation_id="getTranscript",
    summary="Word-level transcript, as it will be captioned",
    response_description="Every word with its start and end, plus the detected language.",
    responses=merge(NOT_FOUND, NO_TRANSCRIPT),
)
def get_transcript(job_id: str, store: JobStore = Depends(get_store)) -> TranscriptResponse:
    """The transcript once stage 2 has run, with fixups and per-word corrections
    already applied — so what you read here is what gets burned in.

    These word timings are the only acoustic truth in the system: stage 4 snaps
    every cut point onto one of them. If a clip opens mid-syllable, this is where
    to look first, not the plan. Each word's `start` is also how you find it in
    the audio — scrub there, hear what was actually said, then correct it with
    `PUT`.

    **Some words may come back with an empty `text`.** `pipeline.health.repair`
    blanks a decoder repetition loop (four or more identical tokens in a row)
    rather than deleting it, so the word count and every `start`/`end` stay put
    — deleting would break `PUT`'s position-keyed overlay and its own refusal of
    a word-count change. A blank renders as nothing (`captions.group_words`
    skips it), but it is still a real element of `words`: `PUT` must echo it
    back unchanged like any other word, or it trips the same word-count/timing
    contract as an accidental deletion would.

    Read from `<work>/words-<model>-<lang>.json`. The cache key includes the
    Whisper size and the forced language, so `?language=ar` after an English run
    re-transcribes rather than silently reusing the wrong transcript. Fixups and
    corrections are deliberately *not* in the key: they are applied on read, so
    editing either never orphans the cache and never rewrites what Whisper
    actually produced.
    """
    return _read(store, require_job(store, job_id))


@router.put(
    "/transcript",
    response_model=TranscriptResponse,
    operation_id="correctTranscript",
    summary="Correct misheard words",
    response_description="The transcript with the corrections applied, and how "
                         "many took effect.",
    responses=merge(NOT_FOUND, RUNNING, NO_TRANSCRIPT, TIMING_LOCKED),
)
def put_transcript(job_id: str, body: TranscriptUpdate,
                   store: JobStore = Depends(get_store)) -> TranscriptResponse:
    """Fix words Whisper heard wrong. Then `POST /render`.

    Send the whole word list back with `text` changed where it is wrong. This is
    a **wholesale replace**: the submission is diffed against the transcript and
    stored as an overlay, so re-submitting the untouched transcript clears every
    correction.

    **Only `text` may differ.** Word count and every `start`/`end` must match
    exactly; anything else is `422`. That refusal is the core invariant enforced
    at the boundary — a correction can change what a caption reads and can never
    move a cut. It is also what makes this cheap: the cut points are provably
    identical, so only stage 5 has to run again. No model call, no
    re-transcription.

    This is the tool for the errors `fixups` cannot reach. A global substitution
    fixes a term the decoder always mishears the same way; it is useless when the
    misheard word is common and correct elsewhere — `من` for `مين` cannot be
    replaced globally without wrecking every other `من` in the file. Corrections
    are keyed by position, so they touch that one word.

    Corrections are stored beside the transcript cache, never inside it:
    re-transcribing must not silently discard them, and the cache has to keep
    saying what Whisper actually produced. They carry the text they replaced, so
    if the transcript later moves underneath them the correction goes **stale**
    and is reported in `edits_stale` rather than landing on the wrong word.
    """
    job = require_job(store, job_id)
    reject_if_running(job)

    transcript = store.transcript_for(job)
    if transcript is None:
        raise NoTranscript("no transcript to correct yet")

    baseline, _blanked = baseline_words(transcript, job.options)
    submitted = words_from_dicts([w.model_dump() for w in body.words])
    corrections = pipeline.edits.diff(baseline, submitted)

    work = job.work_dir(store.root)
    pipeline.edits.save(pipeline.edits.path(work), corrections)

    fields: dict = {"message": f"transcript corrected ({len(corrections)} word(s))"
                               " — POST /render to re-encode"}
    if job.state == JobState.done.value:
        # the rendered clips now caption text nobody will see again. Saying
        # `done` while that is true would be a lie the client cannot detect.
        fields["state"] = JobState.planned.value
    store.update(job_id, **fields)

    # Build the response from the baseline already in hand rather than re-reading
    # and re-parsing the cache. `baseline` carries the job's fixups; layering the
    # corrections we just stored on top of it produces exactly the words a render
    # would use. On a 3-hour transcript the second parse was ~16ms and a 1.6 MB
    # file read, for a result we could already derive.
    words, applied, stale = pipeline.edits.apply(baseline, corrections)
    return _payload(transcript, words, applied, len(stale))


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
                               "(reframe, codec, preset, resolution, ten_bit, "
                               "crf, font, captions, per_line) — the transcript "
                               "and the plan already exist.",
               ),
               store: JobStore = Depends(get_store)) -> JobResponse:
    """Stage 5 alone, over whatever plan the job currently holds.

    No model call and no re-transcription, which is what makes iterating on
    framing, font or codec cheap: `PUT` a plan, render, look at it, render again
    at a different `crf` or a faster `preset`.

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
