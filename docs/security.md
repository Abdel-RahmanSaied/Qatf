# Security

What this system trusts, where it stops trusting, and what it does not defend
against.

The short version: **the API has no authentication of its own.** Everything below
assumes something in front of it decides who may call it. Read
[Deploying it](#deploying-it) before exposing a port.

---

## Trust model

| Input | Source | Trusted? |
| --- | --- | --- |
| `POST /jobs` body | caller | no |
| uploaded video | caller | no — handed to ffmpeg, a large C parsing surface |
| `fixups` values, `PUT /transcript` text | caller | no — reaches the ASS file |
| the transcript | Whisper, over the caller's audio | no — the caller chose the audio |
| stage 3 output (`title`, `hook`, `why`) | a model, prompted with the transcript | no — see [prompt injection](#prompt-injection) |
| `QATF_*` environment | operator | yes |
| the transcript cache on disk | the process itself | yes — protect the data dir |

The load-bearing consequence: **caller text reaches two file formats** — the ASS
subtitle file and the transcript cache path. Both are boundaries, and both are
enforced in the pipeline rather than only at the HTTP layer, so the CLI gets the
same treatment.

---

## Boundaries and where they are enforced

| Boundary | Enforced in | What it stops |
| --- | --- | --- |
| media root | [`api/deps.py`](../qatf-backend/qatf/api/deps.py) `resolve_source` | `POST /jobs {"path": "../../etc/passwd"}` |
| download path | [`api/deps.py`](../qatf-backend/qatf/api/deps.py) `safe_output_path` | `GET /clips/../job.json` |
| transcript cache path | [`pipeline/asr.py`](../qatf-backend/qatf/pipeline/asr.py) `cache_path` | `language` escaping the work dir |
| Whisper model name | [`api/schemas.py`](../qatf-backend/qatf/api/schemas.py) + `asr.MODEL_SIZES` | the server fetching an arbitrary model repo |
| ASS structure | [`pipeline/captions.py`](../qatf-backend/qatf/pipeline/captions.py) `escape` / `safe_font` | caption text becoming subtitle directives |
| filtergraph quoting | [`pipeline/encode.py`](../qatf-backend/qatf/pipeline/encode.py) `filtergraph` | a path breaking out of `ass='...'` |
| cut timings | [`pipeline/edits.py`](../qatf-backend/qatf/pipeline/edits.py) `diff` | a text correction moving a cut |
| numeric ranges | [`api/schemas.py`](../qatf-backend/qatf/api/schemas.py) | `1e999` reaching ffmpeg as a duration |
| work volume | `JobOptions.clips` ≤ 50, `MAX_PLAN_CLIPS` = 100 | one request queuing thousands of encodes |

Both path boundaries **resolve first and check second**, so they catch symlink
escapes and not just `..`.

---

## The 2026-08 review

Findings from a full pass over the codebase. Everything below is fixed and
pinned by a check in the smoke suites.

### High

**Arbitrary file write through `language`.** The field is free text and lands in
the transcript cache *filename*:

```python
work / f"words-{model}-{language}.json"
```

`language="../../../../tmp/pwned"` resolved outside the work directory, and
`write_cache` calls `mkdir(parents=True)` before writing — so a `POST /jobs` body
could create directory trees and write a `.json` file anywhere the process could
write, with partly caller-controlled contents. Reaching another job's `job.json`
was two levels up.

Fixed in `cache_path` (slugified, which leaves `ar` untouched so no cache was
invalidated) **and** at the schema, which now requires a language tag.

**Arbitrary model load through `whisper`.** `WhisperModel` takes a *size or
path*: anything outside the published set is treated as a HuggingFace repo id or
a local directory. An unauthenticated caller could name
`evil-user/backdoored-ct2-model` and have the server fetch it and hand the
weights to CTranslate2 — a remote fetch plus untrusted native deserialisation,
chosen over HTTP. Now an allowlist (`asr.MODEL_SIZES`).

The CLI is deliberately **not** restricted: a local user pointing at their own
converted model crosses no boundary.

**ASS injection through caption text.** `escape()` handled `{`/`}` but not
newlines. ASS is line-oriented, so a newline in `Word.text` ends the `Dialogue:`
line and the remainder is parsed as a fresh directive — including a `[Fonts]`
section, which libass decodes and hands to the font engine. Reachable through
`fixups` values in a `POST /jobs` body and through `PUT /transcript`. `escape()`
now neutralises every line terminator and NUL.

**ASS injection through `font`.** The `Style:` line is comma-delimited, and
`font` went in verbatim: a comma shifted every field after it, a newline injected
a whole directive. New `safe_font()`.

### Medium

**Unhandled exception in the validation path.** A body containing `1e999` was
correctly rejected, but FastAPI's default 422 embeds the offending input — and
encoding an `inf` raises, turning a clean 422 into an unhandled 500. The new
handler reports location and reason and never the input, which also stops
reflecting caller content back.

It fixed a documentation lie too: `ErrorResponse` declares `detail` as a string
while the default 422 returned a list of objects, so a generated client broke on
the most common failure of all.

**Unbounded plan.** `PUT /plan` accepted any number of clips, bypassing the
`clips ≤ 50` cap on `JobOptions` entirely — one request could queue thousands of
ffmpeg encodes. Capped at `MAX_PLAN_CLIPS`.

**Non-finite timings.** `1e999` parses as `inf`, which satisfied `gt=0` and
`end > start`, persisted into the plan and reached ffmpeg as `-t inf`. `NaN` was
worse in `edits.diff`: every comparison against NaN is False, so a NaN retiming
passed the "timings unchanged" guard as *unchanged*. Both refused now —
`allow_inf_nan=False` at the schema, an explicit finiteness check in `diff`.

**Container ran as root.** Stage 1 and stage 5 hand caller-supplied media to
ffmpeg. A demuxer bug should not also be a root shell. Now `USER qatf` (uid
10001).

**Stage 3 ignored injected settings.** `select.pick_clips` called the
process-wide `get_settings()`, so `create_app(settings=...)` did not confine the
one component that opens a network connection and spends a credential. Settings
are now threaded through `plan_clips`, and the smoke suite asserts the injected
object arrives.

### Low

**Filtergraph quote breakout.** The `ass=` value is single-quoted and `'` was not
escaped, so `-o "Ahmed's clips/"` would end the quoting early and the rest of the
path would parse as filtergraph syntax. CLI-only.

Fixed twice. The first fix used the shell idiom `'\''`, which turned the breakout
into a **truncation**: libavfilter tokenizes filter arguments twice — the graph
parser, then `av_opt_set_from_string` on the filter's own options — and the
apostrophe that survives pass one is re-read as a quote by pass two and deleted.
`Ahmed's clips/` became `Ahmeds clips/`, libass could not open the file, and
every clip died at stage 5 with exit 234. `encode._escape_path` now emits
`'\\\''`, verified against ffmpeg 7.1.1 by rendering into such a directory with
captions and `--reframe track` both active — the two consumers of that escape.
Pinned by two checks in `smoke_pipeline.py`, one of which asserts the
single-level form is *not* emitted.

---

## What held up

Worth recording, because it is most of the surface:

- **No `shell=True` anywhere.** Every subprocess call passes an argv list, so
  there is no command injection through filenames or filter arguments.
- **Uploads stream with the size check inside the loop**, and a failed upload
  deletes its own job record — no half-written source is left behind.
- **`slugify` is ASCII-only** and `clip_stem` prefixes an index, so a
  model-chosen title can never produce traversal or a Windows reserved device
  name in an output filename. The known cosmetic limitation turns out to be a
  security property.
- **No pickle, yaml or eval.** `json.loads` only.
- **Domain errors carry their own status code**, so there is one mapping layer
  rather than per-route guesses about what a failure means.

---

## Editable server settings

`PUT /settings` can change the stage-3 provider, model and base URL at runtime.
Three deliberate limits, and one accepted gap.

**API keys are never stored, returned, or settable.** Presets reference a
credential by NAME (`key_env="OPENROUTER_API_KEY"`) and read the value from the
environment. Nothing puts one in `qatf.db` or in a response. `/healthz` reports
`llm_ready` and `llm_error` without exposing the key, and that stays the only
signal a caller gets.

**`media_root` and `data_dir` are not in the allowlist.** `media_root` is the
sandbox for `POST /jobs`; an endpoint that could widen it is an endpoint that
could switch the sandbox off. The allowlist is enforced on write and again on
read, so a hand-edited row naming one of them is inert rather than effective.

**`llm_base_url` is allowlisted.** It decides who receives the transcript AND
the `Authorization: Bearer` header, on an API with no authentication in front of
it — freely editable it is a one-request credential-exfiltration path.
`llm.validate_base_url` accepts a preset's own host, or a host resolving
**entirely** to loopback/private/link-local, or an unresolvable
**single-label** name (`ollama`, `vllm`) — a service that is not running yet.
A single label cannot be a public DNS name; an unresolvable dotted name is
refused because it could start resolving anywhere. Every resolved address must be
private: a name publishing one private and one public record is refused, not
accepted on the first hit. Exact host match, never `.endswith`. Userinfo is
refused, because `https://openrouter.ai@evil/x` resolves to `evil`.

### Accepted gap: DNS rebinding on base_url

The private-address check resolves DNS, so it is **time-of-check/time-of-use**.
A name that resolves privately when you save it can resolve publicly by the time
a job runs.

This is accepted rather than solved. Closing it needs the resolved IP pinned
into the outbound connection, which the `openai` SDK does not expose. What
bounds it is the credential: a private-range `base_url` means a local model,
which needs no API key, so a successful rebind exposes the transcript rather
than the key. Do not read the check as airtight — read it as removing the
trivial "point it at my server" case.

## Known gaps

Not defects — decisions, or things that belong at a different layer. Know them
before exposing this.

**No authentication, authorisation, rate limiting or quota.** Any caller can read
and modify any job. There is no per-user separation at all.

**No request body size limit.** Starlette has none by default, and neither front
end adds one — the frontend's nginx sets `client_max_body_size 0` deliberately.
Field-level caps bound what is *stored* (`MAX_PLAN_CLIPS`, 200 000 words), but
they do not bound what is *received*: a large JSON body is buffered, and
`QATF_MAX_UPLOAD_MB` is compared only after `UploadFile` has already spooled the
whole multipart body to a temp file. An unbounded upload can therefore exhaust
temp space before any check of ours runs. Set a real limit at whatever fronts
this before exposing it.

**`job.error` leaks server paths.** `CommandFailed` embeds the full ffmpeg
command line and a stderr tail, and the worker records `str(exc)` on the job,
served by `GET /jobs/{id}`. Useful for debugging, and an information disclosure
if the API is exposed. Trim it or gate it behind a debug flag before you do.

**ffmpeg parses untrusted media.** It is the largest attack surface here by far
and it is not sandboxed beyond running as a non-root user. On a multi-tenant
deployment, run the workers in a separate container with a seccomp profile and no
network.

**The base image floats.** `python:3.12-slim` is unpinned, and a scanner flags
known CVEs in it. Pin a digest and rebuild on a schedule.

**`docker compose up` publishes `8000:8000`** — with `QATF_HOST=0.0.0.0`, that is
an unauthenticated API on every interface. Bind to `127.0.0.1:8000:8000` unless
something is fronting it.

**The frontend's nginx proxy is not a new trust boundary.** It forwards
`/api/*` to the same unauthenticated `qatf` service on the same host with the
prefix stripped, so reaching the API through `:3000` instead of `:8000` changes
nothing about who can call it — the gaps above apply identically either way.
`client_max_body_size 0` and `proxy_request_buffering off` are deliberate:
uploads are multi-GB, and the size check stays in FastAPI
(`QATF_MAX_UPLOAD_MB`) rather than being duplicated in nginx, where it would
just be a second, driftable number.

**But that check does not bound what reaches the disk.** `create_from_upload`
declares `file: UploadFile`, so Starlette resolves the entire multipart body —
spooling past 1 MB into a temp file — *before* the handler runs. The
`QATF_MAX_UPLOAD_MB` comparison then happens while copying that already-landed
file into the job directory. The endpoint's own docstring calling it "refused
mid-stream" is wrong, and with no byte cap at the ingress an unbounded body can
fill the container's temp space before any code of ours looks at it. This is
**not new** — uvicorn on `:8000` had no limit either and the route was equally
reachable — so it belongs with the known gaps below rather than being a cost of
the proxy. Anything internet-facing needs a real limit in whatever fronts it.

**`.env` discovery walks upward** to the filesystem root. On a shared host, a
writable parent directory means arbitrary environment variables. Standard for
dotenv loaders; worth knowing where you start the process.

### Prompt injection

The transcript is embedded in the stage 3 prompt without fencing, and the caller
chose the audio — so they influence the prompt, and with `PUT /transcript` they
control it outright.

The blast radius is small: stage 3 has no tools and its output only chooses
passages the caller already supplied. But `title`, `hook` and `why` are model
output returned verbatim in API responses, so **a client that renders them as
HTML has an XSS problem**. Escape on display.

---

## Deploying it

In rough order of how much each buys you:

1. Put authentication in front of it. There is none.
2. Set `QATF_MEDIA_ROOT` to a tight directory. It defaults to `.`.
3. Bind to localhost and terminate TLS at a proxy; set a body size limit there.
4. Keep `QATF_WORKERS=1` on a single GPU, and cap concurrency at the proxy.
5. Run the container as the non-root user it now ships with, read-only where you
   can, `/media` mounted `:ro`.
6. Isolate the ffmpeg work if the media is untrusted.
7. Rotate provider keys; they are process environment, never per-request.

---

## Reporting

This is a prototype with no release process, so there is no embargo to respect
and no supported older version to backport to. **Report privately** — a GitHub
security advisory on the repo, or the address in
[`SECURITY.md`](../SECURITY.md), which also lists the gaps already known here so
you can tell quickly whether you have found a new one.
