"""HTTP routers. Endpoints only — pipeline logic lives in `qatf.pipeline`."""

from . import jobs, meta, outputs, plan

#: Starlette matches on the full path template, so these four do not conflict.
#: The one ordering that does matter is inside jobs.py: /jobs/upload is declared
#: before /jobs/{job_id}, so keep it there if a GET is ever added for it.
ALL = (meta.router, jobs.router, plan.router, outputs.router)

__all__ = ["ALL", "jobs", "meta", "outputs", "plan"]
