# Web UI + backend/frontend restructure — design

Date: 2026-08-18
Status: approved (chat), pending spec review

## Goal

Give qatf a web UI covering the full job workflow, and restructure the repo into
`qatf-backend/` and `qatf-frontend/`, each with its own Dockerfile, orchestrated
by one `docker-compose.yaml` so `docker compose up` starts both.

## Decisions (made with the user)

| Decision | Choice |
| --- | --- |
| Frontend stack | React + Vite + TypeScript, static build served by nginx |
| UI scope | Full workflow **plus** every `JobOptions` knob as a settings form |
| API wiring | nginx in the frontend container proxies `/api/*` → backend; no CORS changes |
| Naming | lowercase `qatf-backend/` / `qatf-frontend/` |
| Frontend host port | 3000 (backend keeps publishing 8000) |

## 1. Repository restructure

All moves via `git mv` so history follows the files.

```text
e:\Qutf\
  qatf-backend/
    Dockerfile          moved; COPY lines updated for the new build context
    pyproject.toml      moved; readme repointed (see below)
    README.md           NEW — short, points to the root README and docs/
    requirements.txt    moved
    qatf.py             moved (legacy shim)
    qatf/               moved
    tests/              moved
    prompts/            moved (vocab/fixup files are backend inputs)
  qatf-frontend/        NEW — see section 3
  docker-compose.yaml   renamed from docker-compose.yml; gains frontend service
  CLAUDE.md             stays at root; paths and commands updated
  README.md             stays at root; quickstart updated
  docs/                 stays at root; path references updated
  .gitignore            stays at root; gains frontend entries
  media/                stays at root (compose mount, gitignored)
  qatf-data/            stays at root (compose mount, gitignored)
```

Untracked/gitignored artifacts at the root (`out-*`, `run-*`, `sweep-*.json`,
`tools/`, `api-run`, `cmp-crop`, `output-samples`) are not touched.

### Backend changes — file moves plus exactly three edits

1. `Dockerfile` COPY lines: the build context becomes `qatf-backend/`, so
   `COPY pyproject.toml README.md ./` and `COPY qatf ./qatf` resolve inside it.
   The image no longer copies `CLAUDE.md` (it stays at the repo root).
2. `pyproject.toml`: `readme = "CLAUDE.md"` → `readme = "README.md"`
   (the new backend-local README; hatchling requires the file to exist in the
   package root).
3. Nothing else. No code changes, no CORS middleware, no route changes.
   The frontend is purely additive to the backend, the same philosophy as
   "a provider swap cannot affect cut accuracy."

The smoke suites, `ruff check .`, and the CLI all run from `qatf-backend/`.

## 2. docker-compose.yaml

- **`qatf`** (backend): same service as today, `build: ./qatf-backend`, still
  publishes `8000:8000` so curl, `/docs`, and `tests/api_full_flow.py` keep
  working unchanged. Env vars and volume mounts (`./qatf-data:/data`,
  `./media:/media:ro`) unchanged.
- **`frontend`**: `build: ./qatf-frontend`, publishes `3000:80`,
  `depends_on: [qatf]`. No volumes, no env — a static site plus a proxy.
- **`ollama` / `vllm` profiles**: untouched.

`docker compose up` builds and starts backend + frontend. Profiles behave as
before.

## 3. Frontend

### Image

Multi-stage Dockerfile:

1. `node:22-alpine`: `npm ci && npm run build` (`tsc -b && vite build` — the
   strict type check is the build gate).
2. `nginx:alpine`: copy `dist/` to the web root and `nginx.conf` into place.

### nginx.conf — the load-bearing part

```nginx
location /api/ {
    proxy_pass http://qatf:8000/;   # trailing slash strips the /api prefix
    client_max_body_size 0;         # uploads are multi-GB; the backend owns the limit
    proxy_request_buffering off;    # stream the upload, don't spool it to disk
    proxy_read_timeout 300s;        # clip downloads and slow endpoints
}
location / {
    try_files $uri /index.html;     # SPA fallback
}
```

The backend never learns a proxy exists; the browser sees one origin.

### App structure

```text
qatf-frontend/src/
  api/
    types.ts        hand-written TS mirror of schemas.py (JobOptions, JobResponse,
                    ClipModel, WordModel, TranscriptResponse, Health, ErrorResponse)
    client.ts       typed fetch wrapper; all failures normalised to ErrorResponse
    poll.ts         usePolling hook: interval polling that stops on terminal states
  pages/
    JobsList.tsx    dashboard
    NewJob.tsx      submit + options form
    JobDetail.tsx   status, transcript editor, plan editor, render, clips
  components/
    StateBadge, OptionsForm, PlanEditor, TranscriptEditor, ClipGrid,
    HealthBanner, ErrorToast
```

Routing: react-router. State: React hooks only — no Redux/query library; the
polling hook is the only shared state machinery this needs (YAGNI).

### Pages

**Jobs dashboard** (`/`)

- `GET /api/jobs` polled every 3s; optional `?state=` filter.
- Columns: id, StateBadge (full lifecycle `queued → fetching → extracting →
  transcribing → selecting → planned → rendering → done`, plus `failed` /
  `cancelled`), message, created_at, actions (cancel, delete — delete disabled
  while running, matching the API's refusal).
- HealthBanner from `GET /api/healthz`: shows `status: degraded`, `ffmpeg`,
  `llm_provider` + `llm_ready`, and **`transcribe_device`** — the "know before
  you submit an hour of audio" signal. Shown on the dashboard AND on the
  New-job page, since that is where the hour of audio gets submitted.

**New job** (`/new`)

- Three source tabs → three endpoints:
  - Upload → `POST /api/jobs/upload` (multipart; `options` is a JSON string
    form field). Sent via `XMLHttpRequest` for real upload-progress events.
  - YouTube URL → `POST /api/jobs/url` (403 off the allowlist is surfaced
    verbatim from `ErrorResponse.detail`).
  - Server path → `POST /api/jobs` (path relative to `media_root`, which the
    form shows, read from `/healthz`).
- OptionsForm covers every `JobOptions` field, grouped by stage exactly as the
  schema docstring groups them:
  - selection: `clips`, `min_len`, `max_len`
  - transcription: `whisper` (allowlist dropdown), `device`, `language`,
    `denoise`, `hotwords`, `initial_prompt`, `fixups` (key→value editor),
    `transcript_source`
  - captions: `font`, `captions`, `per_line`
  - encode: `reframe`, `track_tier` (only enabled when reframe=track),
    `codec`, `preset`, `resolution`, `ten_bit`, `crf`
  - workflow: `auto_render` — **defaults OFF in the UI** (the API default is
    on) so the review workflow is the natural path in a UI whose whole point
    is reviewing.
- Client-side constraint mirrors (min_len ≤ max_len, language tag pattern,
  ranges) for instant feedback; the server remains the authority.
- Field help text is lifted from the schema descriptions (e.g. the max_len=52
  Shorts warning) so the measured guidance travels with the form.

**Job detail** (`/jobs/:id`)

- `GET /api/jobs/{id}` polled every 2s while state is non-terminal; polling
  stops on `done` / `failed` / `cancelled` (and slows to 10s on `planned`).
- Header: StateBadge, message, error, source, `device` actually used,
  `transcript_cached`, timestamps.
- **Transcript editor** (visible once `word_count > 0`):
  - `GET /api/jobs/{id}/transcript`; words rendered as inline editable text
    chips with timings shown read-only. RTL text direction applied when the
    language is RTL.
  - Save sends the complete word list to `PUT /api/jobs/{id}/transcript`
    with original `start`/`end` echoed untouched — the UI has no timing
    inputs at all, mirroring the contract instead of merely surviving it.
  - Client-side guard mirrors the server: word count must match; only `text`
    may differ. Shows `edits_applied` / `edits_stale` after save, with the
    stale explanation from the schema.
- **Plan editor** (visible from `planned`; also editable on `done` for
  re-render):
  - Clip rows: start, end, title, hook/why/score read-only, per-clip duration
    shown live with a warning when it exceeds max_len + snap slack.
  - Reorder / delete / add row. Save → `PUT /api/jobs/{id}/plan` with
    `snap: true` always (no UI toggle to disable it — a typed second is a
    semantic guess; the core invariant is enforced by not offering the
    footgun). After save the response's snapped values replace the form
    values, so the user sees where cuts actually landed.
- **Render**: `POST /api/jobs/{id}/render`; 409 (already rendering) surfaced
  as a toast. Note shown that re-render replaces previous outputs.
- **Clips grid**: from `outputs` (grows live during `rendering`); each card is
  a `<video>` element streaming `GET /api/jobs/{id}/clips/{name}` (prefix the
  API-relative `url` field with `/api`), size, and a download link.

### Error handling

- Every non-2xx is parsed as `ErrorResponse` and shown as a toast with
  `detail`. 422s render location/reason lists.
- Network failures during polling show a "connection lost, retrying" banner
  rather than an error toast per tick; polling continues.

## 4. .gitignore additions

```gitignore
node_modules/
qatf-frontend/dist/
```

Existing rules are path-relative-safe already (`qatf-data/`, `media/` match at
any level; both stay at root anyway).

## 5. Documentation updates

- `CLAUDE.md`: layout section gets the two-package shape; every command gains
  its `qatf-backend/` working directory; a short frontend section (stack, the
  nginx proxy contract, "the frontend is additive — no backend change may be
  required by a UI feature").
- `README.md`: quickstart becomes `docker compose up` → `http://localhost:3000`;
  CLI/API quickstarts updated for the new paths.
- `docs/operations.md` and `docs/api.md`: path references and the compose
  story updated. No measured numbers move (rule: they live in quality.md and
  CLAUDE.md only).

## 6. Testing & verification

- Backend: `smoke_{db,pipeline,llm,api}.py`, `load_api.py`, `ruff check .` all
  green from `qatf-backend/` — proves the moves broke nothing.
- Frontend: `tsc -b` strict as the build gate; vitest for `api/client.ts`
  error normalisation and the transcript-edit guard (word count / timing
  immutability) and the plan duration warning logic.
- Docker: `docker compose build` for both images; `docker compose up` then:
  - `GET localhost:3000` serves the app,
  - `GET localhost:3000/api/healthz` round-trips through nginx to the backend,
  - `GET localhost:8000/healthz` still works directly.
- Manual: create a job through the UI against a small fixture and walk
  submit → planned → edit plan → render → preview.

## Out of scope (deliberate)

- Auth (the API has none; the UI adds none — same trust model, documented).
- Jobs surviving a restart, websockets/SSE (polling matches the API design).
- Provider picker UI beyond displaying the `/healthz` roster.
- i18n of the UI chrome (RTL is handled for *transcript content* only).
- A Vite dev-server compose profile — `npm run dev` with a `vite.config.ts`
  proxy to `localhost:8000` is documented instead.
