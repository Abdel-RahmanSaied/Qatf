"""Editable server settings. Endpoints only.

The allowlist and the precedence rule live in `core.config`; the `base_url`
boundary lives in `llm`. This module maps them onto HTTP and does nothing else.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends

from ...core.config import EDITABLE, Settings
from ...core.errors import InvalidSetting
from ...jobs import JobStore
from ...llm import validate_base_url
from ..deps import get_store
from ..openapi import BAD_URL, merge
from ..schemas import SettingItem, SettingsResponse

router = APIRouter(prefix="/settings", tags=["settings"])

#: Stored like any other setting, but the thread pool is not resized while jobs
#: are in flight — `QATF_WORKERS` is 1 by design because two concurrent
#: `large-v3` loads fight over one GPU, and a resize that half-works is worse
#: than a flag that says "restart".
RESTART_REQUIRED = frozenset({"workers"})

#: Refusals name the allowed set and never the rejected key. A validator that
#: formats caller input into its own message defeats the 422 handler that
#: strips it — `whisper`, `preset` and `resolution` all had to learn this.
_UNKNOWN = "unknown setting. Editable: " + ", ".join(sorted(EDITABLE))


def _view(store: JobStore) -> SettingsResponse:
    saved = store.settings_overrides()
    from_env = Settings.from_env()
    bare = Settings()
    effective = store.settings_for_job()
    return SettingsResponse(items=[
        SettingItem(
            key=key,
            value=getattr(effective, key),
            source=("saved" if key in saved
                    else "env" if getattr(from_env, key) != getattr(bare, key)
                    else "default"),
            restart_required=key in RESTART_REQUIRED,
        )
        for key in sorted(EDITABLE)
    ])


@router.get(
    "",
    response_model=SettingsResponse,
    operation_id="getSettings",
    summary="Read the editable server settings",
    response_description="Every editable key, its effective value and its source.",
)
def read_settings(store: JobStore = Depends(get_store)) -> SettingsResponse:
    """What stage 3 will use on the **next** job, and where each value came from.

    API keys never appear here. Presets name a credential and read it from the
    environment, so there is nothing to return — check `/healthz` for whether
    the configured provider has a usable one."""
    return _view(store)


@router.put(
    "",
    response_model=SettingsResponse,
    operation_id="updateSettings",
    summary="Save one or more server settings",
    response_description="The settings as they stand after the update.",
    responses=merge(BAD_URL),
)
def update_settings(
    body: dict = Body(..., examples=[{"llm_model": "anthropic/claude-opus-5"}]),
    store: JobStore = Depends(get_store),
) -> SettingsResponse:
    """Partial update — send only the keys you are changing.

    Partial rather than wholesale replace: a settings page that must send every
    field to change one is a page that silently reverts a field somebody else
    changed between the read and the write.

    **Takes effect on the next job.** A job already running keeps the settings
    it started with, because it captured its own snapshot when it began — the
    job record reports the provider and model used, and a mid-run change would
    make it describe a run that did not happen. `workers` is stored but needs a
    restart; see `restart_required`.

    A saved value overrides the matching `QATF_*` variable. That is the opposite
    of how `.env` parsing works, and it is deliberate: compose always sets
    `QATF_LLM_*`, so an environment-wins rule would make this endpoint inert
    under Docker."""
    unknown = set(body) - EDITABLE
    if unknown:
        raise InvalidSetting(_UNKNOWN)
    if body.get("llm_base_url"):
        # Checked again in `llm` when the provider is built. Here so a refusal
        # is a synchronous 403 rather than a job that dies on a worker thread.
        validate_base_url(body["llm_base_url"])
    for key, value in body.items():
        store.save_setting(key, value)
    return _view(store)


@router.delete(
    "/{key}",
    response_model=SettingsResponse,
    operation_id="clearSetting",
    summary="Clear one setting, falling back to the environment",
    response_description="The settings as they stand after the reset.",
)
def clear_setting(key: str,
                  store: JobStore = Depends(get_store)) -> SettingsResponse:
    """Drop a saved override so the `QATF_*` variable takes over again.

    Deleting the row is not the same as saving `""`. An absent row means "not
    overridden"; an empty string means "explicitly blank — use the preset's
    default model, or the preset's own base URL"."""
    if key not in EDITABLE:
        raise InvalidSetting(_UNKNOWN)
    store.clear_setting(key)
    return _view(store)
