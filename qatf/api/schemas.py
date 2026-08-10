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

from ..core.constants import CAPTION_MAX_WORDS, DEFAULT_TRACK_TIER
from ..jobs.model import RUNNING_STATE_VALUES, RUNNING_STATES, JobState

# encode holds no heavy imports, so naming these at module level costs nothing
# and keeps the wire defaults and the pipeline defaults provably identical.
from ..pipeline.encode import DEFAULT_CODEC, DEFAULT_PRESET, PRESETS

#: Upper bound on any timestamp a caller may send, in seconds — 24 hours. Not a
#: supported-length claim: it exists so `1e999` and other absurd values are
#: refused at the boundary instead of reaching ffmpeg as a duration.
MAX_SECONDS = 86_400.0

#: Upper bound on clips in a hand-edited plan. `JobOptions.clips` caps what the
#: model may be asked for at 50; without this, `PUT /plan` bypasses that cap
#: entirely and one request can queue thousands of ffmpeg encodes.
MAX_PLAN_CLIPS = 100

__all__ = [
    "JobState", "RUNNING_STATES", "RUNNING_STATE_VALUES", "MAX_SECONDS",
    "JobOptions", "JobCreate", "ClipModel", "PlanUpdate", "WordModel",
    "TranscriptResponse", "TranscriptUpdate",
    "ClipOutput", "JobResponse", "JobList",
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
            "codec": "h265",
            "preset": "medium",
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
    reframe: Literal["crop", "blur", "track"] = Field(
        "crop",
        description="crop takes a centre slice and keeps ~3x the subject pixels; "
                    "blur letterboxes the whole frame over a blurred fill. Use "
                    "blur only when the framing genuinely needs the full width. "
                    "track is crop with a window that follows the speaker, and "
                    "needs OpenCV on the SERVER (`pip install -e \".[track]\"`) "
                    "— without it the job fails with 503 rather than quietly "
                    "rendering a static crop.",
    )
    track_tier: Literal["fast", "balanced", "best"] = Field(
        DEFAULT_TRACK_TIER,
        description="how hard `reframe: track` looks: face detection at 1fps, "
                    "3fps or 8fps. Ignored by the other two modes. Never "
                    "silently downgraded — a tier this host runs slowly runs "
                    "slowly and says so.",
    )
    codec: Literal["h264", "h265"] = Field(
        DEFAULT_CODEC,
        description="h265 (the default) is ~40% smaller at equal quality and the "
                    "better archival choice, but measured ~3x slower to encode "
                    "than h264 at the same preset — raise `preset` if that "
                    "matters. Some Instagram and TikTok upload paths still "
                    "prefer h264.",
    )
    preset: str = Field(
        DEFAULT_PRESET,
        description="encoder speed/quality trade, and THE lever on render time: "
                    "veryfast measured 1.6x faster than medium on h265. `medium` "
                    "is the default because a clip is rendered once and watched "
                    "many times; use a faster preset while iterating.",
        examples=["medium", "faster", "veryfast"],
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
        description="faster-whisper model size, from the published set. 'small' "
                    "is far quicker if the quality bar allows. Restricted to an "
                    "allowlist over HTTP: `WhisperModel` treats an unrecognised "
                    "name as a HuggingFace repo or a local path, so a free string "
                    "here would let a caller choose what code the server loads.",
        examples=["large-v3", "medium", "small"],
    )
    device: Literal["auto", "cuda", "cpu"] = Field(
        "auto",
        description="auto tries the GPU and falls back to CPU; an explicit "
                    "device is honoured and will not fall back",
    )
    language: str | None = Field(
        None,
        description="e.g. ar, en. omit to autodetect",
        examples=["ar"],
        # a language tag, not free text: this value is part of the transcript
        # cache FILENAME, and "../../x" there escapes the work directory
        pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$",
        max_length=32,
    )
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
        # allowlist, not a spelling check — see MODEL_SIZES
        from ..pipeline.asr import MODEL_SIZES
        # Neither message quotes the rejected value back. The 422 handler in
        # `api/__init__.py` strips FastAPI's `input` field, but that only covers
        # errors FastAPI composes: a value formatted into a validator's own
        # message sails straight through it as part of the reason. Live testing
        # found all three of these reflecting caller content while the suite
        # reported the rule as held, because the existing check only exercised
        # the FastAPI-generated path.
        if self.whisper not in MODEL_SIZES:
            raise ValueError(
                f"unknown whisper model. Use one of: "
                f"{', '.join(sorted(MODEL_SIZES))}")
        if self.preset not in PRESETS:
            raise ValueError(
                f"unknown preset. Use one of: {', '.join(PRESETS)}")
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

    # allow_inf_nan=False is load-bearing: `1e999` parses as `inf`, which
    # satisfies `gt=0` and `end > start`, persists into the plan, and reaches
    # ffmpeg as `-t inf`. MAX_SECONDS bounds the rest — a plan is not the place
    # to request a 300-hour encode.
    model_config = ConfigDict(allow_inf_nan=False, json_schema_extra={
        "examples": [{
            "start": 184.32,
            "end": 233.86,
            "title": "PHP لسه عايشة",
            "hook": "كل سنة يقولوا ماتت، وكل سنة بتشغّل نص الويب",
            "why": "self-contained argument with a clean open and a punchline",
            "score": 0.85,
        }],
    })

    start: float = Field(..., ge=0, le=MAX_SECONDS,
                         description="seconds into the source video")
    end: float = Field(..., gt=0, le=MAX_SECONDS,
                       description="seconds into the source video")
    title: str = Field("clip", max_length=300,
                       description="used for the output filename (ASCII-slugified)")
    hook: str = Field("", max_length=2000,
                      description="the opening line the model thinks earns attention")
    why: str = Field("", max_length=2000,
                     description="the model's reason for picking this passage")
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

    clips: list[ClipModel] = Field(
        ..., min_length=1, max_length=MAX_PLAN_CLIPS,
        description="the clips you want, in the order you want them numbered")
    snap: bool = Field(
        True,
        description="re-snap edited boundaries onto Whisper word times. Leave on "
                    "unless you are deliberately setting acoustic cut points by hand.",
    )


class WordModel(BaseModel):
    """One word with the timings stage 4 cuts on."""

    model_config = ConfigDict(allow_inf_nan=False, json_schema_extra={
        "examples": [{"text": "بايثون", "start": 184.32, "end": 184.71}],
    })

    text: str = Field(..., max_length=500)
    start: float = Field(..., ge=0, le=MAX_SECONDS,
                         description="seconds into the source video")
    end: float = Field(..., ge=0, le=MAX_SECONDS,
                       description="seconds into the source video")


class TranscriptResponse(BaseModel):
    """The transcript as it will be captioned — fixups and per-word corrections
    already applied, so what you read here is what gets burned in.

    Word timings are the ONLY acoustic truth in the system; everything stage 4
    does is snap to these. They are reported, never accepted back."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "language": "ar",
            "language_probability": 1.0,
            "word_count": 2841,
            "edits_applied": 3,
            "edits_stale": 0,
            "words": [{"text": "بايثون", "start": 184.32, "end": 184.71}],
        }],
    })

    language: str | None = None
    language_probability: float | None = Field(
        None, description="Whisper's confidence in the detected language, 0-1")
    word_count: int
    edits_applied: int = Field(
        0, description="per-word corrections currently in effect")
    edits_stale: int = Field(
        0,
        description="corrections that no longer match the word at their index "
                    "and were therefore SKIPPED, not applied. Non-zero means the "
                    "transcript moved underneath them — usually a re-transcribe "
                    "at a different whisper size, or denoise toggled. Re-submit "
                    "the transcript to rebuild them.",
    )
    words: list[WordModel]


class TranscriptUpdate(BaseModel):
    """Correct misheard words.

    Send the whole word list back with `text` changed where it is wrong. The
    submission is diffed against the transcript and stored as an overlay, so this
    is a wholesale replace: re-submitting the untouched transcript clears every
    correction.

    **Only `text` may differ.** The word count and every `start`/`end` must match
    exactly, and a submission that changes either is refused with `422` rather
    than accepted quietly. Timings come from the audio and are what every cut
    point is snapped to — a correction can change what a caption reads and can
    never move a cut. That is also what makes this safe to do after rendering:
    the cut points are provably identical, so only stage 5 has to run again.

    To split one word into two, put both words in that word's `text`. There is no
    honest timing to give a word Whisper never heard."""

    model_config = ConfigDict(json_schema_extra={
        "examples": [{
            "words": [
                {"text": "هو", "start": 204.11, "end": 204.29},
                {"text": "مين", "start": 204.29, "end": 204.58},
                {"text": "قال", "start": 204.58, "end": 204.91},
            ],
        }],
    })

    words: list[WordModel] = Field(
        ..., min_length=1, max_length=200_000,
        description="the complete word list, in order, with corrected text. The "
                    "cap is ~20 hours of speech and exists so an oversized body "
                    "is refused before it is materialised, not after.")


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
