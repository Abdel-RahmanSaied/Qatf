"""Minimal .env loader.

Hand-rolled rather than pulling in python-dotenv: the CLI's whole point is that
it installs without the API extras, and a 40-line parser covers `KEY=value` with
quotes and comments — which is all a provider key needs.

**A real environment variable always wins.** A .env file is a convenience for
local runs, not an override — otherwise a stale checked-out file silently
overrides the key you just exported, and you debug the wrong credential.
"""

from __future__ import annotations

import os
from pathlib import Path


def parse(text: str) -> dict[str, str]:
    """Parse .env text. Ignores blanks, comments, and malformed lines."""
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # strip one matched pair of surrounding quotes; leave inner ones alone
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif "#" in value:
            # an unquoted trailing comment is not part of the value
            value = value.split("#", 1)[0].strip()
        out[key] = value
    return out


def load(path: str | Path = ".env", *, override: bool = False) -> dict[str, str]:
    """Load `path` into os.environ. Returns what was applied (values redacted-safe
    to log only by key). Missing file is not an error — .env is optional."""
    file = Path(path)
    if not file.is_file():
        return {}
    applied = {}
    for key, value in parse(file.read_text(encoding="utf-8")).items():
        if override or key not in os.environ:
            os.environ[key] = value
            applied[key] = value
    return applied


def find_and_load(start: str | Path = ".", *, override: bool = False) -> dict[str, str]:
    """Load the nearest .env from `start` upward, so the CLI works from a
    subdirectory of the project as well as its root."""
    here = Path(start).resolve()
    for directory in (here, *here.parents):
        candidate = directory / ".env"
        if candidate.is_file():
            return load(candidate, override=override)
    return {}
