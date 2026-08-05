"""End-to-end smoke test of the qatf HTTP API.

    python tests/smoke_api.py [scratch-dir]

ffmpeg, faster-whisper and the Anthropic call are faked at the lowest possible
level (`pipeline.audio.run`, `pipeline.encode.run`, `pipeline.asr.transcribe`,
`pipeline.select.pick_clips`) so everything above them — the job state machine,
the transcript cache, snap, build_ass, filtergraph, the plan round trip and every
endpoint — runs for real. Needs no ffmpeg, no GPU and no API key; it therefore
proves nothing about those four functions themselves.

Settings are injected directly rather than through os.environ, which is the
point of `create_app(settings=...)`.

Exits non-zero on any failure.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from pathlib import Path

from _harness import check, report, section  # also puts the project root on sys.path
from fastapi.testclient import TestClient

from qatf.api import create_app
from qatf.core.config import Settings
from qatf.core.types import Clip, Transcript, Word
from qatf.jobs import JobStore
from qatf.pipeline import asr, audio, encode, select

SCRATCH = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
    tempfile.mkdtemp(prefix="qatf-smoke-"))
MEDIA = SCRATCH / "media"
MEDIA.mkdir(parents=True, exist_ok=True)
(MEDIA / "talk.mp4").write_bytes(b"not really a video")

SETTINGS = Settings(
    data_dir=SCRATCH / "data",
    media_root=MEDIA.resolve(),
    workers=2,
    max_upload_bytes=1024 * 1024,
    llm_provider="anthropic",
    llm_model="claude-sonnet-5",
)

# ---- fakes ---------------------------------------------------------------
RENDERED: list[list[str]] = []


def fake_run(cmd, quiet=True):
    out = Path(cmd[-1])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"\0" * 1024)
    if out.suffix == ".mp4":
        RENDERED.append(cmd)


def fake_transcribe(wav, model_size, device, language,
                    initial_prompt=None, hotwords=None):
    SEEN_PROMPTS.append(hotwords or initial_prompt)
    return Transcript(
        words=[Word(f"word{i}", i * 0.5, i * 0.5 + 0.45) for i in range(260)],
        language=language or "ar",
        language_probability=0.98,
        device="cuda",
        compute_type="float16",
    )


def fake_pick_clips(words, n, lo, hi, model=None):
    SEEN_MODELS.append(model)
    return [
        Clip(10.0, 50.0, "First {clip} title", "a hook", "why", 0.9),
        Clip(60.3, 110.7, "ثاني مقطع", "hook two", "why two", 0.7),
        Clip(0.0, 3.0, "too short to survive", "", "", 0.5),
    ]


SEEN_MODELS: list[str | None] = []
SEEN_PROMPTS: list[str | None] = []
audio.run = fake_run
encode.run = fake_run
asr.transcribe = fake_transcribe
select.pick_clips = fake_pick_clips


def wait(client, job_id, states, timeout=30):
    deadline = time.time() + timeout
    job = {}
    while time.time() < deadline:
        job = client.get(f"/jobs/{job_id}").json()
        if job["state"] in states:
            return job
        time.sleep(0.05)
    raise AssertionError(f"timeout; last state={job.get('state')} error={job.get('error')}")


app = create_app(SETTINGS)

with TestClient(app) as client:
    section("routes")
    # via the OpenAPI schema, not app.routes: since FastAPI 0.141 include_router
    # wraps each router in an _IncludedRouter with no .path, so walking
    # app.routes shows only the four built-ins and silently hides everything.
    # Generating the schema also proves every response_model resolves.
    paths = app.openapi()["paths"]
    for path in sorted(paths):
        print("   ", path)
    expected = {
        "/healthz", "/jobs", "/jobs/upload", "/jobs/{job_id}", "/jobs/{job_id}/cancel",
        "/jobs/{job_id}/transcript", "/jobs/{job_id}/plan", "/jobs/{job_id}/render",
        "/jobs/{job_id}/clips", "/jobs/{job_id}/clips/{name}",
    }
    check("every endpoint registered", expected <= set(paths),
          f"missing: {sorted(expected - set(paths))}")
    check("job creation is documented as async",
          "202" in paths["/jobs"]["post"]["responses"],
          str(sorted(paths["/jobs"]["post"]["responses"])))

    section("openapi document")
    # The schema is the client contract and the /docs page at once. These checks
    # exist because all three failures below are silent: a route with no summary
    # renders as a bare path, an auto-derived operationId ships as a method name
    # in every generated client, and an undeclared error status becomes a case
    # the client has no type for.
    spec = app.openapi()
    ops = [(p, m, op) for p, item in spec["paths"].items()
           for m, op in item.items()]
    check("every operation has a summary",
          all(op.get("summary") for _, _, op in ops),
          str([f"{m} {p}" for p, m, op in ops if not op.get("summary")]))
    check("every operation has a description",
          all(op.get("description") for _, _, op in ops),
          str([f"{m} {p}" for p, m, op in ops if not op.get("description")]))
    check("every operation is tagged",
          all(op.get("tags") for _, _, op in ops))
    ids = [op.get("operationId") for _, _, op in ops]
    check("operationIds are hand-named, not derived",
          all(i and "__" not in i and not i.endswith(("_get", "_post", "_put",
                                                      "_delete")) for i in ids),
          str(ids))
    check("operationIds are unique", len(set(ids)) == len(ids))
    tag_names = {t["name"] for t in spec.get("tags", [])}
    used = {t for _, _, op in ops for t in op.get("tags", [])}
    check("every tag used is described", used <= tag_names,
          f"undescribed: {sorted(used - tag_names)}")

    # the failures a caller will actually hit, declared where they happen
    declared = {(p, m): set(op["responses"]) for p, m, op in ops}
    check("media-root refusal documented on POST /jobs",
          "403" in declared[("/jobs", "post")], str(declared[("/jobs", "post")]))
    check("upload limit documented on POST /jobs/upload",
          {"413", "415"} <= declared[("/jobs/upload", "post")],
          str(declared[("/jobs/upload", "post")]))
    check("conflict documented on the mutating job routes",
          all("409" in declared[(p, m)] for p, m in [
              ("/jobs/{job_id}", "delete"), ("/jobs/{job_id}/cancel", "post"),
              ("/jobs/{job_id}/plan", "put"), ("/jobs/{job_id}/render", "post")]))
    err_ref = "#/components/schemas/ErrorResponse"
    check("errors are typed, not untyped bodies",
          spec["paths"]["/jobs"]["post"]["responses"]["403"]
              ["content"]["application/json"]["schema"]["$ref"] == err_ref)
    # merge() joins descriptions rather than letting the last group win — two
    # different 409s on PUT /plan must both survive
    plan_409 = spec["paths"]["/jobs/{job_id}/plan"]["put"]["responses"]["409"]["description"]
    check("merged status codes keep every description",
          "running" in plan_409 and "transcript" in plan_409, plan_409)

    schemas = spec["components"]["schemas"]
    documented = ["JobOptions", "JobCreate", "ClipModel", "PlanUpdate",
                  "JobResponse", "TranscriptResponse", "Health", "ErrorResponse"]
    check("every request/response model carries an example",
          all("examples" in schemas[n] or "example" in schemas[n] for n in documented),
          str([n for n in documented
               if "examples" not in schemas[n] and "example" not in schemas[n]]))
    check("field descriptions reach the schema",
          "52" in schemas["JobOptions"]["properties"]["max_len"]["description"])
    check("the description explains the state machine",
          "planned" in spec["info"]["description"]
          and "auto_render" in spec["info"]["description"])

    section("health")
    health = client.get("/healthz").json()
    check("healthz responds", "status" in health)
    check("reports injected settings",
          health["media_root"] == str(MEDIA.resolve()) and health["max_workers"] == 2,
          json.dumps(health))
    check("reports the configured model", health["model"] == "claude-sonnet-5")

    section("path job, auto_render")
    r = client.post("/jobs", json={"path": "talk.mp4", "clips": 3, "language": "ar",
                                   "font": "Noto Naskh Arabic", "reframe": "blur",
                                   "hotwords": "بايثون فلاتر", "denoise": True,
                                   # word25 sits at t=12.5s, inside the first
                                   # selected clip (10-50s); word3 would not be
                                   "fixups": {"word25": "FIXED"}})
    check("202 accepted", r.status_code == 202, str(r.status_code))
    jid = r.json()["id"]
    job = wait(client, jid, {"done", "failed"})
    check("reached done", job["state"] == "done", job.get("error") or "")
    check("language recorded", job["language"] == "ar")
    check("word count recorded", job["word_count"] == 260, str(job["word_count"]))
    check("short clip filtered out", len(job["clips"]) == 2, str(len(job["clips"])))
    check("two clips rendered", len(job["outputs"]) == 2, str(len(job["outputs"])))
    check("clip snapped to word bounds",
          abs(job["clips"][0]["start"] - (10.0 - 0.15)) < 0.3, str(job["clips"][0]["start"]))
    check("settings model reached stage 3", SEEN_MODELS[0] == "claude-sonnet-5",
          str(SEEN_MODELS))
    check("hotwords reached stage 2", SEEN_PROMPTS[0] == "بايثون فلاتر",
          str(SEEN_PROMPTS))
    check("device actually used is reported", job["device"] == "cuda",
          str(job.get("device")))
    check("blur filtergraph used", any("gblur" in " ".join(c) for c in RENDERED))
    check("non-ascii title slug falls back",
          any(o["name"].endswith("-clip.mp4") for o in job["outputs"]),
          str([o["name"] for o in job["outputs"]]))

    section("ass output")
    ass_files = list((SETTINGS.data_dir / jid / ".work").glob("*.ass"))
    check("ass files written", len(ass_files) == 2, str(len(ass_files)))
    check("denoise produced its own cached wav, not audio.wav",
          (SETTINGS.data_dir / jid / ".work" / "audio-denoised.wav").exists())
    all_ass = "\n".join(f.read_text(encoding="utf-8") for f in ass_files)
    check("fixups reached the burned-in captions",
          "FIXED" in all_ass and "word25" not in all_ass)
    body = ass_files[0].read_text(encoding="utf-8")
    check("WrapStyle is 0", "WrapStyle: 0" in body)
    check("braces escaped in caption text", "{clip}" not in body.split("[Events]")[1])
    check("requested font used", "Pop,Noto Naskh Arabic," in body)

    section("download")
    name = job["outputs"][0]["name"]
    check("clip downloads", client.get(f"/jobs/{jid}/clips/{name}").status_code == 200)
    check("traversal blocked",
          client.get(f"/jobs/{jid}/clips/..%2F..%2Fjob.json").status_code in (400, 404))

    section("transcript")
    t = client.get(f"/jobs/{jid}/transcript").json()
    check("transcript served", t["word_count"] == 260 and t["language"] == "ar")

    section("plan round trip")
    edited = [{"start": 20.0, "end": 61.0, "title": "hand edited",
               "hook": "", "why": "", "score": 1.0}]
    r = client.put(f"/jobs/{jid}/plan", json={"clips": edited})
    check("plan replaced", r.status_code == 200, str(r.status_code))
    check("edit re-snapped to word times",
          abs(r.json()[0]["start"] - (20.0 - 0.15)) < 0.3, str(r.json()[0]["start"]))
    check("state back to planned", client.get(f"/jobs/{jid}").json()["state"] == "planned")
    r = client.put(f"/jobs/{jid}/plan", json={"clips": edited, "snap": False})
    check("snap:false leaves boundaries alone", r.json()[0]["start"] == 20.0,
          str(r.json()[0]["start"]))
    check("backwards clip rejected",
          client.put(f"/jobs/{jid}/plan",
                     json={"clips": [{"start": 9, "end": 3, "title": "x"}]}
                     ).status_code == 422)
    check("empty plan rejected",
          client.put(f"/jobs/{jid}/plan", json={"clips": []}).status_code == 422)

    r = client.post(f"/jobs/{jid}/render")
    check("render accepted", r.status_code == 202, str(r.status_code))
    job = wait(client, jid, {"done", "failed"})
    check("re-render replaced outputs",
          job["state"] == "done" and len(job["outputs"]) == 1,
          f"{job['state']} {len(job['outputs'])} {job.get('error')}")
    check("no model call on re-render", len(SEEN_MODELS) == 1, str(SEEN_MODELS))

    section("upload job, auto_render off")
    with open(MEDIA / "talk.mp4", "rb") as fh:
        r = client.post("/jobs/upload",
                        files={"file": ("clip.mp4", fh, "video/mp4")},
                        data={"options": json.dumps({"auto_render": False,
                                                     "device": "cpu", "per_line": 2})})
    check("upload accepted", r.status_code == 202, r.text[:200])
    uid = r.json()["id"]
    job = wait(client, uid, {"planned", "failed", "done"})
    check("stops at planned", job["state"] == "planned", job.get("error") or "")
    check("no clips rendered yet", job["outputs"] == [])
    check("upload stored in job dir", "source" in job["video"])
    check("per_line carried through", job["options"]["per_line"] == 2)

    section("validation and errors")
    check("bad extension rejected",
          client.post("/jobs/upload", files={"file": ("x.txt", b"a", "text/plain")},
                      data={"options": "{}"}).status_code == 415)
    big = b"\0" * (SETTINGS.max_upload_bytes + 1024)
    check("oversize upload rejected",
          client.post("/jobs/upload", files={"file": ("big.mp4", big, "video/mp4")},
                      data={"options": "{}"}).status_code == 413)
    check("malformed options rejected",
          client.post("/jobs/upload", files={"file": ("a.mp4", b"a", "video/mp4")},
                      data={"options": "not json"}).status_code == 422)
    check("path escape rejected",
          client.post("/jobs", json={"path": "../../etc/passwd"}).status_code == 403)
    check("absolute path escape rejected",
          client.post("/jobs", json={"path": str(SCRATCH / "data")}).status_code == 403)
    check("missing file 404",
          client.post("/jobs", json={"path": "nope.mp4"}).status_code == 404)
    check("min_len > max_len rejected",
          client.post("/jobs", json={"path": "talk.mp4", "min_len": 90,
                                     "max_len": 30}).status_code == 422)
    check("unknown reframe rejected",
          client.post("/jobs", json={"path": "talk.mp4",
                                     "reframe": "zoom"}).status_code == 422)
    check("unknown codec rejected",
          client.post("/jobs", json={"path": "talk.mp4",
                                     "codec": "av1"}).status_code == 422)
    check("bad resolution rejected at the boundary, not mid-job",
          client.post("/jobs", json={"path": "talk.mp4",
                                     "resolution": "enormous"}).status_code == 422)
    check("valid resolution forms accepted",
          all(client.post("/jobs", json={"path": "talk.mp4", "auto_render": False,
                                         "resolution": r}).status_code == 202
              for r in ("source", "4k", "1216x2160")))
    check("unknown job 404", client.get("/jobs/deadbeef").status_code == 404)
    check("cancel on finished job 409", client.post(f"/jobs/{jid}/cancel").status_code == 409)

    section("listing and deletion")
    check("list returns jobs", len(client.get("/jobs").json()["jobs"]) >= 2)
    check("list filters by state",
          all(j["state"] == "planned"
              for j in client.get("/jobs", params={"state": "planned"}).json()["jobs"]))
    check("delete works", client.delete(f"/jobs/{uid}").status_code == 204)
    check("deleted job gone", client.get(f"/jobs/{uid}").status_code == 404)
    check("deleted job dir removed", not (SETTINGS.data_dir / uid).exists())

    section("restart recovery")
    store = JobStore(SETTINGS.data_dir)
    check("jobs reloaded from disk", store.get(jid) is not None)
    check("reloaded job keeps terminal state", store.get(jid).state == "done")
    store.shutdown()

raise SystemExit(report())
