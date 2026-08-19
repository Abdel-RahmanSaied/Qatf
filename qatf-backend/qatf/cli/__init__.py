"""Command-line front end.

    parser.py  the argument surface
    runner.py  the run flow

Kept alongside the API because it is the fast loop for the thing that actually
needs verifying — filtergraph and caption changes, checked by rendering a clip
and looking at an extracted frame.

Imports neither pydantic nor fastapi, and that is worth preserving: it means the
CLI installs and runs without the `[api]` extra.
"""

from __future__ import annotations

from ..core.dotenv import find_and_load
from ..core.errors import QatfError
from ..core.utils import log
from .parser import build_parser
from .runner import preflight, run

__all__ = ["main", "build_parser", "run", "preflight"]


def main(argv: list[str] | None = None) -> int:
    # before parsing, so a provider key in .env is visible to preflight
    find_and_load()
    args = build_parser().parse_args(argv)
    try:
        return run(args)
    except QatfError as exc:
        # deliberate failures get one clean line, not a traceback
        log(f"{type(exc).__name__}: {exc}")
        return 1
    except KeyboardInterrupt:
        log("\ninterrupted")
        return 130
