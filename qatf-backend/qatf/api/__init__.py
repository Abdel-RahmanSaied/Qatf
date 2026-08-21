"""FastAPI front end over the qatf pipeline.

Nothing in this package contains pipeline logic — it drives `qatf.jobs`, which
drives `qatf.pipeline`.

Run:
    uvicorn qatf.api:app --reload
    python -m qatf.api            # reads QATF_HOST / QATF_PORT
    qatf-serve                    # if installed

Config is `qatf.config.Settings`; `create_app(settings=...)` takes an explicit
one so tests can point at a scratch directory without touching os.environ.

The OpenAPI description below is long on purpose. This API hands out job ids for
work that takes minutes, and the two things a new caller gets wrong — polling
without understanding the state machine, and hand-editing a plan without
re-snapping — are both invisible until the output is wrong. `/docs` is where
they will look, so that is where the warnings live.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .. import __version__
from ..core.config import Settings
from ..core.dotenv import find_and_load
from ..core.errors import QatfError
from ..core.utils import prime_ffmpeg_probe
from ..jobs import JobStore
from . import routers
from .schemas import ErrorResponse

__all__ = ["app", "create_app"]


DESCRIPTION = """
Turn a long video into vertical short-form clips.

A model picks the moments. Everything about **where the cuts land** is
deterministic — and that split is the whole design.

---

## The five stages

| # | Stage | Engine | Model? |
|---|---|---|---|
| 1 | demux to 16 kHz mono wav | ffmpeg | no |
| 2 | transcribe with word timings | faster-whisper | no |
| 3 | pick the clips | your chosen LLM | **yes** |
| 4 | snap cuts to word boundaries | deterministic | no |
| 5 | reframe, caption, encode | ffmpeg | no |

Only stage 3 is a model call. Because stages 1, 4 and 5 are model-free,
**changing provider cannot affect cut accuracy or rendering** — it changes only
*which passages* get picked.

## The core invariant

The model never emits precise timing. It reads the transcript in ~12-second
blocks and returns `MM:SS`. Stage 4 then moves those onto real word start/end
times from Whisper.

> Semantic boundaries come from the model. Acoustic boundaries come from the
> audio. **They are never mixed.**

This applies to hand-edited plans too, which is why `PUT /jobs/{id}/plan`
re-snaps by default. A human typing `"start": 20.0` is making the same kind of
semantic guess the model makes — and skipping the snap is how a clip ends up
opening mid-syllable.

## Working with jobs

Nothing here is synchronous. Every start returns **202** and a job id; poll
`GET /jobs/{id}` and read `state`.

```
queued → extracting → transcribing → selecting → planned → rendering → done
                                                     ↑         │
                                         PUT /plan ──┘         ↓
                                                          failed · cancelled
```

Set `auto_render: false` to stop at `planned`, review the plan, `PUT` an edited
one, then `POST /jobs/{id}/render`. Re-rendering needs no model call and no
re-transcription, which is what makes the round trip cheap.

## Before you submit an hour of audio

Call `GET /healthz`. It reports whether ffmpeg is genuinely on `PATH`, whether
the stage-3 provider has a usable credential, and **which device stage 2 will
actually use**. A missing API key otherwise surfaces only after transcription
has already run.

## Three things this job model does not do

* **Jobs do not survive a restart.** State is a JSON file per job, but the
  worker is an in-process thread pool. On startup anything left running is
  marked `failed: interrupted by a server restart`.
* **Cancellation is cooperative.** The flag is checked between stages and
  between clips. It cannot interrupt an ffmpeg or Whisper call already in
  flight.
* **`QATF_WORKERS` defaults to 1.** Two concurrent `large-v3` loads fight over
  the same GPU. Raise it only for render-only work.

## Errors

Domain failures carry their own status code and always come back as
`{"detail": "..."}`. There is no per-case mapping in the routers — one handler
translates the whole `QatfError` hierarchy, which is why HTTP concerns stay out
of the pipeline entirely.

| Status | Means |
|---|---|
| `403` | the path escaped `QATF_MEDIA_ROOT` — a security boundary, not a typo check |
| `404` | no such job, no plan yet, no such clip |
| `409` | the job is running, or you asked for something that needs a transcript first |
| `413` | upload exceeds `QATF_MAX_UPLOAD_MB`, or the transcript exceeds the context |
| `415` | unsupported video extension |
| `422` | validation failed, no speech was found, or the vocabulary seed is too long |
| `502` | stage 3 returned something that was not the requested JSON, or refused |
| `503` | ffmpeg is missing, or the stage-3 provider has no credential |

A `500` means a bug: every deliberate failure in this project is a `QatfError`
subclass, so anything else escaping the pipeline was not thought through.
"""

TAGS = [
    {
        "name": "meta",
        "description": "Readiness and capability reporting. Call `/healthz` "
                       "**before** submitting work — it is the only place that "
                       "tells you which device stage 2 will pick and whether "
                       "stage 3 has a credential.",
    },
    {
        "name": "jobs",
        "description": "Create, list, inspect, cancel and delete jobs. Creation "
                       "returns `202` and a job id; the pipeline takes minutes, "
                       "so poll `GET /jobs/{job_id}`.",
    },
    {
        "name": "plan",
        "description": "The transcript and the hand-edit round trip. Review the "
                       "plan, replace it, re-render — no model call, no "
                       "re-transcription. `PUT /plan` re-snaps by default and "
                       "you should leave it that way.",
    },
    {
        "name": "settings",
        "description": "Editable server settings — the stage-3 provider, model "
                       "and base URL, plus `workers`. A saved value overrides "
                       "the matching `QATF_*` variable and applies to the NEXT "
                       "job; a job already running keeps the settings it "
                       "started with. API keys are never stored or returned.",
    },
    {
        "name": "outputs",
        "description": "List and download rendered clips. Download names are "
                       "resolved inside the job's own output directory, so "
                       "traversal is rejected.",
    },
]


def create_app(settings: Settings | None = None) -> FastAPI:
    if settings is None:
        find_and_load()          # .env before reading the environment
        settings = Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.store = JobStore(settings.data_dir, max_workers=settings.workers,
                                   settings=settings)
        # Probing ffmpeg spawns a process. Do it once here so no request ever
        # pays for it — /healthz is polled far harder than anything else and
        # measured p99 > 1s when it probed inline. See core.utils.
        prime_ffmpeg_probe()
        yield
        app.state.store.shutdown()

    app = FastAPI(
        title="qatf",
        version=__version__,
        summary="Harvest the parts of a long video worth watching.",
        description=DESCRIPTION,
        openapi_tags=TAGS,
        # Served over the network, so this is the one licence declaration a
        # generated client or a scanner reads instead of a file. It said MIT
        # while everything else said Apache-2.0; smoke_api.py now pins it.
        license_info={"name": "Apache-2.0", "identifier": "Apache-2.0"},
        lifespan=lifespan,
        swagger_ui_parameters={
            # the operation list is short enough to show whole, and a caller
            # landing here is usually looking for one specific endpoint
            "docExpansion": "list",
            "filter": True,
            "tryItOutEnabled": True,
            # stage 2 and 5 take minutes; seeing the elapsed time on a poll is
            # the difference between "hung" and "working"
            "displayRequestDuration": True,
            "defaultModelsExpandDepth": 2,
            "defaultModelRendering": "example",
            "persistAuthorization": True,
            "syntaxHighlight.theme": "obsidian",
        },
    )
    # also set outside the lifespan so `create_app()` is inspectable without
    # starting the app (route listing, schema generation, tooling)
    app.state.settings = settings

    @app.exception_handler(QatfError)
    async def _domain_error(request: Request, exc: QatfError) -> JSONResponse:
        """Domain errors carry their own status code, so routers can raise a
        meaningful exception instead of hand-mapping every case to HTTPException."""
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _validation_error(request: Request,
                                exc: RequestValidationError) -> JSONResponse:
        """Report where the body was wrong and why — never what was sent.

        FastAPI's default 422 embeds the offending input verbatim, which is wrong
        here twice over:

        * **It can fail to serialise.** A body containing `1e999` is rejected
          correctly, then the error detail holds an `inf`, and encoding *that*
          raises — so a crafted body turns a clean 422 into an unhandled 500.
        * **It echoes caller input back.** Combined with a client that renders
          `detail` as HTML, that is a reflected-content bug this API has no
          reason to hand anyone.

        It also makes the documented contract true: `ErrorResponse` declares
        `detail` as a string, and the default 422 returned a list of objects —
        so a generated client broke on the most common failure of all.
        """
        problems = "; ".join(
            f"{'.'.join(str(p) for p in err['loc'][1:]) or 'body'}: {err['msg']}"
            for err in exc.errors()
        )
        return JSONResponse(status_code=422,
                            content={"detail": problems or "invalid request body"})

    for router in routers.ALL:
        # every route can 422, and the shape is the one above — not FastAPI's
        # default HTTPValidationError, which this app does not emit
        app.include_router(router, responses={
            422: {"model": ErrorResponse, "description": "the request failed validation"},
        })

    return app


app = create_app()
