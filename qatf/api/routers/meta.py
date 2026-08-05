"""Health and capability reporting."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ... import __version__
from ...core.config import Settings
from ...core.errors import QatfError
from ...core.utils import ffmpeg_available
from ...llm import describe, provider_from_settings
from ...pipeline import cuda_device_count, resolve_device
from ..deps import get_settings
from ..schemas import Health, ProviderInfo

router = APIRouter(tags=["meta"])


@router.get(
    "/healthz",
    response_model=Health,
    operation_id="health",
    summary="Readiness, provider roster and transcription device",
    response_description="Never 503s — read `status` and the individual flags. "
                         "A server that cannot do the work still answers.",
)
def healthz(settings: Settings = Depends(get_settings)) -> Health:
    """**Call this before submitting an hour of audio.**

    Reports `degraded` when ffmpeg is missing or the stage-3 provider has no
    usable credential. Both are worth knowing up front rather than three minutes
    into a job — a missing API key otherwise surfaces only *after* transcription
    has already run.

    Two fields answer the question benchmarks get wrong:

    * `cuda_devices` — how many devices **CTranslate2** can actually target. Not
      what `nvidia-smi` reports: a card the installed CTranslate2 cannot use
      (compute capability, driver mismatch) is not a usable device, and only the
      engine knows that.
    * `transcribe_device` — what stage 2 will pick under `device: auto`. If this
      says `cpu` on a machine with a GPU, a `large-v3` run is about to take a
      very long time.

    `providers` carries the whole stage-3 roster with each entry's structured-
    output tier, so a client can offer a provider picker without hardcoding one.
    """
    ffmpeg_ok = ffmpeg_available()

    llm_error: str | None = None
    try:
        provider_from_settings(settings)
        llm_ready = True
    except QatfError as exc:
        llm_ready, llm_error = False, str(exc)

    return Health(
        status="ok" if (ffmpeg_ok and llm_ready) else "degraded",
        version=__version__,
        model=settings.model,
        ffmpeg=ffmpeg_ok,
        media_root=str(settings.media_root),
        max_workers=settings.workers,
        llm_provider=settings.llm_provider,
        llm_ready=llm_ready,
        llm_error=llm_error,
        providers=[ProviderInfo(**p) for p in describe()],
        cuda_devices=cuda_device_count(),
        transcribe_device=resolve_device("auto"),
    )
