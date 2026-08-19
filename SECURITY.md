# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem. Use one of:

- **GitHub Security Advisories** (preferred): this repo's **Security** tab →
  **Report a vulnerability**. It's private by default and keeps the
  conversation and any fix in one place.
- **Email**: abdelrahmansaied080@gmail.com

Include what you'd include in any good bug report: the symptom, the request
or command that produced it, and — for anything path- or file-related — the
exact input. If it's exploitable end to end, a proof of concept helps; if not,
say what you were unable to confirm.

### What to expect

qatf is a one-person prototype with no release process, so this is
not an enterprise SLA: expect an acknowledgement within a few days, not
hours, and no bounty. I'll tell you if a report turns out to be one of the
known gaps below rather than something new, and I'll credit you in the fix
commit or advisory unless you'd rather stay anonymous.

## Supported versions

There are no tagged releases yet. Only the current `main` branch is
supported — if you're running an older checkout, please reproduce against
`main` before reporting.

## What is already known (not a new report)

`docs/security.md` is the full trust model and gets updated as things
change; the summary below is current as of this file but that page is the
authority if the two ever disagree. These are documented decisions or
tracked gaps, not surprises:

- **No authentication, authorisation, rate limiting, or per-user quota on the
  API.** Any caller who can reach the API can read and modify any job. This
  is by design — see below.
- **No request body size limit at ingress.** Starlette buffers the whole
  request before any of qatf's own code runs. `QATF_MAX_UPLOAD_MB` is only
  checked *after* `UploadFile` has already spooled the multipart body to a
  temp file, so an oversized upload can exhaust temp space before that check
  ever fires. A real limit belongs in whatever fronts the API (nginx,
  a load balancer), not in qatf itself.
- **`job.error` can leak server paths.** A failed ffmpeg or Whisper call
  records the full command line and a stderr tail on the job record, and
  that's served back by `GET /jobs/{id}`. Useful for debugging, an
  information disclosure if the API is exposed without a debug gate.
- **`.env` discovery walks upward to the filesystem root.** Standard dotenv
  behaviour, but on a shared host a writable parent directory means
  arbitrary environment variables get picked up. Know where you start the
  process.
- Related smaller items tracked in `docs/security.md`'s "Known gaps"
  section: the container's base image floats (unpinned `python:3.12-slim`),
  `docker compose up` publishes the API on all interfaces by default, and
  ffmpeg (a large C parsing surface handling caller-supplied media) isn't
  sandboxed beyond running as a non-root user.

None of the above needs re-reporting. If you've found a way to actually
exploit one of them beyond what's described — for example, a body-size gap
that crashes the process rather than just filling disk — that's worth a
report.

## What is in scope and interesting

Anything that breaks one of the boundaries `docs/security.md` says are
enforced, or that lets caller input do something it isn't supposed to:

- **Escaping `QATF_MEDIA_ROOT`** — reading, writing, or naming a file
  outside the sandbox `POST /jobs`, uploads, or clip downloads are meant to
  be confined to.
- **Getting past the URL allowlist** in `pipeline/fetch.py` — anything that
  reaches a host outside the exact-match allowlist, or that turns a URL into
  a local file read (`file://`, redirects, userinfo tricks, etc.) for
  `POST /jobs/url`.
- **ASS or filtergraph injection through caller-controlled text** — caption
  text, `fixups` values, `PUT /jobs/{id}/transcript` corrections, or a font
  name doing anything other than becoming literal captioned text or a
  literal font name. This project has already found and fixed several bugs
  in this family (newline injection, comma-delimited `Style:` line
  breakout, filtergraph quote breakout) — see `docs/security.md` for the
  specifics; a *new* way in is very much in scope.
- **Anything that makes the server execute or load something the caller
  chose** — an arbitrary model path/repo through `whisper`, an arbitrary
  cache filename through `language`, or any other field that ends up
  choosing a path, a command, or a module rather than a value.
- **Anything that turns a 4xx into an unhandled 500**, especially where the
  response ends up echoing caller input back (the 422 handler is
  deliberately built not to do this — a case where it still does is a
  bug).

## No auth by design

The API ships with **no authentication of its own**, on purpose — it's
meant to run behind something that provides it (a reverse proxy, an
internal network boundary, whatever fits your deployment). "The API has no
auth" is not itself a vulnerability report; `README.md` and
`docs/security.md#deploying-it` say this plainly and it's the first thing
either page tells you to fix before exposing a port. A report that shows a
way *around* an auth layer someone put in front of qatf, or a way that
qatf's own code assumes a trust it doesn't have, is in scope. A report that
says "there is no login page" is not.
