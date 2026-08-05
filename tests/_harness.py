"""Minimal check/report harness.

Deliberately not pytest: these run in seconds with no plugins, and the project's
dependency budget is small. If the suite ever outgrows this, pytest is the
obvious next step.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_PASSED: list[str] = []
_FAILED: list[str] = []


def section(title: str) -> None:
    print(f"\n[{title}]")


def check(label: str, condition: bool, detail: str = "") -> bool:
    (_PASSED if condition else _FAILED).append(label)
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    return bool(condition)


def raises(label: str, exc_type: type[BaseException], fn, *args, **kwargs) -> bool:
    try:
        fn(*args, **kwargs)
    except exc_type:
        return check(label, True)
    except BaseException as exc:                      # noqa: BLE001
        return check(label, False, f"raised {type(exc).__name__}: {exc}")
    return check(label, False, "did not raise")


def report() -> int:
    print(f"\n{len(_PASSED)} passed, {len(_FAILED)} failed")
    for name in _FAILED:
        print("  FAILED:", name)
    return 1 if _FAILED else 0
