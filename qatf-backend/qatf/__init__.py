"""qatf (قطف) — to pick, to harvest.

Harvests the few minutes of a long video worth watching: takes a long video,
returns vertical shorts with burned-in captions.

Every module lives in a subpackage, and the dependency direction is one-way:

    api  ->  jobs  ->  pipeline  ->  core
    cli  ->  pipeline  ->  core

    core/      config, constants, errors, types, utils. depends on nothing
    pipeline/  the five stages, one module each. the ONLY pipeline logic
    jobs/      job record, store, worker. knows nothing about HTTP
    api/       FastAPI front end — app factory, deps, schemas, routers
    cli/       command-line front end — parser, runner

Import cost is deliberate: `qatf.cli` pulls in neither pydantic nor fastapi, and
faster-whisper and anthropic are imported inside the functions that use them, so
`--plan --no-captions` runs without a GPU stack installed.
"""

__version__ = "0.4.0"
__all__ = ["__version__"]
