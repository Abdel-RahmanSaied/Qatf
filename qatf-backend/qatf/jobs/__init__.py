"""Job store and in-process worker.

    model.py   Job record, JobState, on-disk layout
    store.py   persistence, accessors, the thread pool
    worker.py  what a job actually runs

Knows nothing about HTTP. It drives `qatf.pipeline` and records what happened,
so the CLI could use it too if a queue ever made sense there.
"""

from __future__ import annotations

from .model import RUNNING_STATE_VALUES, RUNNING_STATES, Job, JobState
from .store import Cancelled, JobStore
from .worker import render_plan, run_pipeline, run_render

__all__ = [
    "Job", "JobState", "RUNNING_STATES", "RUNNING_STATE_VALUES",
    "JobStore", "Cancelled",
    "run_pipeline", "run_render", "render_plan",
]
