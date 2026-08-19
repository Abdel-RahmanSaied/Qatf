# qatf — backend

The Python pipeline, CLI and FastAPI job server. This directory is the build
context for the backend Docker image.

- Project overview, install and quickstart: [`../README.md`](../README.md)
- Human-facing reference: [`../docs/`](../docs/)
- Working agreement for agents: [`../CLAUDE.md`](../CLAUDE.md)

```bash
pip install -e ".[all]"        # run from THIS directory; ffmpeg must be on PATH
python tests/smoke_pipeline.py # the suites run from here too
```
