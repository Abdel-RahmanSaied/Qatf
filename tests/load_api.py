"""Heavy concurrent load test of every qatf endpoint.

    python tests/load_api.py                    # default profile
    python tests/load_api.py --jobs 300 --concurrency 32 --rounds 6

Same fakes as `smoke_api.py` (`pipeline.audio.run`, `pipeline.encode.run`,
`pipeline.asr.transcribe`, `pipeline.select.pick_clips`), so this needs no
ffmpeg, no GPU, no API key and no network. It therefore measures the HTTP layer,
the job store and the serialisation path — not ffmpeg or Whisper.

This is a *test*, not a benchmark: every phase asserts something and the process
exits non-zero when a threshold is missed. A load test that cannot fail is just a
slow print statement.

What it is guarding, concretely:

  - `GET /jobs` used to stat() every rendered clip on every request, which was
    75% of its cost and scaled with the job count on the endpoint clients poll.
    PER_JOB_BUDGET_US pins that it no longer does.
  - The upload endpoint is `async def` and used to call a blocking `fh.write`,
    which stalls the event loop for the whole upload — every concurrent request
    queues behind a multi-gigabyte file. UPLOAD_STALL_BUDGET_MS pins the fix.
    Note this is a weak proxy: an in-process ASGI upload has no network in it,
    so the window is far shorter than a real one. It catches a full-blown stall,
    not a subtle one.
  - Concurrent mutation of one job must never produce a 5xx or a corrupt record.
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _harness import check, report, section  # also puts the project root on sys.path
from fastapi.testclient import TestClient

from qatf.api import create_app
from qatf.core.config import Settings
from qatf.core.types import Clip, Transcript, Word
from qatf.pipeline import asr, audio, encode, select

# ---- thresholds ----------------------------------------------------------
# Generous enough not to flake on a loaded laptop, tight enough that the
# regressions they exist for would blow straight through them.

#: p99 for a single-job read under concurrent load.
READ_P99_BUDGET_MS = 150.0
#: Marginal cost of one more job in `GET /jobs`. Measured at ~92us with sizes
#: read from the record; it was ~180us when every clip was stat()ed. 250us
#: leaves headroom for a busy machine while still catching a return to stat().
PER_JOB_BUDGET_US = 250.0
#: /healthz is what monitoring and load balancers poll hardest. It used to spawn
#: an ffmpeg subprocess per request and measured p99 > 1s under load — twenty
#: times `GET /jobs/{id}`, which does real work.
#:
#: Asserted SERIALLY, not on the concurrent p99. Every thread in the read storm
#: calls /healthz first, so its tail is dominated by a synchronized 24-way burst
#: — an artifact of the harness, not of the endpoint. The handler itself is now
#: ~35us; 5ms catches a return to spawning a process without measuring thread
#: scheduling.
HEALTH_SERIAL_BUDGET_MS = 5.0
#: Worst poll latency observed while a large upload is streaming. With the
#: blocking write it tracked the upload duration; off the event loop it should
#: stay in the same range as an idle poll.
UPLOAD_STALL_BUDGET_MS = 750.0

# ---- fakes (identical in spirit to smoke_api.py) -------------------------
_render_lock = threading.Lock()


def fake_run(cmd, quiet=True):
    out = Path(cmd[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\0" * 2048)


def fake_transcribe(wav, model_size, device, language,
                    initial_prompt=None, hotwords=None):
    return Transcript(
        words=[Word(f"word{i}", i * 0.5, i * 0.5 + 0.45) for i in range(400)],
        language=language or "ar",
        language_probability=0.98,
        device="cpu",
        compute_type="int8",
    )


def fake_pick_clips(words, n, lo, hi, model=None, settings=None):
    return [
        Clip(10.0, 50.0, "first clip", "hook", "why", 0.9),
        Clip(60.3, 110.7, "second clip", "hook two", "why two", 0.7),
    ]


audio.run = fake_run
encode.run = fake_run
asr.transcribe = fake_transcribe
select.pick_clips = fake_pick_clips


# ---- measurement ---------------------------------------------------------

class Timings:
    """Latency samples per endpoint label, plus every non-2xx seen."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.samples: dict[str, list[float]] = defaultdict(list)
        self.statuses: dict[str, list[int]] = defaultdict(list)
        self.errors: list[str] = []

    def record(self, label: str, ms: float, status: int) -> None:
        with self._lock:
            self.samples[label].append(ms)
            self.statuses[label].append(status)

    def failure(self, what: str) -> None:
        with self._lock:
            self.errors.append(what)

    def call(self, label: str, fn, *args, **kwargs):
        start = time.perf_counter()
        try:
            resp = fn(*args, **kwargs)
        except Exception as exc:                       # noqa: BLE001 — reported
            self.failure(f"{label}: {type(exc).__name__}: {exc}")
            return None
        ms = (time.perf_counter() - start) * 1000
        self.record(label, ms, resp.status_code)
        return resp

    def server_errors(self) -> dict[str, int]:
        with self._lock:
            return {label: sum(1 for s in codes if s >= 500)
                    for label, codes in self.statuses.items()
                    if any(s >= 500 for s in codes)}

    def reset(self) -> None:
        with self._lock:
            self.samples.clear()
            self.statuses.clear()
            self.errors.clear()


def pct(values: list[float], q: float) -> float:
    """Nearest-rank percentile. No numpy — this suite has no test deps."""
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, int(round(q / 100 * len(ordered) + 0.5)) - 1))
    return ordered[idx]


def table(t: Timings, title: str, elapsed: float) -> None:
    print(f"\n  {title}")
    print(f"  {'endpoint':34} {'n':>6} {'err':>4} "
          f"{'p50':>8} {'p95':>8} {'p99':>8} {'max':>8}")
    total = 0
    for label in sorted(t.samples):
        v = t.samples[label]
        total += len(v)
        bad = sum(1 for s in t.statuses[label] if s >= 500)
        print(f"  {label:34} {len(v):6d} {bad:4d} "
              f"{statistics.median(v):7.1f}m {pct(v, 95):7.1f}m "
              f"{pct(v, 99):7.1f}m {max(v):7.1f}m")
    if elapsed > 0:
        print(f"  {'':34} {total:6d} requests in {elapsed:.2f}s "
              f"= {total / elapsed:,.0f} req/s")


# ---- setup ---------------------------------------------------------------

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument("--jobs", type=int, default=200, help="jobs to seed")
ap.add_argument("--concurrency", type=int, default=24, help="worker threads")
ap.add_argument("--rounds", type=int, default=4, help="read-storm rounds per job")
ap.add_argument("--scratch", type=Path, default=None)
args = ap.parse_args()

SCRATCH = args.scratch or Path(tempfile.mkdtemp(prefix="qatf-load-"))
MEDIA = SCRATCH / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
(MEDIA / "talk.mp4").write_bytes(b"not really a video")

SETTINGS = Settings(
    data_dir=SCRATCH / "data",
    media_root=MEDIA.resolve(),
    # deliberately higher than the shipped default of 1: this test is about
    # contention on the store and the response path, not about GPU scheduling
    workers=8,
    max_upload_bytes=192 * 1024 * 1024,
    llm_provider="anthropic",
    llm_model="claude-sonnet-5",
)

T = Timings()
app = create_app(SETTINGS)

print(f"load profile: {args.jobs} jobs, {args.concurrency} threads, "
      f"{args.rounds} rounds\nscratch: {SCRATCH}")


def wait_for(client, job_id, states, timeout=60):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.02)
    raise AssertionError(f"timeout waiting for {job_id}")


with TestClient(app) as client:
    pool = ThreadPoolExecutor(max_workers=args.concurrency)

    # ---- phase 1: seed ---------------------------------------------------
    section("phase 1 — seed the store under concurrent creation")
    t0 = time.perf_counter()

    def create(i: int):
        body = {"path": "talk.mp4", "device": "cpu", "clips": 2,
                "auto_render": i % 3 != 0}
        r = T.call("POST /jobs", client.post, "/jobs", json=body)
        return r.json()["id"] if r is not None and r.status_code == 202 else None

    ids = [j for j in pool.map(create, range(args.jobs)) if j]
    seeded = time.perf_counter() - t0
    check(f"all {args.jobs} creations accepted", len(ids) == args.jobs,
          f"got {len(ids)}")
    check("no 5xx during creation", not T.server_errors(), str(T.server_errors()))

    terminal = {"done", "planned", "failed", "cancelled"}
    for jid in ids:
        wait_for(client, jid, terminal)
    drained = time.perf_counter() - t0
    states = defaultdict(int)
    for j in client.get("/jobs").json()["jobs"]:
        states[j["state"]] += 1
    print(f"  seeded in {seeded:.2f}s, pipeline drained in {drained:.2f}s")
    print(f"  states: {dict(states)}")
    check("every job reached a terminal state", states.get("failed", 0) == 0,
          str(dict(states)))
    rendered = [j["id"] for j in client.get("/jobs").json()["jobs"]
                if j["state"] == "done"]
    check("some jobs rendered outputs", bool(rendered))

    # ---- phase 2: read storm --------------------------------------------
    section("phase 2 — concurrent read storm across every read endpoint")
    T.reset()

    def read_round(jid: str):
        T.call("GET /healthz", client.get, "/healthz")
        T.call("GET /jobs/{id}", client.get, f"/jobs/{jid}")
        T.call("GET /jobs/{id}/plan", client.get, f"/jobs/{jid}/plan")
        T.call("GET /jobs/{id}/transcript", client.get, f"/jobs/{jid}/transcript")
        r = T.call("GET /jobs/{id}/clips", client.get, f"/jobs/{jid}/clips")
        if r is not None and r.status_code == 200 and r.json():
            name = r.json()[0]["name"]
            T.call("GET /clips/{name}", client.get, f"/jobs/{jid}/clips/{name}")

    t0 = time.perf_counter()
    work = [jid for _ in range(args.rounds) for jid in ids]
    list(pool.map(read_round, work))
    elapsed = time.perf_counter() - t0
    table(T, "read storm", elapsed)

    check("no exceptions raised", not T.errors, "; ".join(T.errors[:3]))
    check("no 5xx under read load", not T.server_errors(), str(T.server_errors()))
    single = T.samples["GET /jobs/{id}"]
    check(f"GET /jobs/{{id}} p99 under {READ_P99_BUDGET_MS:.0f}ms",
          pct(single, 99) < READ_P99_BUDGET_MS, f"{pct(single, 99):.1f}ms")
    check("downloads streamed without error",
          all(s == 200 for s in T.statuses.get("GET /clips/{name}", [200])))
    health = T.samples["GET /healthz"]
    check("/healthz p50 is in the same range as the other reads — its tail is "
          "a burst artifact, not a cost",
          statistics.median(health) < statistics.median(single) * 3,
          f"healthz {statistics.median(health):.1f}ms vs "
          f"job {statistics.median(single):.1f}ms")

    # measured serially: the handler cost, with no scheduling noise in it
    client.get("/healthz")                      # warm
    floor = float("inf")
    for _ in range(50):
        t0 = time.perf_counter()
        client.get("/healthz")
        floor = min(floor, (time.perf_counter() - t0) * 1000)
    print(f"  /healthz serial floor: {floor:.2f} ms")
    check(f"/healthz costs under {HEALTH_SERIAL_BUDGET_MS:.0f}ms serially "
          "(guards the subprocess-per-request regression)",
          floor < HEALTH_SERIAL_BUDGET_MS, f"{floor:.2f}ms")

    # ---- phase 3: list scaling ------------------------------------------
    section("phase 3 — GET /jobs marginal cost per job")
    # The regression this guards: sizes read from the job record, not stat()ed
    # off the filesystem on every request.
    T.reset()

    def best_of(params, repeat=9):
        """Minimum, not mean. Noise only ever adds time, so the floor is the
        cleanest estimate of what the work actually costs."""
        client.get("/jobs", params=params)              # warm
        floor = float("inf")
        for _ in range(repeat):
            t0 = time.perf_counter()
            resp = client.get("/jobs", params=params)
            floor = min(floor, (time.perf_counter() - t0) * 1000)
        return len(resp.json()["jobs"]), floor

    # Two real points on the same code path, differing only in how many jobs get
    # serialised. `?state=` filters before to_response runs, so the delta is
    # exactly the per-job response cost — which is the thing the stat() removal
    # was about.
    n_lo, ms_lo = best_of({"state": "done"})
    n_hi, ms_hi = best_of(None)
    print(f"  {n_lo:4d} jobs (?state=done) -> {ms_lo:7.2f} ms")
    print(f"  {n_hi:4d} jobs (unfiltered)  -> {ms_hi:7.2f} ms")
    check("the two points differ enough to divide by",
          n_hi - n_lo >= 10, f"{n_lo} vs {n_hi} jobs")
    per_job_us = ((ms_hi - ms_lo) / max(1, n_hi - n_lo)) * 1000
    print(f"  marginal cost: {per_job_us:.1f} us/job")
    check(f"marginal cost under {PER_JOB_BUDGET_US:.0f} us/job "
          "(guards the stat() regression)",
          per_job_us < PER_JOB_BUDGET_US, f"{per_job_us:.1f} us/job")

    # ---- phase 4: write storm -------------------------------------------
    section("phase 4 — concurrent writes against the same jobs")
    T.reset()
    targets = rendered[: max(1, len(rendered) // 2)]

    def write_round(jid: str):
        plan = {"clips": [{"start": 12.0, "end": 48.0, "title": "edited",
                           "hook": "", "why": "", "score": 0.9}], "snap": True}
        T.call("PUT /jobs/{id}/plan", client.put, f"/jobs/{jid}/plan", json=plan)
        tr = T.call("GET /jobs/{id}/transcript", client.get,
                    f"/jobs/{jid}/transcript")
        if tr is not None and tr.status_code == 200:
            words = [dict(w) for w in tr.json()["words"]]
            words[3]["text"] = "CORRECTED"
            T.call("PUT /jobs/{id}/transcript", client.put,
                   f"/jobs/{jid}/transcript", json={"words": words})
        T.call("POST /jobs/{id}/render", client.post, f"/jobs/{jid}/render")

    t0 = time.perf_counter()
    list(pool.map(write_round, targets * 2))
    elapsed = time.perf_counter() - t0
    table(T, "write storm", elapsed)
    check("no exceptions during concurrent writes", not T.errors,
          "; ".join(T.errors[:3]))
    check("no 5xx during concurrent writes", not T.server_errors(),
          str(T.server_errors()))
    # 409 is the correct answer when a worker already holds the job — a
    # concurrent mutation must be refused, never applied twice
    conflicts = sum(1 for s in T.statuses.get("POST /jobs/{id}/render", [])
                    if s == 409)
    print(f"  {conflicts} render requests correctly refused with 409")

    for jid in targets:
        wait_for(client, jid, terminal)
    check("every job survived concurrent mutation",
          all(client.get(f"/jobs/{j}").json()["state"] in terminal for j in targets))
    bad = [j for j in targets
           if not json.loads((SETTINGS.data_dir / j / "job.json")
                             .read_text(encoding="utf-8")).get("id")]
    check("no job record was corrupted by concurrent persists", not bad, str(bad[:3]))

    # ---- phase 5: uploads must not stall the event loop ------------------
    section("phase 5 — polling latency while a large upload streams")
    T.reset()
    payload = MEDIA / "big.mp4"
    payload.write_bytes(bytes(128 * 1024 * 1024))
    stop = threading.Event()

    def poll_forever():
        while not stop.is_set():
            T.call("GET /healthz (during upload)", client.get, "/healthz")
            time.sleep(0.002)

    pollers = [pool.submit(poll_forever) for _ in range(4)]
    t0 = time.perf_counter()
    with payload.open("rb") as fh:
        up = T.call("POST /jobs/upload", client.post, "/jobs/upload",
                    files={"file": ("big.mp4", fh, "video/mp4")},
                    data={"options": json.dumps({"device": "cpu",
                                                 "auto_render": False})})
    upload_s = time.perf_counter() - t0
    stop.set()
    for f in pollers:
        f.result()
    during = T.samples.get("GET /healthz (during upload)", [])
    print(f"  uploaded {payload.stat().st_size / 1e6:.0f} MB in {upload_s:.2f}s "
          f"while {len(during)} polls ran")
    table(T, "upload window", 0)
    check("upload accepted", up is not None and up.status_code == 202,
          up.text[:160] if up is not None else "no response")
    check(f"worst poll during upload under {UPLOAD_STALL_BUDGET_MS:.0f}ms "
          "(guards the blocking-write regression)",
          during and max(during) < UPLOAD_STALL_BUDGET_MS,
          f"{max(during):.1f}ms" if during else "no samples")
    if up is not None and up.status_code == 202:
        wait_for(client, up.json()["id"], terminal)

    # ---- phase 6: mixed realistic traffic --------------------------------
    section("phase 6 — mixed traffic, the shape a real client produces")
    T.reset()

    def mixed(i: int):
        jid = ids[i % len(ids)]
        T.call("GET /jobs/{id}", client.get, f"/jobs/{jid}")          # poll
        if i % 5 == 0:
            T.call("GET /jobs?state=done", client.get, "/jobs", params={"state": "done"})
        if i % 7 == 0:
            T.call("GET /healthz", client.get, "/healthz")
        if i % 11 == 0:
            T.call("GET /jobs/{id}/clips", client.get, f"/jobs/{jid}/clips")

    t0 = time.perf_counter()
    list(pool.map(mixed, range(args.jobs * args.rounds)))
    elapsed = time.perf_counter() - t0
    table(T, "mixed traffic", elapsed)
    check("no exceptions under mixed load", not T.errors, "; ".join(T.errors[:3]))
    check("no 5xx under mixed load", not T.server_errors(), str(T.server_errors()))

    # ---- phase 7: deletion ----------------------------------------------
    section("phase 7 — concurrent deletion")
    T.reset()
    doomed = ids[: len(ids) // 2]

    def remove(jid: str):
        T.call("DELETE /jobs/{id}", client.delete, f"/jobs/{jid}")

    list(pool.map(remove, doomed))
    check("no 5xx during deletion", not T.server_errors(), str(T.server_errors()))
    gone = [j for j in doomed if (SETTINGS.data_dir / j).exists()]
    check("every deleted job directory is gone", not gone, str(gone[:3]))
    left = {j["id"] for j in client.get("/jobs").json()["jobs"]}
    check("deleted jobs no longer listed", not (set(doomed) & left))

    pool.shutdown(wait=True)

print(f"\nscratch left at {SCRATCH}")
raise SystemExit(report())
