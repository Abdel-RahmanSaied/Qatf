#!/usr/bin/env python3
"""Legacy entry point for `python qatf.py talk.mp4 -o out/`.

The pipeline lives in the `qatf/` package. Prefer one of:

    qatf talk.mp4 -o out/        console script, after `pip install -e .`
    python -m qatf talk.mp4      no install needed

This file only exists so the command documented before the package split keeps
working. It contains no logic and should not grow any.

Why the import below resolves to the package and not to this file: Python's
path finder checks directories before same-named modules, so `qatf/` wins over
`qatf.py`. That is load-bearing — do not add a `qatf/qatf.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Normally redundant: `python qatf.py` already puts this directory on sys.path.
# It is not redundant when the file is reached through a symlink, where sys.path
# gets the link's directory rather than the real one.
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from qatf.cli import main  # noqa: E402  — must follow the sys.path fix above

if __name__ == "__main__":
    raise SystemExit(main())
