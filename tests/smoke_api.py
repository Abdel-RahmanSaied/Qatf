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
from qatf.core.config import Settings
from qatf.core.types import Clip, Keyframe, Track, Transcript, Word
from qatf.jobs import JobStore, worker
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
    check("the INJECTED settings reached stage 3, not the process-wide ones",
          SEEN_SETTINGS[0] is SETTINGS, repr(SEEN_SETTINGS[0]))
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
    check("overlay stored beside the cache, not inside it",
          (SETTINGS.data_dir / jid / ".work" / "word-edits.json").exists()
          and "CORRECTED" not in next(
              (SETTINGS.data_dir / jid / ".work").glob("words-*.json")
          ).read_text(encoding="utf-8"))

    r = client.put(f"/jobs/{jid}/transcript", json={"words": pristine})
    check("re-submitting the untouched transcript clears corrections",
          r.json()["edits_applied"] == 0 and r.json()["words"][30]["text"] == "word30")
    check("cleared overlay is removed, not left empty",
          not (SETTINGS.data_dir / jid / ".work" / "word-edits.json").exists())

    from qatf.core.types import Word as _W
    from qatf.jobs import worker as _worker

    class _T:
        words = [_W("a", 0.0, 0.1)] + [_W("dup", 0.1 + i / 10, 0.2 + i / 10)
                                       for i in range(5)]
    _base = _worker.baseline_words(_T(), {})
    check("repair reaches the read path, so captions and GET /transcript agree",
          [w.text for w in _base] == ["a", "dup", "", "", "", ""],
          str([w.text for w in _base]))
    check("and the word count the PUT contract depends on is unchanged",
          len(_base) == 6)

    section("output sizes come from the record, not the filesystem")
    # to_response used to stat() every output on every read, which measured 75%
    # of GET /jobs and scaled with the job count on the endpoint clients poll.
    rec = json.loads((SETTINGS.data_dir / jid / "job.json").read_text(encoding="utf-8"))
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
    legacy = store_for_legacy = None
    legacy_id = client.post("/jobs", json={"path": "talk.mp4", "auto_render": False,
                                           "device": "cpu"}).json()["id"]
    wait(client, legacy_id, {"planned", "failed", "done"})
    lp = SETTINGS.data_dir / legacy_id / "job.json"
    rec2 = json.loads(lp.read_text(encoding="utf-8"))
    (SETTINGS.data_dir / legacy_id / "clips").mkdir(parents=True, exist_ok=True)
    (SETTINGS.data_dir / legacy_id / "clips" / "99-legacy.mp4").write_bytes(b"x" * 4096)
    rec2["outputs"] = ["99-legacy.mp4"]
    rec2.pop("output_sizes", None)
    lp.write_text(json.dumps(rec2), encoding="utf-8")
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
          "track" in str(_opts["reframe"]), str(_opts["reframe"]))
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
    r = client.put(f"/jobs/{jid}/plan", json={"clips": edited, "snap": False})
    check("snap:false leaves boundaries alone", r.json()[0]["start"] == 20.0,
          str(r.json()[0]["start"]))
    check("backwards clip rejected",
          client.put(f"/jobs/{jid}/plan",
                     json={"clips": [{"start": 9, "end": 3, "title": "x"}]}
                     ).status_code == 422)
    check("empty plan rejected",
          client.put(f"/jobs/{jid}/plan", json={"clips": []}).status_code == 422)

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

raise SystemExit(report())
