"""Reusable failure declarations for the OpenAPI schema.

FastAPI documents the happy path from the return annotation. Every failure has
to be declared by hand, and a status code documented on one route and forgotten
on the next is how a generated client ends up with no error type for a case it
will definitely hit.

So the declarations live here, once, and routers compose them:

    responses={**NOT_FOUND, **RUNNING}

The examples are real messages from `deps.py` and the `QatfError` hierarchy, not
invented ones — if a message changes, this file should change with it.
"""

from __future__ import annotations

from .schemas import ErrorResponse


def _error(description: str, example: str) -> dict:
    return {
        "model": ErrorResponse,
        "description": description,
        "content": {"application/json": {"example": {"detail": example}}},
    }


#: Job id, plan, or output file does not exist.
NOT_FOUND = {404: _error("no such job, plan or clip", "no such job: a1b2c3d4e5f6")}

#: Mutating a job while a worker holds it. Cancel first, and remember that
#: cancellation is cooperative — it lands between stages, not immediately.
RUNNING = {409: _error("the job is running — cancel it first",
                       "job is transcribing — cancel it first")}

#: Asked for a plan or a render before the pipeline produced one.
NO_PLAN = {409: _error("nothing to work with yet", "job has no plan to render")}

#: Cancelling a job no worker is holding any more. The mirror image of RUNNING —
#: that one refuses a mutation *because* a worker holds the job, this one refuses
#: a cancel because none does. Declared here rather than inline on the route: an
#: inline `{409: {"description": ...}}` carries no `model`, so it reaches the
#: schema with no `ErrorResponse` $ref and a generated client gets no error type
#: for a case every polling client will hit. That is exactly what this module
#: exists to prevent, and the one route that hand-rolled it was the one route
#: `smoke_api.py`'s single spot-check did not look at.
ALREADY_FINISHED = {409: _error("the job already finished",
                                "job is already done")}

#: Asked for the transcript before stage 2 finished, or asked to re-snap a
#: hand-edited plan on a job that never got one.
NO_TRANSCRIPT = {409: _error("no transcript for this job yet",
                             "cannot snap: no transcript for this job yet")}

#: The media-root sandbox. A caller-supplied path that resolves outside it is
#: refused whether it got there by `..` or by being absolute.
OUTSIDE_ROOT = {403: _error("path escaped QATF_MEDIA_ROOT",
                            "path must be inside QATF_MEDIA_ROOT (/srv/media)")}

#: Named a file the server cannot see. Distinct from OUTSIDE_ROOT on purpose:
#: one is a security refusal, the other is a typo.
SOURCE_MISSING = {404: _error("no such file under the media root",
                              "no such file: talks/keynote.mov")}

#: A caller-supplied URL failed the scheme/host allowlist. The URL twin of
#: OUTSIDE_ROOT, and 403 for the same reason: a refusal, not a typo.
BAD_URL = {403: _error("url is not an accepted host",
                       "only these hosts are accepted: m.youtube.com, "
                       "music.youtube.com, www.youtube.com, www.youtu.be, "
                       "youtu.be, youtube.com")}

#: The URL was accepted and the far end still would not serve it — private,
#: removed, region-locked, rate-limited.
NOT_FETCHABLE = {502: _error("the source could not be fetched",
                             "could not fetch the video: DownloadError")}

#: yt-dlp is not installed on this server.
NO_FETCHER = {503: _error("this server cannot fetch urls",
                          "fetching from a url needs yt-dlp — install it with "
                          "`pip install -e \".[youtube]\"`, or download the video "
                          "yourself and submit it by path or upload")}

TOO_LARGE = {413: _error("upload exceeds QATF_MAX_UPLOAD_MB",
                         "upload exceeds QATF_MAX_UPLOAD_MB (2048 MB)")}

BAD_MEDIA = {415: _error("unsupported video extension",
                         "unsupported extension: .txt")}

BAD_OPTIONS = {422: _error("options is not valid JSON",
                           "options is not valid JSON: Expecting value: line 1 column 1")}

#: A submitted transcript tried to change something other than word text. The
#: core invariant refusing at the boundary — see `pipeline/edits.py`.
TIMING_LOCKED = {422: _error(
    "the submission changed word count or timings",
    "word 1247 ('من') changed timing 204.290-204.580 -> 204.100-204.600. Word "
    "timings come from the audio and are not editable — they are what every cut "
    "point is snapped to.")}


def merge(*groups: dict) -> dict:
    """Combine response groups, joining descriptions that share a status code.

    Several distinct failures map to 409, and a dict merge would silently keep
    only the last one — so the caller would see "the job is running" documented
    on a route that also 409s for an empty plan."""
    out: dict[int, dict] = {}
    for group in groups:
        for status, spec in group.items():
            if status in out:
                out[status] = {**out[status],
                               "description": f"{out[status]['description']}; "
                                              f"{spec['description']}"}
            else:
                out[status] = dict(spec)
    return out
