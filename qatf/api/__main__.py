"""`python -m qatf.api` / the `qatf-serve` console script."""

from __future__ import annotations

from ..core.config import Settings


def main() -> int:
    import uvicorn

    settings = Settings.from_env()
    # import string rather than the app object, so --reload works
    uvicorn.run("qatf.api:app", host=settings.host, port=settings.port,
                reload=settings.reload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
