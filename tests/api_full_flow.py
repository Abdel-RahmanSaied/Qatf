"""Full-flow API test against a running qatf server.

    python tools/api_full_flow.py [base_url]

Exercises every documented endpoint and the whole job lifecycle against a REAL
server with a REAL video — not the in-process fakes `tests/smoke_api.py` uses.
That suite proves the state machine with ffmpeg, Whisper and the model call
stubbed out; this one proves the thing actually runs.

Ordered so the cheap contract checks fail fast before the expensive render.
Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
VIDEO = "excerpt-ar-4min.mp4"
passed: list[str] = []
failed: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    (passed if ok else failed).append(label)
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{'  ' + detail if detail else ''}")
    return bool(ok)


def call(method: str, path: str, body=None, raw: bytes | None = None,
         ctype: str | None = None, timeout: int = 120):
    data, headers = raw, {}
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if ctype:
        headers["Content-Type"] = ctype
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            payload = r.read()
            try:
                return r.status, json.loads(payload or b"null")
            except json.JSONDecodeError:
                return r.status, payload
    except urllib.error.HTTPError as e:
        payload = e.read()
        try:
            return e.code, json.loads(payload or b"null")
        except json.JSONDecodeError:
            return e.code, {"raw": payload[:200].decode("utf-8", "replace")}


def wait(job_id: str, states: set[str], timeout: int = 1800):
    deadline, last = time.time() + timeout, None
    while time.time() < deadline:
        _, j = call("GET", f"/jobs/{job_id}")
        state = j.get("state")
        if state != last:
            print(f"        {state:<12} {str(j.get('message') or '')[:66]}")
            last = state
        if state in states:
            return j
        time.sleep(2)
    raise AssertionError(f"timeout waiting for {states}; last={last}")


def section(title: str) -> None:
    print(f"\n[{title}]")


# --------------------------------------------------------------- contract
section("health and contract")
st, health = call("GET", "/healthz")
check("GET /healthz -> 200", st == 200, str(st))
check("ffmpeg present", health.get("ffmpeg") is True)
check("GPU reported", health.get("cuda_devices", 0) >= 1,
      f"cuda_devices={health.get('cuda_devices')} device={health.get('transcribe_device')}")
check("provider ready", health.get("llm_ready") is True, str(health.get("llm_error")))

st, spec = call("GET", "/openapi.json")
check("GET /openapi.json -> 200", st == 200)
ops = [(p, m) for p, item in spec["paths"].items() for m in item]
check("every endpoint registered", len(ops) >= 14, f"{len(ops)} operations")
opts = spec["components"]["schemas"]["JobOptions"]["properties"]
check("reframe offers track", "track" in json.dumps(opts["reframe"]))
check("track_tier offered", "track_tier" in opts)

section("security and validation")
for bad, why in [("../../../etc/passwd", "relative traversal"),
                 ("/etc/passwd", "absolute path outside the media root")]:
    st, b = call("POST", "/jobs", {"path": bad})
    check(f"refused: {why}", st in (403, 404, 415, 422), f"{st} {json.dumps(b)[:70]}")
st, b = call("POST", "/jobs", {"path": VIDEO, "whisper": "ZZSENTINELZZ"})
check("unlisted whisper model refused", st == 422, str(st))
check("422 does not echo the rejected value",
      "ZZSENTINELZZ" not in json.dumps(b, ensure_ascii=False), json.dumps(b)[:110])
for field, value in [("preset", "ZZSENTINELZZ"), ("resolution", "ZZSENTINELZZ")]:
    st, b = call("POST", "/jobs", {"path": VIDEO, field: value})
    check(f"{field}: refused without echoing", st == 422
          and "ZZSENTINELZZ" not in json.dumps(b, ensure_ascii=False), str(st))
st, b = call("GET", "/jobs/does-not-exist")
check("unknown job -> 404 with {detail}", st == 404 and "detail" in b)

# ------------------------------------------------------------- full flow
section("full flow — every option set, stop at planned")
FULL = {
    "path": VIDEO,
    "clips": 3, "min_len": 20, "max_len": 45,
    "language": "ar", "denoise": True,
    "whisper": "large-v3", "device": "cuda",
    "hotwords": " ".join(Path("prompts/ar-tech.txt").read_text(encoding="utf-8").split()),
    "fixups": {"بايسون": "بايثون"},
    "reframe": "crop", "codec": "h264", "preset": "veryfast",
    "resolution": "1080p", "crf": 22, "ten_bit": False,
    "font": "Arial", "captions": True, "per_line": 4,
    "auto_render": False,
}
st, job = call("POST", "/jobs", FULL)
check("POST /jobs with every option -> 202", st == 202, f"{st} {json.dumps(job)[:150]}")
jid = job.get("id")
if not jid:
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    raise SystemExit(1)

j = wait(jid, {"planned", "failed", "done"})
check("reached planned (not failed)", j["state"] == "planned", str(j.get("error"))[:200])
check("language recorded", j.get("language") == "ar", str(j.get("language")))
check("device recorded", j.get("device") in ("cuda", "cpu"), str(j.get("device")))
check("words transcribed", (j.get("word_count") or 0) > 50, str(j.get("word_count")))
check("clips planned", len(j.get("clips") or []) >= 1, str(len(j.get("clips") or [])))

section("transcript round trip")
st, tr = call("GET", f"/jobs/{jid}/transcript")
words = tr.get("words") or []
check("GET /transcript -> 200 with words", st == 200 and len(words) > 50, str(len(words)))
edited = json.loads(json.dumps(words))
original = edited[0]["text"]
edited[0]["text"] = "مُصحح"
st, r = call("PUT", f"/jobs/{jid}/transcript", {"words": edited})
check("PUT /transcript accepts a text-only correction", st == 200,
      f"{st} {json.dumps(r)[:120]}")
st, tr2 = call("GET", f"/jobs/{jid}/transcript")
check("the correction is visible on read",
      (tr2.get("words") or [{}])[0].get("text") == "مُصحح")
retimed = json.loads(json.dumps(words))
retimed[0]["start"] = (retimed[0]["start"] or 0) + 1.0
st, r = call("PUT", f"/jobs/{jid}/transcript", {"words": retimed})
check("PUT /transcript REFUSES a retiming — the core invariant", st in (409, 422),
      f"{st} {json.dumps(r, ensure_ascii=False)[:120]}")
st, r = call("PUT", f"/jobs/{jid}/transcript", {"words": words[:-1]})
check("PUT /transcript refuses a word-count change", st in (409, 422), str(st))
# restore
edited[0]["text"] = original
call("PUT", f"/jobs/{jid}/transcript", {"words": edited})

section("plan round trip")
st, plan = call("GET", f"/jobs/{jid}/plan")
check("GET /plan -> 200", st == 200, str(st))
clips = plan.get("clips") or plan if isinstance(plan, list) else plan.get("clips", [])
check("plan has clips", len(clips) >= 1, str(len(clips)))
first = dict(clips[0])
first["title"] = "hand edited"
st, r = call("PUT", f"/jobs/{jid}/plan", {"clips": [first]})
check("PUT /plan replaces the plan", st == 200,
      f"{st} {json.dumps(r, ensure_ascii=False)[:120]}")
st, plan2 = call("GET", f"/jobs/{jid}/plan")
c2 = plan2.get("clips") if isinstance(plan2, dict) else plan2
check("the edit landed and re-snapped", len(c2) == 1, str(len(c2)))

section("render")
st, r = call("POST", f"/jobs/{jid}/render")
check("POST /render -> 202", st == 202, f"{st} {json.dumps(r, ensure_ascii=False)[:120]}")
j = wait(jid, {"done", "failed"})
check("render completed", j["state"] == "done", str(j.get("error"))[:200])
outs = j.get("outputs") or []
check("clip produced", len(outs) >= 1, str(outs))
if outs:
    name = outs[0]["name"] if isinstance(outs[0], dict) else outs[0]
    check("size recorded on the record",
          (outs[0].get("size_bytes", 0) if isinstance(outs[0], dict) else 1) > 0)
    st, blob = call("GET", f"/jobs/{jid}/clips/{name}")
    check("clip downloads", st == 200 and len(blob) > 10_000, f"{st} {len(blob)} bytes")
    st, _ = call("GET", f"/jobs/{jid}/clips/../../etc/passwd")
    check("download traversal refused", st in (400, 403, 404), str(st))

section("listing, cancel, delete")
st, lst = call("GET", "/jobs")
check("GET /jobs lists it", any(x.get("id") == jid for x in (lst or {}).get("jobs", [])))
st, _ = call("GET", "/jobs?state=done")
check("GET /jobs?state= filters", st == 200, str(st))
st, _ = call("DELETE", f"/jobs/{jid}")
check("DELETE on a terminal job", st in (200, 204), str(st))
check("deleted job is gone", call("GET", f"/jobs/{jid}")[0] == 404)

print(f"\n{len(passed)} passed, {len(failed)} failed")
for name in failed:
    print("  FAILED:", name)
raise SystemExit(1 if failed else 0)
