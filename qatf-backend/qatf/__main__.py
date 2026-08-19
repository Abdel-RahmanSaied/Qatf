"""`python -m qatf ...` runs the CLI. The server is `python -m qatf.api`."""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
