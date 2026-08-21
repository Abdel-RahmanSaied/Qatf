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

from qatf import pipeline
from qatf.api import create_app
from qatf.core.config import EDITABLE, Settings
from qatf.core.types import Clip, Keyframe, Track, Transcript, Word
from qatf.jobs import RUNNING_STATE_VALUES, JobStore, worker
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
                    initial_prompt=None, hotwords=None, decode=None):
    SEEN_PROMPTS.append(hotwords or initial_prompt)
    return Transcript(
        words=[Word(f"word{i}", i * 0.5, i * 0.5 + 0.45) for i in range(260)],
        language=language or "ar",
        language_probability=0.98,
        device="cuda",
        compute_type="float16",
    )


def fake_pick_clips(words, n, lo, hi, model=None, settings=None):
    SEEN_MODELS.append(model)
    # Stage 3 is the only part of the pipeline that opens a network connection
    # and spends a credential, so it must receive the settings the app was built
    # with. It used to reach for the process-wide get_settings() instead, which
    # made create_app(settings=...) a half-truth exactly where it mattered most.
    SEEN_SETTINGS.append(settings)
    return [
        Clip(10.0, 50.0, "First {clip} title", "a hook", "why", 0.9),
        Clip(60.3, 110.7, "ثاني مقطع", "hook two", "why two", 0.7),
        Clip(0.0, 3.0, "too short to survive", "", "", 0.5),
    ]


SEEN_MODELS: list[str | None] = []
SEEN_SETTINGS: list = []
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


def settle(client, timeout=30):
    """Block until no job is mid-pipeline.

    A few validation checks fire a job with `auto_render: False` and only
    assert on the immediate 202/422 — they never wait for it to reach
    `planned`, so it keeps running on the worker pool in the background. That
    was harmless when persistence was a plain `write_text` (fast enough that
    the job settled long before any later section looked at it), but a SQLite
    transaction per state transition is measurably slower, so the same
    orphaned job can now still be mid-`selecting` when a later, unrelated
    check counts model calls — making that check flaky for a reason that has
    nothing to do with what it is testing. Called right before any check that
    depends on the total number of model calls so far."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        jobs = client.get("/jobs").json()["jobs"]
        if not any(j["state"] in RUNNING_STATE_VALUES for j in jobs):
            return
        time.sleep(0.05)
    raise AssertionError("jobs still running after timeout")


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

    # The licence the API SERVES. It is the only licence declaration in the
    # repo that goes out over the network — into every generated client and
    # every scanner that reads /openapi.json — and it sat at "MIT" while
    # LICENSE, NOTICE, pyproject.toml, package.json and the README all said
    # Apache-2.0. Nothing could have caught that but an assertion here.
    check("the served OpenAPI licence matches the project's",
          spec.get("info", {}).get("license", {}).get("identifier") == "Apache-2.0",
          str(spec.get("info", {}).get("license")))

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

    def typed_as_error(response: dict) -> bool:
        schema = (response.get("content", {})
                  .get("application/json", {}).get("schema", {}))
        return schema.get("$ref") == err_ref

    # EVERY declared failure, not one spot-check. This assertion used to look at
    # exactly one response — POST /jobs 403 — and passed while POST
    # /jobs/{id}/cancel declared its 409 inline as a bare description with no
    # `model`, so that code reached the schema with no ErrorResponse $ref and a
    # generated client had no error type for it. A single sample cannot see a
    # route that hand-rolls its own declaration; the sweep can, and it is the
    # sweep that makes `qatf/api/openapi.py` the only way to declare a failure.
    untyped = sorted(f"{m.upper()} {p} -> {code}"
                     for p, m, op in ops
                     for code, response in op.get("responses", {}).items()
                     if code[:1] in ("4", "5") and not typed_as_error(response))
    check("every declared failure is typed as ErrorResponse", not untyped, str(untyped))
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
    # NOTHING is filtered for length. The 3s clip the fake proposes used to be
    # deleted here; six of eight on a real run were 24s shorts that were fine to
    # publish, discarded unseen for missing 30s by four seconds. They stay in the
    # plan now, labelled, and the operator decides.
    check("the short clip stays in the plan", len(job["clips"]) == 3,
          str(len(job["clips"])))
    check("and is rendered like any other", len(job["outputs"]) == 3,
          str(len(job["outputs"])))
    _by_title = {c["title"]: c for c in job["clips"]}
    check("the short clip is labelled short on the wire",
          _by_title["too short to survive"]["out_of_range"] == "short",
          str(_by_title["too short to survive"]))
    check("an in-range clip carries no label",
          _by_title["First {clip} title"]["out_of_range"] is None,
          str(_by_title["First {clip} title"]["out_of_range"]))
    check("the terminal message says some were kept out of range",
          "outside" in job["message"] and "kept" in job["message"], job["message"])
    check("clip snapped to word bounds",
          abs(job["clips"][0]["start"] - (10.0 - 0.15)) < 0.3, str(job["clips"][0]["start"]))
    check("settings model reached stage 3", SEEN_MODELS[0] == "claude-sonnet-5",
          str(SEEN_MODELS))
    # Stage 3 now receives a DERIVED object — `settings_for_job()` seeds from
    # the injected one and layers saved overrides on top — so identity no
    # longer holds and asserting it would only be testing that no feature was
    # added. Equality is the stronger claim: every value must survive the trip,
    # or `create_app(settings=...)` is a half-truth again. It also pins
    # `_settings_as_env` round-tripping losslessly, which nothing else does.
    check("the INJECTED settings reached stage 3, not the process-wide ones",
          SEEN_SETTINGS[0] == SETTINGS, repr(SEEN_SETTINGS[0]))
    check("and those really are the injected values, not the environment's",
          SEEN_SETTINGS[0].data_dir == SETTINGS.data_dir
          and SEEN_SETTINGS[0].data_dir != Settings.from_env().data_dir,
          f"{SEEN_SETTINGS[0].data_dir} vs env {Settings.from_env().data_dir}")
    check("hotwords reached stage 2", SEEN_PROMPTS[0] == "بايثون فلاتر",
          str(SEEN_PROMPTS))
    check("device actually used is reported", job["device"] == "cuda",
          str(job.get("device")))
    check("blur filtergraph used", any("gblur" in " ".join(c) for c in RENDERED))
    # null, NOT a zeroed reading: "this job downloaded nothing" and "this job is
    # 0 bytes into a download" are different facts, and only a youtube-sourced
    # job can be in the second. A client that cannot tell them apart draws an
    # empty progress bar on every upload.
    check("a job that never fetched reports null progress, not zero",
          job["fetch_progress"] is None, json.dumps(job.get("fetch_progress")))
    check("non-ascii title slug falls back",
          any(o["name"].endswith("-clip.mp4") for o in job["outputs"]),
          str([o["name"] for o in job["outputs"]]))

    section("ass output")
    ass_files = list((SETTINGS.data_dir / jid / ".work").glob("*.ass"))
    check("an ass file per clip, including the out-of-range one",
          len(ass_files) == 3, str(len(ass_files)))
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
    check("served transcript already has fixups applied — what you read is what "
          "gets burned in", t["words"][25]["text"] == "FIXED", t["words"][25]["text"])

    section("transcript correction round trip")
    # word30 sits at t=15.0s, inside the first selected clip (10-50s).
    cuts_before = json.dumps(client.get(f"/jobs/{jid}").json()["clips"])
    pristine = [dict(w) for w in t["words"]]
    words = [dict(w) for w in t["words"]]
    words[30]["text"] = "CORRECTED"
    r = client.put(f"/jobs/{jid}/transcript", json={"words": words})
    check("correction accepted", r.status_code == 200, r.text[:200])
    check("one correction recorded", r.json()["edits_applied"] == 1, r.text[:200])
    check("correction visible in the served transcript",
          r.json()["words"][30]["text"] == "CORRECTED")
    check("fixups still applied alongside it", r.json()["words"][25]["text"] == "FIXED")
    check("done job drops back to planned — its clips now caption stale text",
          client.get(f"/jobs/{jid}").json()["state"] == "planned")
    check("CUT POINTS UNCHANGED by a text correction — the core invariant",
          json.dumps(client.get(f"/jobs/{jid}").json()["clips"]) == cuts_before)

    retimed = [dict(w) for w in words]
    retimed[30]["start"] = retimed[30]["start"] - 0.5
    check("retiming a word is refused",
          client.put(f"/jobs/{jid}/transcript", json={"words": retimed}).status_code == 422)
    check("adding a word is refused",
          client.put(f"/jobs/{jid}/transcript",
                     json={"words": [*words, {"text": "x", "start": 999.0, "end": 999.5}]}
                     ).status_code == 422)
    check("removing a word is refused",
          client.put(f"/jobs/{jid}/transcript",
                     json={"words": words[:-1]}).status_code == 422)
    check("timings survived every refusal",
          client.get(f"/jobs/{jid}/transcript").json()["words"][30]["start"] == 15.0)

    r = client.post(f"/jobs/{jid}/render")
    job = wait(client, jid, {"done", "failed"})
    check("re-render after a correction succeeds", job["state"] == "done",
          job.get("error") or "")
    corrected_ass = "\n".join(
        f.read_text(encoding="utf-8")
        for f in (SETTINGS.data_dir / jid / ".work").glob("*.ass"))
    check("correction reached the burned-in captions",
          "CORRECTED" in corrected_ass and "word30" not in corrected_ass)
    check("still no model call — corrections are stage 5 only",
          len(SEEN_MODELS) == 1, str(SEEN_MODELS))
    # The overlay now lives in SQLite too (qatf.db in the same .work directory,
    # word_edits table scoped by job id), not a word-edits.json file — read it
    # back directly with a fresh connection rather than through qatf.core.db, so
    # this check does not share a cached thread-local handle with the app under
    # test (see qatf.core.db.close's docstring for why that matters).
    import sqlite3 as _sqlite3_edits

    def _overlay_rows(job_id: str) -> int:
        con = _sqlite3_edits.connect(SETTINGS.data_dir / job_id / ".work" / "qatf.db")
        try:
            return con.execute(
                "SELECT COUNT(*) FROM word_edits WHERE scope=?", (job_id,)).fetchone()[0]
        finally:
            con.close()

    # the transcript cache lives in SQLite (qatf.db in the same .work
    # directory), not a words-*.json file — read it back the same way
    # transcript_for does, with the key built from this job's own options, and
    # check the correction never reached the raw row
    _raw_key = pipeline.cache_key("large-v3", "ar", None, "بايثون فلاتر")
    _raw_cached = pipeline.read_cache(SETTINGS.data_dir / jid / ".work", _raw_key)
    check("overlay stored beside the cache, not inside it",
          _overlay_rows(jid) == 1
          and _raw_cached is not None
          and all(w.text != "CORRECTED" for w in _raw_cached.words))

    r = client.put(f"/jobs/{jid}/transcript", json={"words": pristine})
    check("re-submitting the untouched transcript clears corrections",
          r.json()["edits_applied"] == 0 and r.json()["words"][30]["text"] == "word30")
    check("cleared overlay is removed, not left empty", _overlay_rows(jid) == 0)

    section("transcript correction — a PUT before any GET marks the scope (finding 2)")
    # `edits.save` never wrote the `imported` marker before this fix — only the
    # legacy-import path did — and `put_transcript` never calls `load` first.
    # A pre-SQLite job whose word-edits.json still exists could take a PUT
    # with no GET ever having run, leave the marker unset, and have the
    # documented undo (PUT the pristine transcript back) silently revert
    # itself on the very next read. Reproduce it with nothing but the public
    # endpoints: a fresh job, a legacy file dropped into its .work directory
    # the way an old install would have left one, and PUT twice with no GET
    # ever in between.
    nid = client.post("/jobs", json={"path": "talk.mp4", "auto_render": False,
                                     "device": "cpu"}).json()["id"]
    wait(client, nid, {"planned", "failed", "done"})
    nwork = SETTINGS.data_dir / nid / ".work"
    (nwork / pipeline.edits.FILENAME).write_text(
        json.dumps({"edits": [{"index": 7, "was": "word7", "text": "LEGACY-GHOST"}]}),
        encoding="utf-8")
    # fake_transcribe's output is deterministic (word0..word259 at i*0.5s), so
    # the baseline is known here without ever reading it back through GET.
    npristine = [{"text": f"word{i}", "start": i * 0.5, "end": i * 0.5 + 0.45}
                for i in range(260)]
    ncorrected = [dict(w) for w in npristine]
    ncorrected[10]["text"] = "APICORRECT"
    r1 = client.put(f"/jobs/{nid}/transcript", json={"words": ncorrected})
    check("PUT with no prior GET is accepted", r1.status_code == 200, r1.text[:200])
    r2 = client.put(f"/jobs/{nid}/transcript", json={"words": npristine})
    check("clearing it — still no GET ever run — is accepted", r2.status_code == 200,
          r2.text[:200])
    served = client.get(f"/jobs/{nid}/transcript").json()
    check("THE CLEAR STICKS ON THE FIRST READ — it must not resurrect the "
          "legacy file's correction just because save() ran with no load() "
          "before it, and must not resurrect the API correction either",
          served["words"][7]["text"] == "word7"
          and served["words"][10]["text"] == "word10",
          str(served["words"][7:11]))

    from qatf.core.types import Word as _W
    from qatf.jobs import worker as _worker

    class _T:
        words = [_W("a", 0.0, 0.1)] + [_W("dup", 0.1 + i / 10, 0.2 + i / 10)
                                       for i in range(5)]
    # baseline_words now returns (words, blanked) instead of discarding the
    # repair count — see qatf/jobs/worker.py — so every caller unpacks a pair.
    _base, _base_blanked = _worker.baseline_words(_T(), {})
    check("repair reaches the read path, so captions and GET /transcript agree",
          [w.text for w in _base] == ["a", "dup", "", "", "", ""],
          str([w.text for w in _base]))
    check("and the word count the PUT contract depends on is unchanged",
          len(_base) == 6)

    section("output sizes come from the record, not the filesystem")
    # to_response used to stat() every output on every read, which measured 75%
    # of GET /jobs and scaled with the job count on the endpoint clients poll.
    # The record now lives in qatf.db, not job.json, so it is read and rewritten
    # through the database directly here rather than through the filesystem.
    import sqlite3 as _sqlite3_rec

    def _read_doc(job_id: str) -> dict:
        con = _sqlite3_rec.connect(SETTINGS.data_dir / "qatf.db")
        try:
            return json.loads(con.execute(
                "SELECT doc FROM jobs WHERE id=?", (job_id,)).fetchone()[0])
        finally:
            con.close()

    def _write_doc(job_id: str, doc: dict) -> None:
        con = _sqlite3_rec.connect(SETTINGS.data_dir / "qatf.db")
        try:
            con.execute("UPDATE jobs SET doc=? WHERE id=?",
                       (json.dumps(doc, ensure_ascii=False), job_id))
            con.commit()
        finally:
            con.close()

    rec = _read_doc(jid)
    check("worker recorded a size per rendered clip",
          set(rec["output_sizes"]) == set(rec["outputs"]) and rec["outputs"],
          str(rec.get("output_sizes")))
    listed = client.get(f"/jobs/{jid}").json()["outputs"]
    check("sizes reach the wire", all(o["size_bytes"] > 0 for o in listed),
          str(listed))
    # delete the file: a stat-based implementation reports 0, a record-based one
    # still reports the truth. This is the check that proves the syscall is gone.
    victim = SETTINGS.data_dir / jid / "clips" / listed[0]["name"]
    kept = victim.read_bytes()
    victim.unlink()
    after = client.get(f"/jobs/{jid}").json()["outputs"][0]["size_bytes"]
    check("no stat() on the read path", after == listed[0]["size_bytes"],
          f"{after} vs {listed[0]['size_bytes']}")
    victim.write_bytes(kept)
    # a record written before output_sizes existed must still report the truth
    legacy_id = client.post("/jobs", json={"path": "talk.mp4", "auto_render": False,
                                           "device": "cpu"}).json()["id"]
    wait(client, legacy_id, {"planned", "failed", "done"})
    (SETTINGS.data_dir / legacy_id / "clips").mkdir(parents=True, exist_ok=True)
    (SETTINGS.data_dir / legacy_id / "clips" / "99-legacy.mp4").write_bytes(b"x" * 4096)
    rec2 = _read_doc(legacy_id)
    rec2["outputs"] = ["99-legacy.mp4"]
    rec2.pop("output_sizes", None)
    _write_doc(legacy_id, rec2)
    # a fresh store reads whatever is in the database now, same as the running
    # one would — there is no in-memory cache left to go stale between them.
    app2 = create_app(SETTINGS)
    with TestClient(app2) as c2:
        legacy = c2.get(f"/jobs/{legacy_id}").json()["outputs"]
    check("a record with no stored sizes falls back to stat, not zero",
          legacy and legacy[0]["size_bytes"] == 4096, str(legacy))

    section("track mode reaches the encoder")
    # REFRAME_MODES advertised three modes while JobOptions offered two, so the
    # server had no path to the new stage at all — and the moment `track` was
    # added to the schema without wiring the worker, every job would have died
    # at stage 5 with a 500 AFTER transcribing. These checks fail if either half
    # is missing.
    _opts = spec["components"]["schemas"]["JobOptions"]["properties"]
    check("track is offered on the wire",
          "track" in _opts["reframe"]["enum"], str(_opts["reframe"]))
    check("the tier is offered with it", "track_tier" in _opts)

    SEEN_TRACKING: list[tuple] = []

    def fake_track_clips(video, clips, work, src, *, tier="balanced"):
        SEEN_TRACKING.append((src, tier, len(clips)))
        return [Track(keyframes=[Keyframe(0.0, 0.30), Keyframe(1.0, 0.60)],
                      detector="yunet", tier=tier, coverage=0.9)
                for _ in clips]

    real_track_clips, real_probe = pipeline.track_clips, worker.probe_video
    pipeline.track_clips = fake_track_clips
    worker.probe_video = lambda path: {"width": 1920, "height": 1080}
    RENDERED.clear()
    try:
        tid = client.post("/jobs", json={"path": "talk.mp4", "reframe": "track",
                                         "track_tier": "best", "device": "cpu"}
                          ).json()["id"]
        tjob = wait(client, tid, {"done", "failed"})
    finally:
        pipeline.track_clips, worker.probe_video = real_track_clips, real_probe

    check("a track job runs to completion", tjob["state"] == "done", str(tjob.get("error")))
    check("stage 4c got the SOURCE dimensions, not the output ones — the crop "
          "path is normalised against the source frame",
          bool(SEEN_TRACKING) and SEEN_TRACKING[0][0] == (1920, 1080),
          str(SEEN_TRACKING))
    check("the requested tier reaches stage 4b rather than the default",
          bool(SEEN_TRACKING) and SEEN_TRACKING[0][1] == "best", str(SEEN_TRACKING))
    check("the solved track reaches ffmpeg as a sendcmd script",
          bool(RENDERED) and any("sendcmd=f=" in " ".join(cmd) for cmd in RENDERED),
          f"{len(RENDERED)} renders")

    section("input validation at the boundary")
    # `language` lands in the transcript cache FILENAME, so a traversal there is
    # an arbitrary file write, not a cosmetic bug.
    check("traversal in language rejected",
          client.post("/jobs", json={"path": "talk.mp4",
                                     "language": "../../../../tmp/pwned"}
                      ).status_code == 422)
    check("a real language code still accepted",
          client.post("/jobs", json={"path": "talk.mp4", "language": "ar",
                                     "auto_render": False}).status_code == 202)
    # WhisperModel takes a "size OR path" — a free string here chooses what code
    # the server downloads and loads.
    for bad in ["evil-user/backdoored-ct2-model", "/etc", "../../secrets"]:
        check(f"whisper {bad!r} rejected",
              client.post("/jobs", json={"path": "talk.mp4", "whisper": bad}
                          ).status_code == 422)
    check("a real whisper size still accepted",
          client.post("/jobs", json={"path": "talk.mp4", "whisper": "small",
                                     "auto_render": False}).status_code == 202)

    # a plan bypasses JobOptions.clips entirely — without a cap, one request
    # queues thousands of ffmpeg encodes
    flood = {"clips": [{"start": i, "end": i + 1, "title": "x"} for i in range(5000)]}
    check("oversized plan rejected",
          client.put(f"/jobs/{jid}/plan", json=flood).status_code == 422)
    # `1e999` parses as inf, which satisfies gt=0 and end>start, persists into
    # the plan and reaches ffmpeg as `-t inf`. Sent as a raw body because a JSON
    # encoder refuses to emit it — which is exactly why it has to be caught
    # server-side rather than assumed impossible.
    JSON = {"content-type": "application/json"}
    check("infinite clip end rejected",
          client.put(f"/jobs/{jid}/plan", headers=JSON,
                     content='{"clips": [{"start": 0, "end": 1e999, "title": "x"}]}'
                     ).status_code == 422)
    check("NaN clip end rejected",
          client.put(f"/jobs/{jid}/plan", headers=JSON,
                     content='{"clips": [{"start": 0, "end": NaN, "title": "x"}]}'
                     ).status_code == 422)
    check("absurd clip length rejected",
          client.put(f"/jobs/{jid}/plan",
                     json={"clips": [{"start": 0, "end": 10_000_000, "title": "x"}]}
                     ).status_code == 422)
    check("non-finite word timing rejected",
          client.put(f"/jobs/{jid}/transcript", headers=JSON,
                     content='{"words": [{"text": "x", "start": 1e999, "end": 2}]}'
                     ).status_code == 422)

    # FastAPI's default 422 embeds the offending input, which (a) cannot be
    # serialised when it is inf and (b) reflects caller content back
    bad = client.put(f"/jobs/{jid}/plan", headers=JSON,
                     content='{"clips": [{"start": 0, "end": 1e999, "title": "SENTINEL"}]}')
    check("a validation error is the documented {detail: string} shape",
          isinstance(bad.json().get("detail"), str), bad.text[:200])
    check("the rejected input is not echoed back", "SENTINEL" not in bad.text, bad.text[:200])

    # The check above only exercises the error FastAPI composes, where the
    # handler strips the `input` field. A validator that formats the value into
    # its OWN message bypasses that entirely — the value comes back as part of
    # the reason. All three of these reflected caller content while the check
    # above passed, and a live server is what surfaced it. Each field is listed
    # separately so a regression names the one that broke.
    for field, value in [("whisper", "ZZSENTINELZZ"), ("preset", "ZZSENTINELZZ"),
                         ("resolution", "ZZSENTINELZZ")]:
        r = client.post("/jobs", json={"path": "talk.mp4", field: value})
        check(f"{field}: rejected", r.status_code == 422, str(r.status_code))
        check(f"{field}: custom validator does not echo the value either",
              "ZZSENTINELZZ" not in r.text, r.text[:150])
        check(f"{field}: the caller is still told what IS allowed",
              len(r.json().get("detail", "")) > 20, r.text[:120])
    check("the reason and location still reach the caller",
          "end" in bad.json()["detail"], bad.json()["detail"][:160])

    section("plan round trip")
    edited = [{"start": 20.0, "end": 61.0, "title": "hand edited",
               "hook": "", "why": "", "score": 1.0}]
    r = client.put(f"/jobs/{jid}/plan", json={"clips": edited})
    check("plan replaced", r.status_code == 200, str(r.status_code))
    check("edit re-snapped to word times",
          abs(r.json()[0]["start"] - (20.0 - 0.15)) < 0.3, str(r.json()[0]["start"]))
    check("state back to planned", client.get(f"/jobs/{jid}").json()["state"] == "planned")
    # The label is derived on read, never echoed. A hand-edited clip gets
    # measured against the job's range like any other.
    _short_edit = [{"start": 20.0, "end": 25.0, "title": "hand edited short",
                    "hook": "", "why": "", "score": 1.0, "out_of_range": None}]
    _r = client.put(f"/jobs/{jid}/plan", json={"clips": _short_edit, "snap": False})
    check("a hand-edited short clip comes back flagged, not echoed",
          _r.json()[0]["out_of_range"] == "short", str(_r.json()[0]))
    client.put(f"/jobs/{jid}/plan", json={"clips": edited, "snap": False})
    r = client.put(f"/jobs/{jid}/plan", json={"clips": edited, "snap": False})
    check("snap:false leaves boundaries alone", r.json()[0]["start"] == 20.0,
          str(r.json()[0]["start"]))
    check("backwards clip rejected",
          client.put(f"/jobs/{jid}/plan",
                     json={"clips": [{"start": 9, "end": 3, "title": "x"}]}
                     ).status_code == 422)
    check("empty plan rejected",
          client.put(f"/jobs/{jid}/plan", json={"clips": []}).status_code == 422)

    settle(client)  # let any orphaned background job from the checks above finish
    models_before = len(SEEN_MODELS)
    r = client.post(f"/jobs/{jid}/render")
    check("render accepted", r.status_code == 202, str(r.status_code))
    job = wait(client, jid, {"done", "failed"})
    check("re-render replaced outputs",
          job["state"] == "done" and len(job["outputs"]) == 1,
          f"{job['state']} {len(job['outputs'])} {job.get('error')}")
    # a delta, not an absolute — other sections legitimately start jobs of their
    # own, and an absolute count silently couples this check to their order
    check("no model call on re-render", len(SEEN_MODELS) == models_before,
          f"{models_before} -> {len(SEEN_MODELS)}")

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

    section("sqlite persistence")
    import sqlite3 as _sqlite3

    _dbp = SETTINGS.data_dir / "qatf.db"
    check("the store keeps its records in one database file", _dbp.exists(),
          str(_dbp))
    _con = _sqlite3.connect(_dbp)
    _rows = _con.execute("SELECT count(*) FROM jobs").fetchone()[0]
    check("jobs are rows, not files", _rows > 0, str(_rows))

    # The old version of this check only confirmed an index NAMED
    # ix_jobs_state existed in sqlite_master — true even if `list()` were
    # rewritten to fetch every row and filter in Python (the exact regression
    # ix_jobs_state exists to prevent), since nothing there asks whether any
    # query actually USES the index. Capture the real SQL `JobStore.list()`
    # runs via a trace callback — not a hand-copied string, the literal query
    # the store executes — then ask the query planner what it did with it.
    # That is the only way to prove the index is load-bearing rather than
    # merely present.
    from qatf.core import db as _db

    _store = app.state.store
    _store_con = _db.connect(_store.db_path)
    _captured: list[str] = []
    _store_con.set_trace_callback(lambda sql: _captured.append(sql))
    try:
        _store.list(state="planned")
    finally:
        _store_con.set_trace_callback(None)
    _state_query = next((s for s in _captured if "FROM jobs" in s and "WHERE" in s), None)
    _plan = ([tuple(r) for r in
              _store_con.execute("EXPLAIN QUERY PLAN " + _state_query).fetchall()]
             if _state_query else [])
    check("?state= has an index to use, rather than scanning every record",
          _state_query is not None
          and any("SEARCH jobs USING INDEX ix_jobs_state" in (r[-1] or "") for r in _plan),
          f"query={_state_query!r} plan={_plan}")

    # The failure this whole change exists to remove. A record truncated by a
    # crash used to be dropped SILENTLY by _recover's bare `continue`, so the
    # job vanished with no error anywhere.
    #
    # This check used to be `SELECT count(*) FROM jobs WHERE doc IS NULL`, but
    # `doc` is declared TEXT NOT NULL in the schema (qatf/core/db.py) — sqlite
    # rejects a NULL insert into that column outright, so the query could never
    # return non-zero. The check could not fail, which means it was measuring
    # nothing (the same reason this project asserts its render control FAILS:
    # see CLAUDE.md's working agreements). Prove the actual property instead:
    # open a real transaction, insert a row, blow up before it commits, and
    # confirm the row never landed — through a connection `qatf.core.db`'s
    # thread-local cache never touches, so there is no cached-handle shortcut
    # making a torn write look consistent.
    import uuid

    from qatf.core import db

    class _SimulatedCrash(Exception):
        pass

    _torn_id = "torn-" + uuid.uuid4().hex[:12]
    try:
        with db.transaction(_dbp) as _tcon:
            _tcon.execute(
                "INSERT INTO jobs (id, state, created_at, updated_at, doc) "
                "VALUES (?,?,?,?,?)",
                (_torn_id, "queued", "2026-01-01T00:00:00", "2026-01-01T00:00:00",
                 "{}"),
            )
            raise _SimulatedCrash("crash mid-write, before commit")
    except _SimulatedCrash:
        pass

    _fresh = _sqlite3.connect(_dbp)  # bypasses db.connect()'s per-thread cache
    _found = _fresh.execute(
        "SELECT count(*) FROM jobs WHERE id=?", (_torn_id,)).fetchone()[0]
    _fresh.close()
    check("no record is half-written — a transaction either lands or does not",
          _found == 0, f"found {_found} rows for the row that never committed")
    _con.close()

section("url sources — stage 0 and the caption path")
# `fetch.download` is faked at the same depth as ffmpeg and Whisper: the network
# is the only thing stubbed, so the URL boundary, the job state machine, the
# caption parse, the cache key split and the bounded snap tail all run for real.
from qatf.pipeline import fetch as _fetch  # noqa: E402


def _caption_doc(tokens: int = 260, step_ms: int = 500, word_level: bool = True) -> dict:
    """A json3 document shaped like YouTube's: 4 tokens per event, the first
    segment carrying no offset (which means zero, not missing)."""
    events: list[dict] = [{"tStartMs": 0, "dDurationMs": tokens * step_ms, "id": 1}]
    for start in range(0, tokens, 4):
        chunk = range(start, min(start + 4, tokens))
        segs = []
        for position, index in enumerate(chunk):
            seg: dict = {"utf8": f"word{index}"}
            if position and word_level:
                seg["tOffsetMs"] = position * step_ms
            segs.append(seg)
        events.append({"tStartMs": start * step_ms, "dDurationMs": 4 * step_ms,
                       "segs": segs})
    return {"events": events}


NEXT_CAPTIONS: list = [_caption_doc()]


def fake_download(url, dest, *, language=None, want_captions=True, on_progress=None):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    video = dest / "source.mp4"
    video.write_bytes(b"\0" * 1024)
    # Drive the progress callback the way yt-dlp would: a partial reading,
    # then the closing one. Faking the SIGNATURE without CALLING it would let
    # the wiring rot silently — this fake is the only place the stage 0
    # progress path runs without a network.
    if on_progress is not None:
        on_progress(_fetch.FetchProgress(downloaded_bytes=256,
                                         total_bytes=1024, file_index=1))
        on_progress(_fetch.FetchProgress(downloaded_bytes=1024,
                                         total_bytes=1024, file_index=1,
                                         final=True))
    captions = None
    if want_captions and NEXT_CAPTIONS[0] is not None:
        captions = dest / "source.ar-orig.json3"
        captions.write_text(json.dumps(NEXT_CAPTIONS[0]), encoding="utf-8")
    return _fetch.Fetched(video=video, captions=captions, title="a title",
                          duration=130.0, video_id="vid", language=language)


_fetch.download = fake_download
URL = "https://youtu.be/j5HVqFaa2Ts"

with TestClient(create_app(settings=SETTINGS)) as client:
    # -- the boundary, over HTTP -------------------------------------------
    for bad, why in [("file:///etc/passwd", "file scheme"),
                     ("https://evil.example/x", "host off the allowlist"),
                     ("https://youtube.com@evil.example/x", "userinfo")]:
        r = client.post("/jobs/url", json={"url": bad})
        check(f"POST /jobs/url refuses {why} with 403", r.status_code == 403,
              f"{r.status_code} {r.text[:90]}")
        check(f"the {why} refusal does not echo the url back",
              "evil" not in r.text and "passwd" not in r.text, r.text[:90])

    # -- the happy path: captions replace stage 2 --------------------------
    NEXT_CAPTIONS[0] = _caption_doc()
    r = client.post("/jobs/url", json={
        "url": URL, "language": "ar", "auto_render": False,
        "transcript_source": "captions"})
    check("POST /jobs/url accepts a youtube url", r.status_code == 202, r.text[:120])
    job_id = r.json()["id"]
    check("the job records its source and url", r.json()["source"] == "youtube"
          and r.json()["url"] == URL, json.dumps(r.json())[:140])

    job = wait(client, job_id, {"planned", "failed"})
    check("a url job reaches planned", job["state"] == "planned",
          f"{job['state']} {job.get('error')}")

    body = client.get(f"/jobs/{job_id}/transcript").json()
    check("the transcript came from the caption track",
          body["timing_source"] == "captions", json.dumps(body)[:140])
    check("and carries every token", body["word_count"] == 260,
          str(body["word_count"]))
    check("a caption job reports no transcription device",
          client.get(f"/jobs/{job_id}").json()["device"] is None)

    _fetched = client.get(f"/jobs/{job_id}").json()["fetch_progress"]
    check("a url job records stage 0 progress on the record",
          _fetched is not None, json.dumps(_fetched))
    # Both readings land inside one throttle window, so the CLOSING one has to
    # win. Otherwise a finished download is recorded forever as partway through
    # — a wrong number wearing an authoritative face.
    check("the closing reading beats the write throttle",
          _fetched["downloaded_bytes"] == 1024 and _fetched["total_bytes"] == 1024,
          json.dumps(_fetched))
    check("and it says which file of the fetch it was",
          _fetched["file_index"] == 1, json.dumps(_fetched))

    # The property the design turns on, end to end: with bounded ends the cut
    # closes exactly ON a word boundary rather than SNAP_TAIL past it.
    plan = client.get(f"/jobs/{job_id}/plan").json()
    starts = {round(w["start"], 6) for w in body["words"]}
    check("every clip ends exactly on a word onset — no tail past the bound",
          all(round(c["end"], 6) in starts for c in plan),
          str([c["end"] for c in plan]))

    # ...and the round trip re-snaps with the SAME tail, so it cannot drift.
    again = client.put(f"/jobs/{job_id}/plan",
                       json={"clips": plan, "snap": True}).json()
    check("re-snapping a caption plan is a fixed point",
          [(c["start"], c["end"]) for c in again] ==
          [(c["start"], c["end"]) for c in plan],
          str([(c["start"], c["end"]) for c in again][:2]))

    # -- transcript_source=whisper ignores the captions entirely -----------
    r = client.post("/jobs/url", json={
        "url": URL, "language": "ar", "auto_render": False,
        "transcript_source": "whisper"})
    whisper_id = r.json()["id"]
    job = wait(client, whisper_id, {"planned", "failed"})
    check("transcript_source=whisper reaches planned", job["state"] == "planned",
          f"{job['state']} {job.get('error')}")
    check("transcript_source=whisper never reads the caption track",
          client.get(f"/jobs/{whisper_id}/transcript").json()["timing_source"] == "asr")

    # -- a line-level track falls back to Whisper, loudly, without failing --
    NEXT_CAPTIONS[0] = _caption_doc(word_level=False)
    r = client.post("/jobs/url", json={
        "url": URL, "language": "ar", "auto_render": False,
        "transcript_source": "captions"})
    fallback_id = r.json()["id"]
    job = wait(client, fallback_id, {"planned", "failed"})
    check("a line-level caption track does not fail the job",
          job["state"] == "planned", f"{job['state']} {job.get('error')}")
    check("it falls back to Whisper rather than importing unusable timings",
          client.get(f"/jobs/{fallback_id}/transcript").json()["timing_source"] == "asr")

    # -- no captions at all -------------------------------------------------
    NEXT_CAPTIONS[0] = None
    r = client.post("/jobs/url", json={
        "url": URL, "language": "ar", "auto_render": False,
        "transcript_source": "auto"})
    none_id = r.json()["id"]
    job = wait(client, none_id, {"planned", "failed"})
    check("a video with no captions still completes via Whisper",
          job["state"] == "planned"
          and client.get(f"/jobs/{none_id}/transcript").json()["timing_source"] == "asr",
          f"{job['state']} {job.get('error')}")

    # -- the two cache key shapes cannot collide ---------------------------
    check("a caption transcript and a whisper transcript live in different rows",
          pipeline.asr.subs_cache_key("ar") != pipeline.cache_key("large-v3", "ar")
          and pipeline.asr.subs_cache_key("ar").startswith("subs-"),
          pipeline.asr.subs_cache_key("ar"))


section("settings overrides reach the next job, not a running one")
STORE = app.state.store
STORE.save_setting("llm_model", "saved/model-x")
check("a saved override wins over the injected settings",
      STORE.settings_for_job().llm_model == "saved/model-x",
      str(STORE.settings_for_job().llm_model))
STORE.clear_setting("llm_model")
check("clearing falls back to the injected/environment value",
      STORE.settings_for_job().llm_model == SETTINGS.llm_model,
      str(STORE.settings_for_job().llm_model))
# The table is a file someone can edit; the allowlist holds on read too.
STORE.save_setting("media_root", "/etc")
check("a non-editable key cannot be saved into effect",
      str(STORE.settings_for_job().media_root) == str(SETTINGS.media_root),
      str(STORE.settings_for_job().media_root))
STORE.clear_setting("media_root")
check("the injected settings still reach a job with no overrides saved",
      STORE.settings_for_job().llm_provider == SETTINGS.llm_provider,
      STORE.settings_for_job().llm_provider)


section("settings endpoints")
r = client.get("/settings")
check("GET /settings is 200", r.status_code == 200, str(r.status_code))
items = {i["key"]: i for i in r.json()["items"]}
check("every editable key is reported", set(items) == set(EDITABLE),
      str(sorted(items)))
check("no response body carries a credential",
      "sk-" not in r.text and "API_KEY" not in r.text, r.text[:200])
check("workers is flagged restart_required", items["workers"]["restart_required"])
check("llm_model is not", not items["llm_model"]["restart_required"])

r = client.put("/settings", json={"llm_model": "saved/model-y"})
check("PUT is 200", r.status_code == 200, r.text[:200])
after = {i["key"]: i for i in r.json()["items"]}
check("the saved value comes back", after["llm_model"]["value"] == "saved/model-y",
      str(after["llm_model"]))
check("and its source flips to saved", after["llm_model"]["source"] == "saved",
      after["llm_model"]["source"])
check("the override reaches what the next job would use",
      app.state.store.settings_for_job().llm_model == "saved/model-y")

r = client.delete("/settings/llm_model")
check("DELETE is 200", r.status_code == 200, str(r.status_code))
back = {i["key"]: i for i in r.json()["items"]}
check("source falls back off saved", back["llm_model"]["source"] != "saved",
      back["llm_model"]["source"])

r = client.put("/settings", json={"media_root": "/etc"})
check("a non-editable key is refused 422", r.status_code == 422, str(r.status_code))
check("the refusal names the allowed set", "llm_provider" in r.text, r.text[:200])
check("and does not echo the rejected key back",
      "media_root" not in r.text, r.text[:200])

r = client.put("/settings", json={"llm_base_url": "https://evil.example.com/v1"})
check("a public base_url is refused 403", r.status_code == 403, str(r.status_code))
check("the base_url refusal does not echo the url",
      "evil.example.com" not in r.text, r.text[:200])
r = client.put("/settings", json={"llm_base_url": "http://127.0.0.1:11434/v1"})
check("a private base_url is accepted", r.status_code == 200, r.text[:200])
client.delete("/settings/llm_base_url")
check("DELETE of a non-editable key is refused 422",
      client.delete("/settings/media_root").status_code == 422)


section("transcript suggestions are read-only")
from qatf.pipeline import enhance as _enh  # noqa: E402

# Its own client: the blocks above have exited their lifespans, so the worker
# pool is shut down and `POST /jobs` cannot schedule anything.
with TestClient(create_app(settings=SETTINGS)) as sclient:
    _sr = sclient.post("/jobs", json={"path": "talk.mp4", "clips": 2,
                                      "language": "ar"})
    _sjid = _sr.json()["id"]
    wait(sclient, _sjid, {"done", "failed"})
    _before = sclient.get(f"/jobs/{_sjid}/transcript").json()
    check("the suggestion fixture has a transcript", _before.get("word_count", 0) > 0,
          str(_before)[:140])

    def _fake_suggest(words, terms, settings=None):
        return ([_enh.Suggestion(index=1, was=words[1].text, text="",
                                 why="artefact")],
                ["index 9 proposes something that is not a listed term"])

    _real_suggest = _enh.suggest
    _enh.suggest = _fake_suggest
    try:
        r = sclient.post(f"/jobs/{_sjid}/transcript/suggest",
                         json={"terms": ["بايثون"]})
        check("suggest is 200", r.status_code == 200, r.text[:200])
        d = r.json()
        check("it returns the kept suggestion", len(d["suggestions"]) == 1,
              str(d)[:160])
        check("and reports what the server refused", d["dropped"] == 1,
              str(d.get("dropped")))
        check("and how many terms it matched against", d["terms_used"] >= 1,
              str(d.get("terms_used")))
        check("it names the model that produced them", bool(d["model"]),
              str(d.get("model")))
        # THE POINT: this endpoint writes nothing. Every write still goes
        # through PUT /transcript, so `edits.diff` stays the single place the
        # word-count and timing contract is enforced.
        check("the stored transcript is byte-identical afterwards",
              sclient.get(f"/jobs/{_sjid}/transcript").json() == _before)
    finally:
        _enh.suggest = _real_suggest

raise SystemExit(report())
