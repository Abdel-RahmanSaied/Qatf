"""Request/response models for the HTTP API.

These are the wire contract only. Pipeline types live in `qatf.core.types` and
are deliberately kept as plain dataclasses so the CLI has no pydantic dependency.

`JobState` is re-exported from `qatf.jobs.model` rather than defined here: the
job lifecycle is a domain concept and `jobs` must never import from `api`. It
serialises correctly in responses because it is already a `str` Enum.

Every model carries a worked example. Those examples are the OpenAPI schema —
Swagger UI prefills "Try it out" from them, and client generators emit them as
fixtures — so they must stay *runnable*, not illustrative. A field description
that reads like documentation is doing its job twice: once in `/docs`, once in
the generated client's docstring.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..core.constants import CAPTION_MAX_WORDS
from ..jobs.model import RUNNING_STATE_VALUES, RUNNING_STATES, JobState

__all__ = [
    "JobState", "RUNNING_STATES", "RUNNING_STATE_VALUES",
    "JobOptions", "JobCreate", "ClipModel", "PlanUpdate", "WordModel",
    "TranscriptResponse", "ClipOutput", "JobResponse", "JobList",
    "ProviderInfo", "Health", "ErrorResponse",
]


class ErrorResponse(BaseModel):
    """Every failure on this API — domain error, validation error or 404 — comes
    back in this shape. `QatfError` subclasses carry their own status code and
    are mapped by a single exception handler, so routers never hand-build one."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{"detail": "path must be inside QATF_MEDIA_ROOT (/srv/media)"}],
    })

    detail: str = Field(..., description="human-readable cause")


class JobOptions(BaseModel):
    """Everything that controls a run. Every field has a working default, so
    `{"path": "talk.mov"}` is a complete request.

    Grouped by stage: selection (clips, min_len, max_len), transcription
    (whisper, device, language, denoise, hotwords, initial_prompt, fixups),
    captions (font, captions, per_line) and encode (reframe, codec, resolution,
    ten_bit, crf)."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "clips": 8,
            "min_len": 28,
            "max_len": 52,
            "language": "ar",
            "denoise": True,
            "hotwords": "بايثون جافاسكريبت باك اند فرونت اند ريأكت",
            "font": "Traditional Arabic",
            "resolution": "1080p",
            "codec": "h264",
        }],
    })

    clips: int = Field(5, ge=1, le=50, description="how many clips to ask stage 3 for")
    min_len: int = Field(30, ge=1, le=600,
                         description="seconds; clips shorter than this are dropped")
    max_len: int = Field(
        75, ge=1, le=600,
        description="seconds. Set 52, not 60, if you are targeting YouTube "
                    "Shorts: stage 4 snaps cuts onto word boundaries AFTER the "
                    "model picks them and routinely adds a few seconds.",
    )
    reframe: Literal["crop", "blur"] = Field(
        "crop",
        description="crop takes a centre slice and keeps ~3x the subject pixels; "
                    "blur letterboxes the whole frame over a blurred fill. Use "
                    "blur only when the framing genuinely needs the full width.",
    )
    codec: Literal["h264", "h265"] = Field(
        "h264",
        description="h265 is ~40% smaller at equal quality but slower to encode "
                    "and less universally accepted on upload.",
    )
    resolution: str = Field(
        "1080p",
        description="source | 1080p | 1440p | 4k | WxH. 'source' keeps the "
                    "cropped region at native pixels (resolved per video).",
        examples=["1080p", "1440p", "4k", "source", "1080x1920"],
    )
    ten_bit: bool = Field(
        False, description="10-bit output; requires a 10-bit source.")
    crf: int = Field(20, ge=0, le=51, description="quality, lower is better")
    whisper: str = Field(
        "large-v3",
        description="faster-whisper model size. 'small' is far quicker if the "
                    "quality bar allows.",
        examples=["large-v3", "medium", "small"],
    )
    device: Literal["auto", "cuda", "cpu"] = Field(
        "auto",
        description="auto tries the GPU and falls back to CPU; an explicit "
                    "device is honoured and will not fall back",
    )
    language: str | None = Field(
        None, description="e.g. ar, en. omit to autodetect", examples=["ar"])
    denoise: bool = Field(
        False,
        description="speech-band filter + FFT denoise before transcribing. "
                    "Measured 15 to 11 errors on field audio, and faster. "
                    "Produces a separate cached wav, so toggling is safe.",
    )
    fixups: dict[str, str] | None = Field(
        None,
        description="word substitutions applied to caption text after "
                    "transcription, as {wrong: right}. Timestamps are never "
                    "touched, so cuts are unaffected. Applied on read, so this "
                    "can change between renders without re-transcribing.",
        examples=[{"بايسون": "بايثون"}],
    )
    hotwords: str | None = Field(
        None, max_length=4000,
        description="space-separated terms to bias transcription toward, spelled "
                    "the way you want them back. Applies to the whole file — the "
                    "main quality lever on dialect and technical loanwords. Part "
                    "of the transcript cache key.",
    )
    initial_prompt: str | None = Field(
        None, max_length=4000,
        description="free-text prompt seeding ONLY the first ~30s. Prefer "
                    "hotwords. Part of the transcript cache key.",
    )
    font: str = Field(
        "Arial",
        description="must be installed on the RENDERING host, which under the "
                    "API is the server and not the caller's machine. libass falls "
                    "back silently, so a missing Arabic face ships as tofu.",
        examples=["Arial", "Traditional Arabic"],
    )
    captions: bool = Field(True, description="burn captions into the video")
    per_line: int = Field(CAPTION_MAX_WORDS, ge=1, le=8,
                          description="max words per caption line")
    auto_render: bool = Field(
        True,
        description="false stops at state=planned so the plan can be edited "
                    "via PUT /jobs/{id}/plan before rendering",
    )

    @model_validator(mode="after")
    def _lengths_ordered(self) -> JobOptions:
        if self.min_len > self.max_len:
            raise ValueError("min_len must be <= max_len")
        # reject a bad resolution at the API boundary rather than three minutes
        # into a job, when the worker finally reaches stage 5
        from ..pipeline.encode import parse_resolution
        parse_resolution(self.resolution)
        return self


class JobCreate(JobOptions):
    """Job from a video already on the server's filesystem."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "path": "talks/متسلمش دماغك.mov",
            "clips": 8,
            "min_len": 28,
            "max_len": 52,
            "language": "ar",
            "denoise": True,
            "font": "Traditional Arabic",
        }],
    })

    path: str = Field(
        ...,
        description="path to the source video. Relative paths resolve under "
                    "QATF_MEDIA_ROOT; absolute paths must still land inside it. "
                    "This is a security boundary — anything escaping it is a 403.",
        examples=["talks/keynote.mov"],
    )


class ClipModel(BaseModel):
    """One planned clip. `start`/`end` are seconds into the SOURCE video.

    After stage 4 these sit exactly on Whisper word boundaries. If you edit them
    by hand, leave `snap` on in `PUT /plan` — a typed second is a semantic guess
    and skipping the snap is how clips open mid-syllable."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "start": 184.32,
            "end": 233.86,
            "title": "PHP لسه عايشة",
            "hook": "كل سنة يقولوا ماتت، وكل سنة بتشغّل نص الويب",
            "why": "self-contained argument with a clean open and a punchline",
            "score": 0.85,
        }],
    })

    start: float = Field(..., ge=0, description="seconds into the source video")
    end: float = Field(..., gt=0, description="seconds into the source video")
    title: str = Field("clip", description="used for the output filename (ASCII-slugified)")
    hook: str = Field("", description="the opening line the model thinks earns attention")
    why: str = Field("", description="the model's reason for picking this passage")
    score: float = Field(0.0, ge=0.0, le=1.0, description="the model's own confidence")

    @model_validator(mode="after")
    def _ordered(self) -> ClipModel:
        if self.end <= self.start:
            raise ValueError("end must be greater than start")
        return self


class PlanUpdate(BaseModel):
    """Replace a job's whole plan. There is no partial update — send the clips
    you want, in the order you want them numbered."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "clips": [{"start": 184.0, "end": 233.0, "title": "PHP لسه عايشة", "score": 0.85}],
            "snap": True,
        }],
    })

    clips: list[ClipModel] = Field(..., min_length=1)
    snap: bool = Field(
        True,
        description="re-snap edited boundaries onto Whisper word times. Leave on "
                    "unless you are deliberately setting acoustic cut points by hand.",
    )


class WordModel(BaseModel):
    """One word with the timings stage 4 cuts on."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{"text": "بايثون", "start": 184.32, "end": 184.71}],
    })

    text: str
    start: float = Field(..., description="seconds into the source video")
    end: float = Field(..., description="seconds into the source video")


class TranscriptResponse(BaseModel):
    """The cached transcript. Word timings here are the ONLY acoustic truth in
    the system — everything stage 4 does is snap to these."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "language": "ar",
            "language_probability": 1.0,
            "word_count": 2841,
            "words": [{"text": "بايثون", "start": 184.32, "end": 184.71}],
        }],
    })

    language: str | None = None
    language_probability: float | None = Field(
        None, description="Whisper's confidence in the detected language, 0-1")
    word_count: int
    words: list[WordModel]


class ClipOutput(BaseModel):
    """A rendered file. `url` is relative to this API's root and needs no auth
    beyond whatever fronts the server."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "name": "01-php-lsh-ayshh.mp4",
            "size_bytes": 18_432_119,
            "url": "/jobs/a1b2c3d4e5f6/clips/01-php-lsh-ayshh.mp4",
        }],
    })

    name: str
    size_bytes: int
    url: str = Field(..., description="GET this path to download the clip")


class JobResponse(BaseModel):
    """The whole job. This is what you poll.

    `state` drives everything: `done` means `outputs` is final, `planned` means
    `clips` is ready for review, `failed` means read `error`."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "id": "a1b2c3d4e5f6",
            "state": "rendering",
            "message": "[5/5] rendered 3/8: 03-php-lsh-ayshh.mp4",
            "error": None,
            "video": "/srv/media/talks/keynote.mov",
            "source": "path",
            "created_at": "2026-08-06T09:14:02Z",
            "updated_at": "2026-08-06T09:31:44Z",
            "language": "ar",
            "device": "cuda",
            "word_count": 2841,
            "transcript_cached": False,
        }],
    })

    id: str
    state: JobState
    message: str = Field("", description="human-readable progress, e.g. '[2/5] transcribing'")
    error: str | None = Field(None, description="set only in state=failed")
    video: str
    source: Literal["upload", "path"]
    options: JobOptions
    created_at: str
    updated_at: str
    language: str | None = Field(None, description="detected, or whatever was forced")
    #: device stage 2 actually used — may differ from options.device under `auto`
    device: str | None = Field(
        None,
        description="the device stage 2 ACTUALLY used. Under `auto` this may be "
                    "cpu even though a GPU was requested — check it before "
                    "trusting a benchmark.",
    )
    word_count: int = 0
    transcript_cached: bool = Field(
        False, description="true when stage 2 was skipped because a matching "
                           "transcript was already on disk")
    clips: list[ClipModel] = Field([], description="the plan; populated from state=planned")
    outputs: list[ClipOutput] = Field(
        [], description="rendered files; grows during state=rendering")


class JobList(BaseModel):
    jobs: list[JobResponse]


class ProviderInfo(BaseModel):
    """One row of the stage-3 provider roster, as reported by /healthz."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "key": "anthropic",
            "default_model": "claude-opus-5",
            "base_url": None,
            "key_env": "ANTHROPIC_API_KEY",
            "structured_output": "json_schema",
            "context_tokens": 200_000,
            "note": "",
        }],
    })

    key: str = Field(..., description="the value to put in QATF_LLM_PROVIDER")
    default_model: str
    base_url: str | None = None
    key_env: str | None = Field(None, description="env var holding this provider's credential")
    structured_output: Literal["json_schema", "json_object", "prompt_only"] = Field(
        ...,
        description="json_schema = constrained decoding, malformed output is "
                    "impossible. json_object = valid JSON, shape not guaranteed. "
                    "prompt_only = nothing but the prompt.",
    )
    context_tokens: int | None = None
    note: str = ""


class Health(BaseModel):
    """Everything worth knowing BEFORE submitting an hour of audio.

    `degraded` is not fatal — the server still accepts jobs — but a job that
    reaches stage 3 without a credential fails after transcription has already
    run, which is minutes wasted."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "status": "ok",
            "version": "0.3.0",
            "model": "anthropic/claude-opus-5",
            "ffmpeg": True,
            "media_root": "/srv/media",
            "max_workers": 1,
            "llm_provider": "openrouter",
            "llm_ready": True,
            "llm_error": None,
            "providers": [],
            "cuda_devices": 1,
            "transcribe_device": "cuda",
        }],
    })

    status: Literal["ok", "degraded"] = Field(
        ..., description="degraded when ffmpeg is missing or stage 3 has no credential")
    version: str
    model: str = Field(..., description="the model stage 3 will actually call")
    ffmpeg: bool = Field(..., description="whether ffmpeg is genuinely on PATH")
    media_root: str = Field(..., description="the sandbox POST /jobs paths resolve inside")
    max_workers: int = Field(
        ..., description="concurrent jobs. Defaults to 1 because two large-v3 "
                         "loads fight over the same GPU.")
    #: active stage-3 provider, and whether its credential is actually present
    llm_provider: str
    llm_ready: bool = Field(..., description="whether the provider's credential is present")
    llm_error: str | None = None
    providers: list[ProviderInfo] = Field(
        [], description="the full roster, so a client can offer a provider picker")
    #: what stage 2 will pick under `device: auto`, and how many CUDA devices
    #: CTranslate2 can actually see. Worth knowing before submitting an hour of
    #: audio to a CPU.
    cuda_devices: int = Field(
        0, description="CUDA devices CTranslate2 can actually target — not what "
                       "nvidia-smi reports")
    transcribe_device: str = Field(
        "cpu", description="what stage 2 will pick under device=auto")
