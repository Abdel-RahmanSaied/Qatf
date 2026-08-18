# Web UI + Backend/Frontend Restructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the repo into `qatf-backend/` and `qatf-frontend/`, add a React web UI covering the full job workflow, and orchestrate both with one `docker compose up`.

**Architecture:** The Python backend moves wholesale into `qatf-backend/` with zero behavioural change (only the Dockerfile COPY lines and pyproject `readme` pointer change). A new React + Vite + TypeScript SPA lives in `qatf-frontend/`, built to static files and served by nginx, which proxies `/api/*` to the backend service — one origin, no CORS. `docker-compose.yaml` at the root starts both.

**Tech Stack:** Backend unchanged (Python 3.12, FastAPI, ffmpeg). Frontend: React 19, react-router-dom 7, Vite 6, TypeScript strict, vitest; nginx:alpine runtime image, node:22-alpine build stage.

**Spec:** `docs/superpowers/specs/2026-08-18-web-ui-restructure-design.md` — read it first.

## Global Constraints

- Directory names are lowercase: `qatf-backend/`, `qatf-frontend/`.
- Every move uses `git mv` so history follows the files.
- **No backend behaviour change.** No CORS middleware, no route changes, no new backend dependencies. The only backend edits are the Dockerfile COPY lines and `pyproject.toml`'s `readme` pointer.
- Frontend runtime dependencies are exactly: `react`, `react-dom`, `react-router-dom`. Dev-only: `vite`, `@vitejs/plugin-react`, `typescript`, `vitest`, `@types/react`, `@types/react-dom`. Nothing else without a stated reason.
- Frontend publishes host port **3000**; backend keeps publishing **8000**.
- nginx proxies `/api/` → `http://qatf:8000/` with the prefix **stripped** (trailing slash on `proxy_pass`), `client_max_body_size 0`, `proxy_request_buffering off`.
- The UI always sends `snap: true` on `PUT /plan` (no toggle), never offers timing inputs on the transcript, and defaults `auto_render` to **false** (the API default is true).
- All work happens on the existing `web-ui` branch. Never commit the user's pre-existing `.gitignore` working-tree edit unless a task explicitly modifies `.gitignore` (Task 3 does; fold whatever is there in at that point).
- Windows host: the Bash tool (Git Bash) runs `git mv`, `npm`, `docker compose`. If the Docker daemon is not running/installed, do NOT silently skip Docker steps — run everything else and report the Docker steps as blocked in the task summary.
- Backend verification commands run **from `qatf-backend/`**: `python tests/smoke_pipeline.py`, `smoke_db.py`, `smoke_llm.py`, `smoke_api.py`, and `ruff check .`.

---

### Task 1: Move the backend into qatf-backend/

**Files:**
- Move (git mv): `qatf/` → `qatf-backend/qatf/`, `tests/` → `qatf-backend/tests/`, `prompts/` → `qatf-backend/prompts/`, `Dockerfile` → `qatf-backend/Dockerfile`, `pyproject.toml` → `qatf-backend/pyproject.toml`, `requirements.txt` → `qatf-backend/requirements.txt`, `qatf.py` → `qatf-backend/qatf.py`
- Create: `qatf-backend/README.md`
- Modify: `qatf-backend/pyproject.toml` (readme pointer), `qatf-backend/Dockerfile` (COPY lines)

**Interfaces:**
- Consumes: nothing.
- Produces: the `qatf-backend/` package root every later task builds against. Docker build context for the backend becomes `./qatf-backend`.

**Background you need:** `tests/_harness.py` resolves the import root as `Path(__file__).resolve().parent.parent` — moving `tests/` *together with* `qatf/` keeps every suite working with no edits. `pyproject.toml` currently has `readme = "CLAUDE.md"`; CLAUDE.md stays at the repo root, so the pointer must change or `pip install -e .` breaks inside the new context. Runtime code never reads CLAUDE.md/README.md (verified by grep — only comments mention them).

- [ ] **Step 1: Verify the suites are green BEFORE moving anything** (baseline — if something is already red, stop and report rather than blaming the move)

Run from repo root:
```bash
python tests/smoke_pipeline.py && python tests/smoke_db.py && python tests/smoke_llm.py && python tests/smoke_api.py && ruff check .
```
Expected: all PASS lines, exit 0 each.

- [ ] **Step 2: Move everything with git mv**

```bash
mkdir qatf-backend
git mv qatf qatf-backend/qatf
git mv tests qatf-backend/tests
git mv prompts qatf-backend/prompts
git mv Dockerfile qatf-backend/Dockerfile
git mv pyproject.toml qatf-backend/pyproject.toml
git mv requirements.txt qatf-backend/requirements.txt
git mv qatf.py qatf-backend/qatf.py
```

- [ ] **Step 3: Create qatf-backend/README.md**

````markdown
# qatf — backend

The Python pipeline, CLI and FastAPI job server. This directory is the build
context for the backend Docker image.

- Project overview, install and quickstart: [`../README.md`](../README.md)
- Human-facing reference: [`../docs/`](../docs/)
- Working agreement for agents: [`../CLAUDE.md`](../CLAUDE.md)

```bash
pip install -e ".[all]"        # run from THIS directory; ffmpeg must be on PATH
python tests/smoke_pipeline.py # the suites run from here too
```
````

- [ ] **Step 4: Repoint the readme in qatf-backend/pyproject.toml**

Change line `readme = "CLAUDE.md"` to:
```toml
readme = "README.md"
```

- [ ] **Step 5: Fix the Dockerfile COPY line**

In `qatf-backend/Dockerfile`, change
```dockerfile
COPY pyproject.toml README.md* CLAUDE.md ./
```
to
```dockerfile
COPY pyproject.toml README.md ./
```
(`README.md` now definitely exists in the context, and CLAUDE.md is no longer in it. The next line `COPY qatf ./qatf` is already correct relative to the new context.)

- [ ] **Step 6: Re-run every suite from the new location**

```bash
cd qatf-backend
python tests/smoke_pipeline.py && python tests/smoke_db.py && python tests/smoke_llm.py && python tests/smoke_api.py && ruff check .
```
Expected: identical PASS counts to Step 1, exit 0. If the editable install is stale (`pip show qatf` points at the old path), re-run `pip install -e ".[all]"` from `qatf-backend/` first — that is an environment fix, not a code fix.

- [ ] **Step 7: Commit**

```bash
git add -A qatf-backend
git commit -m "refactor: move the Python backend into qatf-backend/"
```
Check `git status` afterwards: the root `.gitignore` modification must still be unstaged and uncommitted.

---

### Task 2: docker-compose.yaml — rename and repoint the backend context

**Files:**
- Move (git mv): `docker-compose.yml` → `docker-compose.yaml`
- Modify: `docker-compose.yaml` (the `qatf` service's `build:`)

**Interfaces:**
- Consumes: `qatf-backend/Dockerfile` from Task 1.
- Produces: the compose file Task 10 adds the `frontend` service to. Service name stays `qatf` — nginx's upstream name in Task 10 depends on it.

- [ ] **Step 1: Rename and repoint**

```bash
git mv docker-compose.yml docker-compose.yaml
```
Then in `docker-compose.yaml`, change the `qatf` service line `build: .` to:
```yaml
    build: ./qatf-backend
```

- [ ] **Step 2: Validate the compose file**

```bash
docker compose config -q
```
Expected: exit 0, no output. (If the Docker CLI is unavailable, report it and continue — YAML syntax is still checked in Task 10's build.)

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yaml
git commit -m "build: rename compose file to .yaml, point backend build at qatf-backend/"
```

---

### Task 3: Frontend scaffold — Vite + React + TypeScript, building green

**Files:**
- Create: `qatf-frontend/package.json`, `qatf-frontend/tsconfig.json`, `qatf-frontend/vite.config.ts`, `qatf-frontend/index.html`, `qatf-frontend/src/main.tsx`, `qatf-frontend/src/App.tsx`, `qatf-frontend/src/styles.css`, `qatf-frontend/.dockerignore`
- Modify: `.gitignore` (root)

**Interfaces:**
- Consumes: nothing.
- Produces: `npm run build` (`tsc --noEmit && vite build`) and `npm test` (vitest) as the gates every later frontend task runs. `App.tsx` owns the router; Tasks 5-7 replace its placeholder route elements with real pages. The dev proxy maps `/api/*` to `http://localhost:8000/*` with the prefix stripped, so `npm run dev` matches the production nginx contract exactly.

- [ ] **Step 1: Write the config files**

`qatf-frontend/package.json`:

```json
{
  "name": "qatf-frontend",
  "private": true,
  "version": "0.1.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc --noEmit && vite build",
    "test": "vitest run",
    "preview": "vite preview"
  },
  "dependencies": {
    "react": "^19.0.0",
    "react-dom": "^19.0.0",
    "react-router-dom": "^7.1.0"
  },
  "devDependencies": {
    "@types/react": "^19.0.0",
    "@types/react-dom": "^19.0.0",
    "@vitejs/plugin-react": "^4.3.4",
    "typescript": "~5.7.2",
    "vite": "^6.0.0",
    "vitest": "^3.0.0"
  }
}
```

`qatf-frontend/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "noFallthroughCasesInSwitch": true,
    "skipLibCheck": true,
    "types": ["vite/client"],
    "noEmit": true
  },
  "include": ["src"]
}
```

`qatf-frontend/vite.config.ts`:

```ts
/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The dev proxy mirrors the production nginx contract exactly: the browser
// talks to /api/* and the /api prefix is STRIPPED before the backend sees it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ""),
      },
    },
  },
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
  },
});
```

`qatf-frontend/index.html`:

```html
<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>qatf</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`qatf-frontend/.dockerignore`:

```text
node_modules
dist
```

- [ ] **Step 2: Write the app shell**

`qatf-frontend/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </StrictMode>,
);
```

`qatf-frontend/src/App.tsx` (placeholder pages — Tasks 5-7 replace the route elements):

```tsx
import { Link, Route, Routes } from "react-router-dom";

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <Link to="/" className="brand">qatf <span className="brand-ar">قطف</span></Link>
        <nav>
          <Link to="/">Jobs</Link>
          <Link to="/new" className="btn btn-primary">New job</Link>
        </nav>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<p>jobs dashboard — Task 5</p>} />
          <Route path="/new" element={<p>new job — Task 6</p>} />
          <Route path="/jobs/:id" element={<p>job detail — Task 7</p>} />
        </Routes>
      </main>
    </div>
  );
}
```

`qatf-frontend/src/styles.css`:

```css
:root {
  --bg: #0f1115; --panel: #171a21; --border: #2a2f3a; --text: #e6e9ef;
  --muted: #9aa3b2; --accent: #e0b64c; --danger: #e05c5c; --ok: #4cc38a;
  --info: #5ca8e0;
  font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text); }
.app { max-width: 1100px; margin: 0 auto; padding: 0 1rem 4rem; }
.topbar { display: flex; justify-content: space-between; align-items: center;
  padding: 1rem 0; border-bottom: 1px solid var(--border); margin-bottom: 1.5rem; }
.topbar nav { display: flex; gap: 1rem; align-items: center; }
.brand { font-weight: 700; font-size: 1.2rem; color: var(--text); text-decoration: none; }
.brand-ar { color: var(--accent); }
a { color: var(--info); text-decoration: none; }
a:hover { text-decoration: underline; }
h1 { font-size: 1.4rem; } h2 { font-size: 1.1rem; margin-top: 2rem; }
.btn { display: inline-block; padding: 0.4rem 0.9rem; border-radius: 6px;
  border: 1px solid var(--border); background: var(--panel); color: var(--text);
  cursor: pointer; font-size: 0.9rem; }
.btn:hover { border-color: var(--muted); text-decoration: none; }
.btn-primary { background: var(--accent); border-color: var(--accent); color: #1a1503; font-weight: 600; }
.btn-danger { color: var(--danger); }
.btn:disabled { opacity: 0.45; cursor: not-allowed; }
table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
th, td { text-align: left; padding: 0.5rem 0.6rem; border-bottom: 1px solid var(--border); }
th { color: var(--muted); font-weight: 500; }
.badge { display: inline-block; padding: 0.15rem 0.55rem; border-radius: 999px;
  font-size: 0.78rem; font-weight: 600; }
.badge-running { background: #21384d; color: var(--info); }
.badge-done { background: #1d3a2d; color: var(--ok); }
.badge-planned { background: #3d3420; color: var(--accent); }
.badge-failed { background: #422225; color: var(--danger); }
.badge-cancelled { background: #2a2f3a; color: var(--muted); }
.panel { background: var(--panel); border: 1px solid var(--border);
  border-radius: 8px; padding: 1rem; margin: 1rem 0; }
.banner { border-radius: 8px; padding: 0.6rem 1rem; margin: 0.75rem 0; font-size: 0.88rem; }
.banner-warn { background: #3d3420; color: var(--accent); }
.banner-error { background: #422225; color: var(--danger); }
.banner-info { background: #21384d; color: var(--info); }
.muted { color: var(--muted); }
.mono { font-family: ui-monospace, Consolas, monospace; font-size: 0.85em; }
label { display: block; margin: 0.7rem 0 0.2rem; font-size: 0.85rem; color: var(--muted); }
input, select, textarea { width: 100%; padding: 0.45rem 0.6rem; border-radius: 6px;
  border: 1px solid var(--border); background: var(--bg); color: var(--text); font: inherit; }
input[type="checkbox"] { width: auto; }
fieldset { border: 1px solid var(--border); border-radius: 8px; margin: 1rem 0; padding: 0.5rem 1rem 1rem; }
legend { color: var(--muted); font-size: 0.85rem; padding: 0 0.4rem; }
.field-error { color: var(--danger); font-size: 0.8rem; margin-top: 0.2rem; }
.help { color: var(--muted); font-size: 0.78rem; margin-top: 0.15rem; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 0 1.5rem; }
.grid-3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 0 1.5rem; }
.tabs { display: flex; gap: 0.5rem; margin-bottom: 1rem; }
.tab { padding: 0.45rem 1rem; border-radius: 6px 6px 0 0; cursor: pointer;
  border: 1px solid var(--border); border-bottom: none; background: var(--bg); color: var(--muted); }
.tab.active { background: var(--panel); color: var(--text); }
.toasts { position: fixed; bottom: 1rem; right: 1rem; display: flex;
  flex-direction: column; gap: 0.5rem; z-index: 10; max-width: 26rem; }
.toast { padding: 0.7rem 1rem; border-radius: 8px; font-size: 0.88rem;
  border: 1px solid var(--border); background: var(--panel); }
.toast-error { border-color: var(--danger); }
.toast-ok { border-color: var(--ok); }
.progress { height: 8px; border-radius: 4px; background: var(--border); overflow: hidden; margin: 0.5rem 0; }
.progress > div { height: 100%; background: var(--accent); transition: width 0.2s; }
.clips-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 1rem; }
.clip-card video { width: 100%; border-radius: 8px; background: #000; aspect-ratio: 9 / 16; }
.words { line-height: 2.1; max-height: 24rem; overflow-y: auto; padding: 0.5rem; }
.word { padding: 0.1rem 0.2rem; border-radius: 4px; cursor: pointer; }
.word:hover { background: var(--border); }
.word.edited { background: #3d3420; color: var(--accent); }
.word-input { width: 8rem; display: inline-block; padding: 0.1rem 0.3rem; }
.row-actions { display: flex; gap: 0.3rem; }
.plan-table input { min-width: 5rem; }
```

- [ ] **Step 3: Add frontend entries to the root .gitignore**

Append to `.gitignore` (root):

```gitignore

# ---------------------------------------------------------------------------
# frontend
# ---------------------------------------------------------------------------
node_modules/
qatf-frontend/dist/
```

Note: the working tree already carries an uncommitted user edit to `.gitignore`. Do not revert it — append these lines and commit the file as it then stands.

- [ ] **Step 4: Install and build**

```bash
cd qatf-frontend
npm install
npm run build
```

Expected: `tsc --noEmit` silent, `vite build` writes `dist/` with no errors. `package-lock.json` appears — commit it (Task 10's `npm ci` depends on it).

- [ ] **Step 5: Commit**

```bash
git add .gitignore qatf-frontend
git commit -m "feat(frontend): scaffold Vite + React + TypeScript app shell"
```

---

### Task 4: API layer — types, client, error normalisation (TDD)

**Files:**
- Create: `qatf-frontend/src/api/types.ts`, `qatf-frontend/src/api/client.ts`
- Test: `qatf-frontend/src/api/client.test.ts`

**Interfaces:**
- Consumes: the backend wire contract (`qatf-backend/qatf/api/schemas.py`) — mirrored, never imported.
- Produces (used by every later task):
  - `types.ts`: `JobState`, `TERMINAL_STATES: ReadonlySet<JobState>`, `JobOptions`, `DEFAULT_OPTIONS: JobOptions`, `WHISPER_MODELS: readonly string[]`, `PRESETS: readonly string[]`, `ClipModel`, `WordModel`, `TranscriptResponse`, `ClipOutput`, `JobResponse`, `Health`, `ProviderInfo`
  - `client.ts`: `class ApiError extends Error { status: number }`, `detailFrom(body: unknown, fallback: string): string`, `health(): Promise<Health>`, `listJobs(state?: JobState): Promise<JobResponse[]>`, `getJob(id: string): Promise<JobResponse>`, `createJobFromPath(path: string, options: JobOptions): Promise<JobResponse>`, `createJobFromUrl(url: string, options: JobOptions): Promise<JobResponse>`, `uploadJob(file: File, options: JobOptions, onProgress: (frac: number) => void): Promise<JobResponse>`, `cancelJob(id: string): Promise<JobResponse>`, `deleteJob(id: string): Promise<void>`, `getTranscript(id: string): Promise<TranscriptResponse>`, `putTranscript(id: string, words: WordModel[]): Promise<TranscriptResponse>`, `putPlan(id: string, clips: ClipModel[]): Promise<ClipModel[]>`, `renderJob(id: string): Promise<JobResponse>`, `clipUrl(output: ClipOutput): string`

**Contract facts (verified against the backend source — do not re-derive):**
- All routes sit at the API root (`/jobs`, `/healthz`); the client prefixes `/api`, which nginx/Vite strip.
- `POST /jobs` body is `{path, ...options}` flattened (JobCreate extends JobOptions). Same flattening for `/jobs/url` with `url`.
- `POST /jobs/upload` is multipart: field `file` (the video) + field `options` (JobOptions as a **JSON string**).
- `PUT /jobs/{id}/plan` body `{clips, snap: true}` returns the re-snapped `ClipModel[]`. `GET /jobs` returns `{jobs: [...]}` (unwrap it). `DELETE` returns 204 with no body. Errors are `{"detail": string}` except FastAPI 422s where `detail` is a list of `{loc, msg}` objects.
- `ClipOutput.url` is API-root-relative (e.g. `/jobs/{id}/clips/01-x.mp4`) — prefix `/api` before handing it to `<video>`/`<a>`.

- [ ] **Step 1: Write `types.ts`**

```ts
// Hand-written mirror of qatf-backend/qatf/api/schemas.py. If a field changes
// there, it changes here — grep for the field name in both places.

export type JobState =
  | "queued" | "fetching" | "extracting" | "transcribing" | "selecting"
  | "planned" | "rendering" | "done" | "failed" | "cancelled";

export const TERMINAL_STATES: ReadonlySet<JobState> =
  new Set<JobState>(["done", "failed", "cancelled"]);

export const JOB_STATES: readonly JobState[] = [
  "queued", "fetching", "extracting", "transcribing", "selecting",
  "planned", "rendering", "done", "failed", "cancelled",
];

export interface JobOptions {
  clips: number;
  min_len: number;
  max_len: number;
  reframe: "crop" | "blur" | "track";
  track_tier: "fast" | "balanced" | "best";
  codec: "h264" | "h265";
  preset: string;
  resolution: string;
  ten_bit: boolean;
  crf: number;
  whisper: string;
  device: "auto" | "cuda" | "cpu";
  language: string | null;
  denoise: boolean;
  fixups: Record<string, string> | null;
  hotwords: string | null;
  initial_prompt: string | null;
  font: string;
  captions: boolean;
  per_line: number;
  transcript_source: "auto" | "captions" | "whisper";
  auto_render: boolean;
}

// Server defaults from schemas.py, EXCEPT auto_render: the server defaults to
// true; the UI defaults to false because reviewing the plan is its whole point.
export const DEFAULT_OPTIONS: JobOptions = {
  clips: 5,
  min_len: 30,
  max_len: 75,
  reframe: "crop",
  track_tier: "balanced",
  codec: "h265",
  preset: "medium",
  resolution: "1080p",
  ten_bit: false,
  crf: 20,
  whisper: "large-v3",
  device: "auto",
  language: null,
  denoise: false,
  fixups: null,
  hotwords: null,
  initial_prompt: null,
  font: "Arial",
  captions: true,
  per_line: 4,
  transcript_source: "auto",
  auto_render: false,
};

// asr.MODEL_SIZES, ordered for a dropdown (most-used first).
export const WHISPER_MODELS: readonly string[] = [
  "large-v3", "large-v3-turbo", "turbo", "large-v2", "large-v1", "large",
  "medium", "medium.en", "small", "small.en", "base", "base.en",
  "tiny", "tiny.en",
  "distil-large-v3", "distil-large-v2", "distil-medium.en", "distil-small.en",
];

// encode.PRESETS, slowest first.
export const PRESETS: readonly string[] = [
  "veryslow", "slower", "slow", "medium", "fast", "faster",
  "veryfast", "superfast", "ultrafast",
];

export interface ClipModel {
  start: number;
  end: number;
  title: string;
  hook: string;
  why: string;
  score: number;
}

export interface WordModel {
  text: string;
  start: number;
  end: number;
}

export interface TranscriptResponse {
  language: string | null;
  language_probability: number | null;
  timing_source: "asr" | "captions";
  word_count: number;
  edits_applied: number;
  edits_stale: number;
  words: WordModel[];
}

export interface ClipOutput {
  name: string;
  size_bytes: number;
  url: string;
}

export interface JobResponse {
  id: string;
  state: JobState;
  message: string;
  error: string | null;
  video: string;
  source: "upload" | "path" | "youtube";
  url: string;
  options: JobOptions;
  created_at: string;
  updated_at: string;
  language: string | null;
  device: string | null;
  word_count: number;
  transcript_cached: boolean;
  clips: ClipModel[];
  outputs: ClipOutput[];
}

export interface ProviderInfo {
  key: string;
  default_model: string;
  base_url: string | null;
  key_env: string | null;
  structured_output: "json_schema" | "json_object" | "prompt_only";
  context_tokens: number | null;
  note: string;
}

export interface Health {
  status: "ok" | "degraded";
  version: string;
  model: string;
  ffmpeg: boolean;
  media_root: string;
  max_workers: number;
  llm_provider: string;
  llm_ready: boolean;
  llm_error: string | null;
  providers: ProviderInfo[];
  cuda_devices: number;
  transcribe_device: string;
}
```

- [ ] **Step 2: Write the failing tests for error normalisation**

`qatf-frontend/src/api/client.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, detailFrom, getJob, listJobs } from "./client";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("detailFrom", () => {
  it("uses a string detail verbatim", () => {
    expect(detailFrom({ detail: "job is running" }, "HTTP 409"))
      .toBe("job is running");
  });

  it("flattens FastAPI 422 detail lists to location: reason", () => {
    const body = {
      detail: [
        { loc: ["body", "min_len"], msg: "min_len must be <= max_len" },
        { loc: ["body", "language"], msg: "string does not match pattern" },
      ],
    };
    expect(detailFrom(body, "HTTP 422")).toBe(
      "body.min_len: min_len must be <= max_len; " +
      "body.language: string does not match pattern",
    );
  });

  it("falls back when the body is not an ErrorResponse", () => {
    expect(detailFrom(null, "HTTP 500")).toBe("HTTP 500");
    expect(detailFrom({ weird: true }, "HTTP 500")).toBe("HTTP 500");
  });
});

describe("request layer", () => {
  it("unwraps { jobs } from GET /jobs and prefixes /api", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { jobs: [] }));
    vi.stubGlobal("fetch", fetchMock);
    const jobs = await listJobs();
    expect(jobs).toEqual([]);
    expect(fetchMock).toHaveBeenCalledWith("/api/jobs", undefined);
  });

  it("throws ApiError with the server's detail on a non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      jsonResponse(404, { detail: "no such job" })));
    const err = await getJob("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("no such job");
  });

  it("maps a network failure to ApiError status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    const err = await getJob("x").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(0);
    expect((err as ApiError).message).toBe("cannot reach the server");
  });

  it("survives a non-JSON error body", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
      new Response("<html>bad gateway</html>", { status: 502 })));
    const err = await getJob("x").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(502);
    expect((err as ApiError).message).toBe("HTTP 502");
  });
});
```

- [ ] **Step 3: Run the tests to verify they fail**

```bash
cd qatf-frontend
npm test
```
Expected: FAIL — `./client` does not exist / exports missing.

- [ ] **Step 4: Write `client.ts`**

```ts
import type {
  ClipModel, ClipOutput, Health, JobOptions, JobResponse, JobState,
  TranscriptResponse, WordModel,
} from "./types";

const API_BASE = "/api";

export class ApiError extends Error {
  readonly status: number;
  constructor(status: number, detail: string) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Normalise any error body to one string. Handles ErrorResponse ({detail: str})
 * and FastAPI 422s ({detail: [{loc, msg}, ...]}); anything else -> fallback. */
export function detailFrom(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = (body as { detail: unknown }).detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const parts = detail.map((item: unknown) => {
        if (item && typeof item === "object") {
          const { loc, msg } = item as { loc?: unknown; msg?: unknown };
          const where = Array.isArray(loc) ? loc.join(".") : "";
          const reason = typeof msg === "string" ? msg : JSON.stringify(item);
          return where ? `${where}: ${reason}` : reason;
        }
        return String(item);
      });
      if (parts.length) return parts.join("; ");
    }
  }
  return fallback;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(API_BASE + path, init);
  } catch {
    throw new ApiError(0, "cannot reach the server");
  }
  const text = await res.text();
  let body: unknown = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      body = null; // non-JSON body (proxy error page); fall back to status
    }
  }
  if (!res.ok) throw new ApiError(res.status, detailFrom(body, `HTTP ${res.status}`));
  return body as T;
}

function jsonInit(method: string, payload: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  };
}

export function health(): Promise<Health> {
  return request<Health>("/healthz");
}

export async function listJobs(state?: JobState): Promise<JobResponse[]> {
  const query = state ? `?state=${state}` : "";
  const { jobs } = await request<{ jobs: JobResponse[] }>(`/jobs${query}`);
  return jobs;
}

export function getJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}`);
}

export function createJobFromPath(path: string, options: JobOptions): Promise<JobResponse> {
  return request<JobResponse>("/jobs", jsonInit("POST", { path, ...options }));
}

export function createJobFromUrl(url: string, options: JobOptions): Promise<JobResponse> {
  return request<JobResponse>("/jobs/url", jsonInit("POST", { url, ...options }));
}

/** Multipart upload via XHR — fetch still has no upload-progress events. */
export function uploadJob(
  file: File,
  options: JobOptions,
  onProgress: (frac: number) => void,
): Promise<JobResponse> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE}/jobs/upload`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(e.loaded / e.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        body = null;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(body as JobResponse);
      } else {
        reject(new ApiError(xhr.status, detailFrom(body, `HTTP ${xhr.status}`)));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "cannot reach the server"));
    const form = new FormData();
    form.append("file", file);
    form.append("options", JSON.stringify(options));
    xhr.send(form);
  });
}

export function cancelJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}/cancel`, { method: "POST" });
}

export function deleteJob(id: string): Promise<void> {
  return request<void>(`/jobs/${id}`, { method: "DELETE" });
}

export function getTranscript(id: string): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(`/jobs/${id}/transcript`);
}

export function putTranscript(id: string, words: WordModel[]): Promise<TranscriptResponse> {
  return request<TranscriptResponse>(`/jobs/${id}/transcript`, jsonInit("PUT", { words }));
}

/** snap is ALWAYS true — a typed second is a semantic guess, and the core
 * invariant says acoustic boundaries come from Whisper. No UI toggle. */
export function putPlan(id: string, clips: ClipModel[]): Promise<ClipModel[]> {
  return request<ClipModel[]>(`/jobs/${id}/plan`, jsonInit("PUT", { clips, snap: true }));
}

export function renderJob(id: string): Promise<JobResponse> {
  return request<JobResponse>(`/jobs/${id}/render`, { method: "POST" });
}

/** ClipOutput.url is API-root-relative; the browser needs the /api prefix. */
export function clipUrl(output: ClipOutput): string {
  return API_BASE + output.url;
}
```

- [ ] **Step 5: Run the tests and the build**

```bash
cd qatf-frontend
npm test && npm run build
```
Expected: all tests PASS, build clean.

- [ ] **Step 6: Commit**

```bash
git add qatf-frontend/src/api
git commit -m "feat(frontend): typed API client with normalised errors"
```

---

### Task 5: Polling hook, toasts, badges, health banner, jobs dashboard

**Files:**
- Create: `qatf-frontend/src/api/poll.ts`, `qatf-frontend/src/lib/format.ts`, `qatf-frontend/src/components/Toasts.tsx`, `qatf-frontend/src/components/StateBadge.tsx`, `qatf-frontend/src/components/HealthBanner.tsx`, `qatf-frontend/src/pages/JobsList.tsx`
- Modify: `qatf-frontend/src/App.tsx` (wire the real route + ToastProvider)
- Test: `qatf-frontend/src/lib/format.test.ts`

**Interfaces:**
- Consumes: everything Task 4 exported.
- Produces:
  - `usePolling(fn: () => Promise<void>, intervalMs: number | null): void` — repeats `fn` every `intervalMs`; `null` pauses; never overlaps calls; swallows rejections (pages surface errors themselves).
  - `useToast(): { push: (message: string, kind?: "error" | "ok") => void }` plus `<ToastProvider>` wrapping the app.
  - `<StateBadge state={JobState} />`, `<HealthBanner />` (self-fetching, polls every 30s).
  - `formatAge(iso: string, now?: Date): string` and `formatBytes(n: number): string`, `formatSeconds(s: number): string` in `lib/format.ts`.

- [ ] **Step 1: Write the failing format tests**

`qatf-frontend/src/lib/format.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { formatAge, formatBytes, formatSeconds } from "./format";

describe("formatAge", () => {
  const now = new Date("2026-08-18T12:00:00Z");
  it("says just now under a minute", () => {
    expect(formatAge("2026-08-18T11:59:40Z", now)).toBe("just now");
  });
  it("uses minutes, hours, days", () => {
    expect(formatAge("2026-08-18T11:45:00Z", now)).toBe("15m ago");
    expect(formatAge("2026-08-18T09:00:00Z", now)).toBe("3h ago");
    expect(formatAge("2026-08-15T12:00:00Z", now)).toBe("3d ago");
  });
});

describe("formatBytes", () => {
  it("scales units", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(18_432_119)).toBe("17.6 MB");
    expect(formatBytes(2_147_483_648)).toBe("2.0 GB");
  });
});

describe("formatSeconds", () => {
  it("renders M:SS.cc", () => {
    expect(formatSeconds(184.32)).toBe("3:04.32");
    expect(formatSeconds(59.999)).toBe("1:00.00"); // carry, same trap as ts_ass
  });
});
```

- [ ] **Step 2: Run them to verify failure**

Run: `cd qatf-frontend && npm test` — Expected: FAIL, `./format` missing.

- [ ] **Step 3: Implement `lib/format.ts`**

```ts
export function formatAge(iso: string, now: Date = new Date()): string {
  const seconds = Math.floor((now.getTime() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return "just now";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ago`;
  return `${Math.floor(seconds / 86400)}d ago`;
}

export function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(1)} GB`;
}

/** M:SS.cc for plan boundaries. Round to centiseconds BEFORE decomposing —
 * the backend's ts_ass had the 59.999 -> 0:00:60.00 bug; don't repeat it. */
export function formatSeconds(s: number): string {
  const totalCs = Math.round(s * 100);
  const minutes = Math.floor(totalCs / 6000);
  const rest = totalCs - minutes * 6000;
  const seconds = Math.floor(rest / 100);
  const cs = rest % 100;
  return `${minutes}:${String(seconds).padStart(2, "0")}.${String(cs).padStart(2, "0")}`;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `npm test` — Expected: PASS.

- [ ] **Step 5: Write `api/poll.ts`**

```ts
import { useEffect, useRef } from "react";

/** Poll `fn` every `intervalMs`; pass null to pause. Uses setTimeout chaining,
 * not setInterval, so a slow response never overlaps the next tick. */
export function usePolling(fn: () => Promise<void>, intervalMs: number | null): void {
  const fnRef = useRef(fn);
  fnRef.current = fn;
  useEffect(() => {
    if (intervalMs === null) return;
    let alive = true;
    let timer: number | undefined;
    const tick = async () => {
      try {
        await fnRef.current();
      } catch {
        // pages track their own error state; a failed poll must not kill the loop
      }
      if (alive) timer = window.setTimeout(tick, intervalMs);
    };
    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [intervalMs]);
}
```

- [ ] **Step 6: Write `components/Toasts.tsx`**

```tsx
import { createContext, useCallback, useContext, useRef, useState } from "react";
import type { ReactNode } from "react";

interface Toast { id: number; message: string; kind: "error" | "ok" }
interface ToastApi { push: (message: string, kind?: "error" | "ok") => void }

const ToastContext = createContext<ToastApi>({ push: () => {} });

export function useToast(): ToastApi {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextId = useRef(0);
  const push = useCallback((message: string, kind: "error" | "ok" = "error") => {
    const id = nextId.current++;
    setToasts((current) => [...current, { id, message, kind }]);
    window.setTimeout(
      () => setToasts((current) => current.filter((t) => t.id !== id)),
      6000,
    );
  }, []);
  return (
    <ToastContext.Provider value={{ push }}>
      {children}
      <div className="toasts">
        {toasts.map((t) => (
          <div key={t.id} className={`toast toast-${t.kind}`}>{t.message}</div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
```

- [ ] **Step 7: Write `components/StateBadge.tsx`**

```tsx
import type { JobState } from "../api/types";

const BADGE_CLASS: Record<JobState, string> = {
  queued: "badge-running",
  fetching: "badge-running",
  extracting: "badge-running",
  transcribing: "badge-running",
  selecting: "badge-running",
  planned: "badge-planned",
  rendering: "badge-running",
  done: "badge-done",
  failed: "badge-failed",
  cancelled: "badge-cancelled",
};

export function StateBadge({ state }: { state: JobState }) {
  return <span className={`badge ${BADGE_CLASS[state]}`}>{state}</span>;
}
```

- [ ] **Step 8: Write `components/HealthBanner.tsx`**

```tsx
import { useCallback, useState } from "react";
import { health } from "../api/client";
import { usePolling } from "../api/poll";
import type { Health } from "../api/types";

/** Everything worth knowing BEFORE submitting an hour of audio. */
export function HealthBanner() {
  const [info, setInfo] = useState<Health | null>(null);
  const [unreachable, setUnreachable] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setInfo(await health());
      setUnreachable(false);
    } catch {
      setUnreachable(true);
    }
  }, []);
  usePolling(refresh, 30_000);

  if (unreachable) {
    return <div className="banner banner-error">Cannot reach the qatf server — retrying.</div>;
  }
  if (!info) return null;

  const warnings: string[] = [];
  if (!info.ffmpeg) warnings.push("ffmpeg is missing on the server — nothing can render.");
  if (!info.llm_ready) {
    warnings.push(
      `Stage 3 provider "${info.llm_provider}" has no credential — ` +
      "jobs will fail after transcription has already run.");
  }
  if (info.transcribe_device === "cpu") {
    warnings.push("Transcription will run on CPU — large-v3 on an hour of audio is slow. " +
      "Consider whisper=small while iterating.");
  }
  if (warnings.length === 0) return null;
  return (
    <div className={`banner ${info.status === "degraded" ? "banner-error" : "banner-warn"}`}>
      {warnings.map((w) => <div key={w}>{w}</div>)}
    </div>
  );
}
```

- [ ] **Step 9: Write `pages/JobsList.tsx` and wire the routes**

`qatf-frontend/src/pages/JobsList.tsx`:

```tsx
import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, cancelJob, deleteJob, listJobs } from "../api/client";
import { usePolling } from "../api/poll";
import { JOB_STATES, TERMINAL_STATES } from "../api/types";
import type { JobResponse, JobState } from "../api/types";
import { HealthBanner } from "../components/HealthBanner";
import { StateBadge } from "../components/StateBadge";
import { useToast } from "../components/Toasts";
import { formatAge } from "../lib/format";

export default function JobsList() {
  const [jobs, setJobs] = useState<JobResponse[] | null>(null);
  const [filter, setFilter] = useState<JobState | "">("");
  const [unreachable, setUnreachable] = useState(false);
  const { push } = useToast();

  const refresh = useCallback(async () => {
    try {
      setJobs(await listJobs(filter || undefined));
      setUnreachable(false);
    } catch {
      setUnreachable(true); // banner, not a toast per tick
    }
  }, [filter]);
  usePolling(refresh, 3000);

  async function onCancel(id: string) {
    try {
      await cancelJob(id);
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onDelete(id: string) {
    if (!window.confirm(`Delete job ${id} and all its files?`)) return;
    try {
      await deleteJob(id);
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  return (
    <div>
      <h1>Jobs</h1>
      <HealthBanner />
      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying.</div>
      )}
      <label htmlFor="state-filter">Filter by state</label>
      <select
        id="state-filter"
        style={{ maxWidth: "14rem" }}
        value={filter}
        onChange={(e) => setFilter(e.target.value as JobState | "")}
      >
        <option value="">all</option>
        {JOB_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      {jobs && jobs.length === 0 && (
        <p className="muted">No jobs yet. <Link to="/new">Start one.</Link></p>
      )}
      {jobs && jobs.length > 0 && (
        <table>
          <thead>
            <tr><th>id</th><th>state</th><th>progress</th><th>created</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td><Link to={`/jobs/${job.id}`} className="mono">{job.id}</Link></td>
                <td><StateBadge state={job.state} /></td>
                <td className="muted">{job.error ?? job.message}</td>
                <td className="muted">{formatAge(job.created_at)}</td>
                <td className="row-actions">
                  {!TERMINAL_STATES.has(job.state) && (
                    <button className="btn" onClick={() => onCancel(job.id)}>cancel</button>
                  )}
                  <button
                    className="btn btn-danger"
                    disabled={!TERMINAL_STATES.has(job.state) && job.state !== "planned"}
                    title="the API refuses deletion while a job is running"
                    onClick={() => onDelete(job.id)}
                  >
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

In `App.tsx`: import `JobsList` and `ToastProvider`, wrap the returned tree in `<ToastProvider>` and replace the `/` route element with `<JobsList />`:

```tsx
import { Link, Route, Routes } from "react-router-dom";
import { ToastProvider } from "./components/Toasts";
import JobsList from "./pages/JobsList";

export default function App() {
  return (
    <ToastProvider>
      <div className="app">
        <header className="topbar">
          <Link to="/" className="brand">qatf <span className="brand-ar">قطف</span></Link>
          <nav>
            <Link to="/">Jobs</Link>
            <Link to="/new" className="btn btn-primary">New job</Link>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<JobsList />} />
            <Route path="/new" element={<p>new job — Task 6</p>} />
            <Route path="/jobs/:id" element={<p>job detail — Task 7</p>} />
          </Routes>
        </main>
      </div>
    </ToastProvider>
  );
}
```

- [ ] **Step 10: Verify and commit**

```bash
cd qatf-frontend && npm test && npm run build
git add qatf-frontend/src
git commit -m "feat(frontend): jobs dashboard with polling, toasts and health banner"
```

---

### Task 6: New-job page — source tabs, full options form, upload progress (TDD on the validation mirror)

**Files:**
- Create: `qatf-frontend/src/lib/rules.ts`, `qatf-frontend/src/components/OptionsForm.tsx`, `qatf-frontend/src/pages/NewJob.tsx`
- Modify: `qatf-frontend/src/App.tsx` (route `/new`)
- Test: `qatf-frontend/src/lib/rules.test.ts`

**Interfaces:**
- Consumes: Task 4's types/client, Task 5's `HealthBanner`, `useToast`.
- Produces:
  - `rules.ts`: `type FieldErrors = Record<string, string>`, `validateOptions(o: JobOptions): FieldErrors` (empty object = valid). Tasks 8 and 9 will ADD `transcriptEditGuard` and `durationWarning` to this same file — it is the single home for client-side mirrors of server rules.
  - `<OptionsForm value onChange errors />` — controlled, no internal submit.
  - Route `/new` renders `NewJob`.

- [ ] **Step 1: Write the failing validation tests**

`qatf-frontend/src/lib/rules.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import { DEFAULT_OPTIONS } from "../api/types";
import { validateOptions } from "./rules";

describe("validateOptions", () => {
  it("accepts the defaults", () => {
    expect(validateOptions(DEFAULT_OPTIONS)).toEqual({});
  });

  it("mirrors min_len <= max_len", () => {
    const errors = validateOptions({ ...DEFAULT_OPTIONS, min_len: 60, max_len: 30 });
    expect(errors.min_len).toBeTruthy();
  });

  it("mirrors the language tag pattern (it is part of a cache FILENAME server-side)", () => {
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "ar" })).toEqual({});
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "en-US" })).toEqual({});
    expect(validateOptions({ ...DEFAULT_OPTIONS, language: "../x" }).language).toBeTruthy();
  });

  it("bounds clips, crf and per_line", () => {
    expect(validateOptions({ ...DEFAULT_OPTIONS, clips: 0 }).clips).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, clips: 51 }).clips).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, crf: 52 }).crf).toBeTruthy();
    expect(validateOptions({ ...DEFAULT_OPTIONS, per_line: 9 }).per_line).toBeTruthy();
  });

  it("accepts every resolution form the backend parses", () => {
    for (const r of ["source", "1080p", "1440p", "4k", "1080x1920", "1214x2160"]) {
      expect(validateOptions({ ...DEFAULT_OPTIONS, resolution: r })).toEqual({});
    }
    expect(validateOptions({ ...DEFAULT_OPTIONS, resolution: "huge" }).resolution).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd qatf-frontend && npm test` → FAIL, `./rules` missing.

- [ ] **Step 3: Implement `lib/rules.ts`**

```ts
// Client-side mirrors of server rules — instant feedback only. The server
// stays the authority; nothing here may be the ONLY place a rule lives.
import type { JobOptions } from "../api/types";

export type FieldErrors = Record<string, string>;

const LANGUAGE_RE = /^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})*$/;
const RESOLUTION_RE = /^(source|1080p|1440p|4k|\d{2,5}x\d{2,5})$/;

export function validateOptions(o: JobOptions): FieldErrors {
  const errors: FieldErrors = {};
  if (o.clips < 1 || o.clips > 50) errors.clips = "between 1 and 50";
  if (o.min_len < 1 || o.min_len > 600) errors.min_len = "between 1 and 600 seconds";
  if (o.max_len < 1 || o.max_len > 600) errors.max_len = "between 1 and 600 seconds";
  if (!errors.min_len && !errors.max_len && o.min_len > o.max_len) {
    errors.min_len = "min_len must be <= max_len";
  }
  if (o.crf < 0 || o.crf > 51) errors.crf = "between 0 and 51";
  if (o.per_line < 1 || o.per_line > 8) errors.per_line = "between 1 and 8";
  if (o.language !== null && !LANGUAGE_RE.test(o.language)) {
    errors.language = "a language tag like ar or en-US";
  }
  if (!RESOLUTION_RE.test(o.resolution)) {
    errors.resolution = "source, 1080p, 1440p, 4k, or WxH like 1080x1920";
  }
  return errors;
}
```

- [ ] **Step 4: Run to verify pass** — `npm test` → PASS.

- [ ] **Step 5: Write `components/OptionsForm.tsx`**

```tsx
import { useState } from "react";
import { DEFAULT_OPTIONS, PRESETS, WHISPER_MODELS } from "../api/types";
import type { JobOptions } from "../api/types";
import type { FieldErrors } from "../lib/rules";

interface Props {
  value: JobOptions;
  onChange: (next: JobOptions) => void;
  errors: FieldErrors;
}

/** Every JobOptions knob, grouped the way schemas.py groups them. Controlled;
 * the page owns submit. Empty text inputs map to null for nullable fields. */
export function OptionsForm({ value, onChange, errors }: Props) {
  const set = <K extends keyof JobOptions>(key: K, v: JobOptions[K]) =>
    onChange({ ...value, [key]: v });

  const err = (key: string) =>
    errors[key] ? <div className="field-error">{errors[key]}</div> : null;

  // Rows are LOCAL state, not derived from value.fixups: the record drops
  // empty keys, so a derived freshly-added blank row would vanish instantly.
  const [fixupRows, setFixupRows] = useState<[string, string][]>(
    () => Object.entries(value.fixups ?? {}));
  const setFixups = (rows: [string, string][]) => {
    setFixupRows(rows);
    const record: Record<string, string> = {};
    for (const [wrong, right] of rows) if (wrong) record[wrong] = right;
    set("fixups", Object.keys(record).length ? record : null);
  };

  return (
    <div>
      <fieldset>
        <legend>selection</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-clips">clips</label>
            <input id="opt-clips" type="number" min={1} max={50} value={value.clips}
              onChange={(e) => set("clips", Number(e.target.value))} />
            {err("clips")}
          </div>
          <div>
            <label htmlFor="opt-min-len">min length (s)</label>
            <input id="opt-min-len" type="number" min={1} max={600} value={value.min_len}
              onChange={(e) => set("min_len", Number(e.target.value))} />
            {err("min_len")}
          </div>
          <div>
            <label htmlFor="opt-max-len">max length (s)</label>
            <input id="opt-max-len" type="number" min={1} max={600} value={value.max_len}
              onChange={(e) => set("max_len", Number(e.target.value))} />
            <div className="help">
              Targeting YouTube Shorts? Use 52, not 60 — snapping adds seconds.
            </div>
            {err("max_len")}
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>transcription</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-whisper">whisper model</label>
            <select id="opt-whisper" value={value.whisper}
              onChange={(e) => set("whisper", e.target.value)}>
              {WHISPER_MODELS.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
          <div>
            <label htmlFor="opt-device">device</label>
            <select id="opt-device" value={value.device}
              onChange={(e) => set("device", e.target.value as JobOptions["device"])}>
              <option value="auto">auto (GPU if usable)</option>
              <option value="cuda">cuda (no fallback)</option>
              <option value="cpu">cpu</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-language">language</label>
            <input id="opt-language" placeholder="autodetect" value={value.language ?? ""}
              onChange={(e) => set("language", e.target.value || null)} />
            {err("language")}
          </div>
        </div>
        <div className="grid-2">
          <div>
            <label htmlFor="opt-source">transcript source</label>
            <select id="opt-source" value={value.transcript_source}
              onChange={(e) =>
                set("transcript_source", e.target.value as JobOptions["transcript_source"])}>
              <option value="auto">auto — captions for URLs when word-timed</option>
              <option value="captions">captions (falls back UP to Whisper)</option>
              <option value="whisper">whisper only</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-denoise">
              <input id="opt-denoise" type="checkbox" checked={value.denoise}
                onChange={(e) => set("denoise", e.target.checked)} /> denoise
            </label>
            <div className="help">Speech-band filter before transcribing. Measured 15 → 11
              errors on field audio, and faster.</div>
          </div>
        </div>
        <label htmlFor="opt-hotwords">vocabulary (hotwords)</label>
        <textarea id="opt-hotwords" rows={2} value={value.hotwords ?? ""}
          placeholder="terms spelled the way you want them back — the main quality lever"
          onChange={(e) => set("hotwords", e.target.value || null)} />
        <label htmlFor="opt-prompt">initial prompt</label>
        <textarea id="opt-prompt" rows={2} value={value.initial_prompt ?? ""}
          placeholder="seeds only the first ~30s — prefer vocabulary"
          onChange={(e) => set("initial_prompt", e.target.value || null)} />
        <label>fixups (wrong → right, applied to captions only, never timings)</label>
        {fixupRows.map(([wrong, right], i) => (
          <div key={i} className="grid-2">
            <input value={wrong} placeholder="wrong" dir="auto"
              onChange={(e) => {
                const rows: [string, string][] = [...fixupRows];
                rows[i] = [e.target.value, right];
                setFixups(rows);
              }} />
            <input value={right} placeholder="right" dir="auto"
              onChange={(e) => {
                const rows: [string, string][] = [...fixupRows];
                rows[i] = [wrong, e.target.value];
                setFixups(rows);
              }} />
          </div>
        ))}
        <button type="button" className="btn"
          onClick={() => setFixups([...fixupRows, ["", ""]])}>
          + add fixup
        </button>
      </fieldset>

      <fieldset>
        <legend>captions</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-font">font</label>
            <input id="opt-font" value={value.font}
              onChange={(e) => set("font", e.target.value)} />
            <div className="help">Must be installed on the SERVER. A Latin-only face
              renders Arabic as tofu, silently.</div>
          </div>
          <div>
            <label htmlFor="opt-per-line">words per line</label>
            <input id="opt-per-line" type="number" min={1} max={8} value={value.per_line}
              onChange={(e) => set("per_line", Number(e.target.value))} />
            {err("per_line")}
          </div>
          <div>
            <label htmlFor="opt-captions">
              <input id="opt-captions" type="checkbox" checked={value.captions}
                onChange={(e) => set("captions", e.target.checked)} /> burn captions
            </label>
          </div>
        </div>
      </fieldset>

      <fieldset>
        <legend>encode</legend>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-reframe">reframe</label>
            <select id="opt-reframe" value={value.reframe}
              onChange={(e) => set("reframe", e.target.value as JobOptions["reframe"])}>
              <option value="crop">crop — ~3x the subject pixels (default)</option>
              <option value="blur">blur — full width over blurred fill</option>
              <option value="track">track — follows the largest face</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-tier">track tier</label>
            <select id="opt-tier" value={value.track_tier}
              disabled={value.reframe !== "track"}
              onChange={(e) => set("track_tier", e.target.value as JobOptions["track_tier"])}>
              <option value="fast">fast — 1 fps</option>
              <option value="balanced">balanced — 3 fps</option>
              <option value="best">best — 8 fps</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-resolution">resolution</label>
            <input id="opt-resolution" value={value.resolution}
              onChange={(e) => set("resolution", e.target.value)} />
            <div className="help">source | 1080p | 1440p | 4k | WxH. `source` only makes
              sense with crop.</div>
            {err("resolution")}
          </div>
        </div>
        <div className="grid-3">
          <div>
            <label htmlFor="opt-codec">codec</label>
            <select id="opt-codec" value={value.codec}
              onChange={(e) => set("codec", e.target.value as JobOptions["codec"])}>
              <option value="h265">h265 — smaller, ~3x slower encode</option>
              <option value="h264">h264 — safest for IG/TikTok uploads</option>
            </select>
          </div>
          <div>
            <label htmlFor="opt-preset">preset</label>
            <select id="opt-preset" value={value.preset}
              onChange={(e) => set("preset", e.target.value)}>
              {PRESETS.map((p) => <option key={p} value={p}>{p}</option>)}
            </select>
            <div className="help">THE render-time lever. veryfast ≈ 1.6x faster than
              medium on h265.</div>
          </div>
          <div>
            <label htmlFor="opt-crf">crf</label>
            <input id="opt-crf" type="number" min={0} max={51} value={value.crf}
              onChange={(e) => set("crf", Number(e.target.value))} />
            {err("crf")}
          </div>
        </div>
        <label htmlFor="opt-tenbit">
          <input id="opt-tenbit" type="checkbox" checked={value.ten_bit}
            onChange={(e) => set("ten_bit", e.target.checked)} /> 10-bit
          <span className="help"> — needs a 10-bit source (e.g. ProRes)</span>
        </label>
      </fieldset>

      <fieldset>
        <legend>workflow</legend>
        <label htmlFor="opt-autorender">
          <input id="opt-autorender" type="checkbox" checked={value.auto_render}
            onChange={(e) => set("auto_render", e.target.checked)} /> render immediately
        </label>
        <div className="help">
          Off (default here): the job stops at <b>planned</b> so you can review and edit
          the plan, then render from the job page.
        </div>
        <button type="button" className="btn"
          onClick={() => {
            setFixupRows([]); // local row state must reset with the values
            onChange({ ...DEFAULT_OPTIONS });
          }}>
          reset to defaults
        </button>
      </fieldset>
    </div>
  );
}
```

- [ ] **Step 6: Write `pages/NewJob.tsx`**

```tsx
import { useCallback, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, createJobFromPath, createJobFromUrl, health, uploadJob,
} from "../api/client";
import { usePolling } from "../api/poll";
import { DEFAULT_OPTIONS } from "../api/types";
import type { JobOptions } from "../api/types";
import { HealthBanner } from "../components/HealthBanner";
import { OptionsForm } from "../components/OptionsForm";
import { useToast } from "../components/Toasts";
import { validateOptions } from "../lib/rules";

type Source = "upload" | "url" | "path";

export default function NewJob() {
  const [source, setSource] = useState<Source>("upload");
  const [options, setOptions] = useState<JobOptions>({ ...DEFAULT_OPTIONS });
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [mediaRoot, setMediaRoot] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const navigate = useNavigate();
  const { push } = useToast();

  const refreshRoot = useCallback(async () => {
    try {
      setMediaRoot((await health()).media_root);
    } catch {
      // HealthBanner reports unreachability; the path hint just stays generic
    }
  }, []);
  usePolling(refreshRoot, 60_000);

  const errors = validateOptions(options);

  async function submit() {
    if (Object.keys(errors).length) {
      push("Fix the highlighted fields first.");
      return;
    }
    setBusy(true);
    try {
      let job;
      if (source === "upload") {
        if (!file) {
          push("Choose a video file first.");
          return;
        }
        setProgress(0);
        job = await uploadJob(file, options, setProgress);
      } else if (source === "url") {
        job = await createJobFromUrl(url.trim(), options);
      } else {
        job = await createJobFromPath(path.trim(), options);
      }
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const tab = (key: Source, title: string) => (
    <button type="button"
      className={`tab ${source === key ? "active" : ""}`}
      onClick={() => setSource(key)}>
      {title}
    </button>
  );

  const sourceReady =
    source === "upload" ? file !== null :
    source === "url" ? url.trim() !== "" :
    path.trim() !== "";

  return (
    <div>
      <h1>New job</h1>
      <HealthBanner />
      <div className="tabs">
        {tab("upload", "Upload")}
        {tab("url", "YouTube URL")}
        {tab("path", "Server path")}
      </div>
      <div className="panel">
        {source === "upload" && (
          <div>
            <label htmlFor="src-file">video file</label>
            <input id="src-file" type="file" accept="video/*,.mkv,.mov,.m4v"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {progress !== null && (
              <div>
                <div className="progress"><div style={{ width: `${progress * 100}%` }} /></div>
                <span className="muted">{Math.round(progress * 100)}% uploaded</span>
              </div>
            )}
          </div>
        )}
        {source === "url" && (
          <div>
            <label htmlFor="src-url">YouTube URL</label>
            <input id="src-url" placeholder="https://youtu.be/…" value={url}
              onChange={(e) => setUrl(e.target.value)} />
            <div className="help">Only YouTube hosts are accepted — anything else is a 403.</div>
          </div>
        )}
        {source === "path" && (
          <div>
            <label htmlFor="src-path">path on the server</label>
            <input id="src-path" placeholder="talks/keynote.mov" value={path}
              onChange={(e) => setPath(e.target.value)} />
            <div className="help">
              Relative to {mediaRoot ? <span className="mono">{mediaRoot}</span> : "the media root"};
              absolute paths must still resolve inside it.
            </div>
          </div>
        )}
      </div>
      <OptionsForm value={options} onChange={setOptions} errors={errors} />
      <button className="btn btn-primary" disabled={busy || !sourceReady} onClick={submit}>
        {busy ? "starting…" : "Start job"}
      </button>
    </div>
  );
}
```

In `App.tsx`, replace the `/new` placeholder:

```tsx
import NewJob from "./pages/NewJob";
// …
<Route path="/new" element={<NewJob />} />
```

- [ ] **Step 7: Verify and commit**

```bash
cd qatf-frontend && npm test && npm run build
git add qatf-frontend/src
git commit -m "feat(frontend): new-job page with full options form and upload progress"
```

---

### Task 7: Job-detail page — live status, render, cancel, clips grid

**Files:**
- Create: `qatf-frontend/src/pages/JobDetail.tsx`, `qatf-frontend/src/components/ClipGrid.tsx`
- Modify: `qatf-frontend/src/App.tsx` (route `/jobs/:id`)

**Interfaces:**
- Consumes: Task 4's client/types, Task 5's `usePolling`, `StateBadge`, `useToast`, `formatAge`/`formatBytes`.
- Produces: `JobDetail` owns the job state and exposes two clearly-marked mount points (comments) where Task 8 mounts `<TranscriptEditor jobId job />` and Task 9 mounts `<PlanEditor jobId job onSaved />`. It passes `job: JobResponse` down and provides `reload(): Promise<void>` for children that change server state.

- [ ] **Step 1: Write `components/ClipGrid.tsx`**

```tsx
import { clipUrl } from "../api/client";
import type { ClipOutput } from "../api/types";
import { formatBytes } from "../lib/format";

export function ClipGrid({ outputs }: { outputs: ClipOutput[] }) {
  if (outputs.length === 0) return null;
  return (
    <div className="clips-grid">
      {outputs.map((clip) => (
        <div key={clip.name} className="clip-card">
          <video src={clipUrl(clip)} controls preload="metadata" />
          <div className="mono">{clip.name}</div>
          <div className="muted">
            {formatBytes(clip.size_bytes)} ·{" "}
            <a href={clipUrl(clip)} download={clip.name}>download</a>
          </div>
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 2: Write `pages/JobDetail.tsx`**

```tsx
import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, cancelJob, getJob, renderJob } from "../api/client";
import { usePolling } from "../api/poll";
import { TERMINAL_STATES } from "../api/types";
import type { JobResponse } from "../api/types";
import { ClipGrid } from "../components/ClipGrid";
import { StateBadge } from "../components/StateBadge";
import { useToast } from "../components/Toasts";
import { formatAge } from "../lib/format";

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [missing, setMissing] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const { push } = useToast();

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setJob(await getJob(id));
      setUnreachable(false);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) setMissing(true);
      else setUnreachable(true);
    }
  }, [id]);

  // 2s while working, 10s parked at planned, stopped once terminal.
  const interval =
    job === null ? 2000 :
    TERMINAL_STATES.has(job.state) ? null :
    job.state === "planned" ? 10_000 : 2000;
  usePolling(reload, missing ? null : interval);

  async function onRender() {
    if (!id) return;
    try {
      setJob(await renderJob(id)); // 202 -> queued; polling resumes automatically
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onCancel() {
    if (!id) return;
    try {
      setJob(await cancelJob(id));
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  if (missing) {
    return <p>No such job. <Link to="/">Back to jobs.</Link></p>;
  }
  if (!job) return <p className="muted">loading…</p>;

  const running = !TERMINAL_STATES.has(job.state) && job.state !== "planned";
  const canRender = job.clips.length > 0 && !running;

  return (
    <div>
      <h1>
        <span className="mono">{job.id}</span> <StateBadge state={job.state} />
      </h1>
      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying.</div>
      )}
      <div className="panel">
        <div>{job.error ?? job.message}</div>
        <div className="muted">
          source: {job.source}{job.url ? <> · <span className="mono">{job.url}</span></> : null}
          {" · created "}{formatAge(job.created_at)}
          {" · updated "}{formatAge(job.updated_at)}
        </div>
        <div className="muted">
          {job.language ? `language: ${job.language} · ` : ""}
          {job.device ? `transcribed on: ${job.device} · ` : ""}
          {job.word_count > 0 ? `${job.word_count} words` : "no transcript yet"}
          {job.transcript_cached ? " (cached)" : ""}
        </div>
        <div className="row-actions" style={{ marginTop: "0.6rem" }}>
          {running && <button className="btn" onClick={onCancel}>cancel</button>}
          {canRender && (
            <button className="btn btn-primary" onClick={onRender}
              title="encodes the current plan; replaces any previous outputs">
              {job.outputs.length > 0 ? "re-render" : "render"}
            </button>
          )}
        </div>
        {canRender && job.outputs.length > 0 && (
          <div className="help">Re-rendering replaces the previous outputs.</div>
        )}
      </div>

      {/* PlanEditor mounts here — Task 9 */}

      {/* TranscriptEditor mounts here — Task 8 */}

      {job.outputs.length > 0 && (
        <>
          <h2>clips {job.state === "rendering" ? "(rendering…)" : ""}</h2>
          <ClipGrid outputs={job.outputs} />
        </>
      )}
    </div>
  );
}
```

In `App.tsx`, replace the `/jobs/:id` placeholder:

```tsx
import JobDetail from "./pages/JobDetail";
// …
<Route path="/jobs/:id" element={<JobDetail />} />
```

- [ ] **Step 3: Verify and commit**

```bash
cd qatf-frontend && npm test && npm run build
git add qatf-frontend/src
git commit -m "feat(frontend): job detail page with live status, render and clip previews"
```

---

### Task 8: Transcript editor — text-only corrections, timings untouchable (TDD on the guard)

**Files:**
- Create: `qatf-frontend/src/components/TranscriptEditor.tsx`
- Modify: `qatf-frontend/src/lib/rules.ts` (add `transcriptEditGuard`), `qatf-frontend/src/pages/JobDetail.tsx` (mount at the Task-8 comment)
- Test: `qatf-frontend/src/lib/rules.test.ts` (extend)

**Interfaces:**
- Consumes: Task 4's `getTranscript`/`putTranscript`/`WordModel`/`TranscriptResponse`, Task 5's `useToast`, Task 7's mount point (`<TranscriptEditor jobId={job.id} job={job} />`).
- Produces: `transcriptEditGuard(original: WordModel[], edited: WordModel[]): string | null` in `rules.ts` (null = safe to submit).

**Why the guard exists client-side:** the server refuses a retiming or count change with 422 — the guard mirrors that so the user gets instant feedback, and it protects against a UI bug ever constructing a bad submission. The UI has **no timing inputs at all**; only `text` is editable.

- [ ] **Step 1: Extend the failing tests**

Append to `qatf-frontend/src/lib/rules.test.ts`:

```ts
import { transcriptEditGuard } from "./rules";
import type { WordModel } from "../api/types";

describe("transcriptEditGuard", () => {
  const original: WordModel[] = [
    { text: "هو", start: 204.11, end: 204.29 },
    { text: "من", start: 204.29, end: 204.58 },
  ];

  it("allows a text-only correction", () => {
    const edited = [original[0], { ...original[1], text: "مين" }];
    expect(transcriptEditGuard(original, edited)).toBeNull();
  });

  it("refuses a changed word count", () => {
    expect(transcriptEditGuard(original, [original[0]])).toMatch(/word count/);
  });

  it("refuses a retiming", () => {
    const edited = [original[0], { ...original[1], start: 204.30 }];
    expect(transcriptEditGuard(original, edited)).toMatch(/timing/);
  });
});
```

(The existing `import { describe, expect, it } from "vitest";` at the top of the file already covers these.)

- [ ] **Step 2: Run to verify failure** — `cd qatf-frontend && npm test` → FAIL, `transcriptEditGuard` not exported.

- [ ] **Step 3: Add the guard to `lib/rules.ts`**

Append (and add `WordModel` to the type import from `../api/types`):

```ts
/** Mirror of PUT /jobs/{id}/transcript's contract: only text may differ.
 * Timings come from the audio and are what every cut is snapped to. */
export function transcriptEditGuard(
  original: WordModel[],
  edited: WordModel[],
): string | null {
  if (edited.length !== original.length) {
    return `word count changed (${original.length} -> ${edited.length}) — ` +
      "to split a word, put both words in its text instead";
  }
  for (let i = 0; i < original.length; i++) {
    if (edited[i].start !== original[i].start || edited[i].end !== original[i].end) {
      return `timing changed at word ${i + 1} — timings are never editable`;
    }
  }
  return null;
}
```

- [ ] **Step 4: Run to verify pass** — `npm test` → PASS.

- [ ] **Step 5: Write `components/TranscriptEditor.tsx`**

```tsx
import { useState } from "react";
import { ApiError, getTranscript, putTranscript } from "../api/client";
import type { JobResponse, TranscriptResponse, WordModel } from "../api/types";
import { useToast } from "./Toasts";
import { transcriptEditGuard } from "../lib/rules";

const RTL_LANGS = new Set(["ar", "he", "fa", "ur"]);

interface Props {
  jobId: string;
  job: JobResponse;
}

/** Word-level text corrections. Loaded on demand (a transcript can be 27k
 * words), edited word by word, submitted wholesale — the server diffs it. */
export function TranscriptEditor({ jobId, job }: Props) {
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  const running = !["planned", "done", "failed", "cancelled"].includes(job.state);

  async function load() {
    setLoading(true);
    try {
      setTranscript(await getTranscript(jobId));
      setEdits({});
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  function beginEdit(index: number) {
    if (!transcript) return;
    setEditing(index);
    setDraft(edits[index] ?? transcript.words[index].text);
  }

  function commitEdit() {
    if (editing === null || !transcript) return;
    const originalText = transcript.words[editing].text;
    setEdits((current) => {
      const next = { ...current };
      if (draft === originalText || draft === "") delete next[editing];
      else next[editing] = draft;
      return next;
    });
    setEditing(null);
  }

  async function save() {
    if (!transcript) return;
    const edited: WordModel[] = transcript.words.map((word, i) =>
      i in edits ? { ...word, text: edits[i] } : word);
    const problem = transcriptEditGuard(transcript.words, edited);
    if (problem) {
      push(problem);
      return;
    }
    setSaving(true);
    try {
      const response = await putTranscript(jobId, edited);
      setTranscript(response);
      setEdits({});
      const stale = response.edits_stale > 0
        ? ` (${response.edits_stale} stale — the transcript moved underneath them)` : "";
      push(`${response.edits_applied} correction(s) in effect${stale}.`, "ok");
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  if (job.word_count === 0) return null;

  const language = transcript?.language ?? job.language;
  const dir = language && RTL_LANGS.has(language.split("-")[0]) ? "rtl" : "auto";
  const dirty = Object.keys(edits).length > 0;

  return (
    <div>
      <h2>transcript</h2>
      {!transcript && (
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? "loading…" : `show ${job.word_count} words`}
        </button>
      )}
      {transcript && (
        <div className="panel">
          <div className="muted">
            {transcript.language ?? "unknown language"}
            {transcript.language_probability !== null
              ? ` (p=${transcript.language_probability.toFixed(3)})` : ""}
            {" · timings from "}{transcript.timing_source}
            {" · "}{transcript.edits_applied} correction(s) in effect
            {transcript.edits_stale > 0 ? ` · ${transcript.edits_stale} STALE` : ""}
          </div>
          <div className="help">
            Click a word to correct its text. Timings are not editable — a correction
            changes what a caption reads and can never move a cut.
          </div>
          <div className="words" dir={dir}>
            {transcript.words.map((word, i) =>
              editing === i ? (
                <input
                  key={i}
                  className="word-input"
                  dir={dir}
                  autoFocus
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  onBlur={commitEdit}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") commitEdit();
                    if (e.key === "Escape") setEditing(null);
                  }}
                />
              ) : (
                <span
                  key={i}
                  className={`word ${i in edits ? "edited" : ""}`}
                  title={`${word.start.toFixed(2)}–${word.end.toFixed(2)}s`}
                  onClick={() => beginEdit(i)}
                >
                  {(i in edits ? edits[i] : word.text) + " "}
                </span>
              ),
            )}
          </div>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={save}
              disabled={!dirty || saving || running}
              title={running ? "the job is running — corrections are refused until it stops" : ""}>
              {saving ? "saving…" : `save ${Object.keys(edits).length} correction(s)`}
            </button>
            {dirty && (
              <button className="btn" onClick={() => setEdits({})}>discard</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 6: Mount it in `JobDetail.tsx`**

Replace the comment `{/* TranscriptEditor mounts here — Task 8 */}` with:

```tsx
<TranscriptEditor jobId={job.id} job={job} />
```

and add the import:

```tsx
import { TranscriptEditor } from "../components/TranscriptEditor";
```

- [ ] **Step 7: Verify and commit**

```bash
cd qatf-frontend && npm test && npm run build
git add qatf-frontend/src
git commit -m "feat(frontend): transcript editor — text-only corrections with guard"
```

---

### Task 9: Plan editor — hand-edit round trip, always re-snapped (TDD on the duration rule)

**Files:**
- Create: `qatf-frontend/src/components/PlanEditor.tsx`
- Modify: `qatf-frontend/src/lib/rules.ts` (add `DURATION_SLACK`, `durationWarning`), `qatf-frontend/src/pages/JobDetail.tsx` (mount at the Task-9 comment)
- Test: `qatf-frontend/src/lib/rules.test.ts` (extend)

**Interfaces:**
- Consumes: Task 4's `putPlan`/`ClipModel`, Task 5's `useToast`, `formatSeconds`, Task 7's mount point and `reload`.
- Produces: `DURATION_SLACK = 2.0` and `durationWarning(clip: ClipModel, maxLen: number): string | null` in `rules.ts`. `<PlanEditor jobId={job.id} job={job} onSaved={reload} />`.

**The invariant, again:** the save path always sends `snap: true` (fixed inside `putPlan`, Task 4). After save the UI **replaces the draft with the response** — the re-snapped boundaries — so the user sees where the cuts actually landed. `snap` is idempotent, so saving an untouched plan changes nothing.

- [ ] **Step 1: Extend the failing tests**

Append to `qatf-frontend/src/lib/rules.test.ts`:

```ts
import { DURATION_SLACK, durationWarning } from "./rules";
import type { ClipModel } from "../api/types";

describe("durationWarning", () => {
  const clip = (start: number, end: number): ClipModel =>
    ({ start, end, title: "t", hook: "", why: "", score: 0 });

  it("accepts a clip inside max_len + slack", () => {
    expect(durationWarning(clip(0, 52), 52)).toBeNull();
    expect(durationWarning(clip(0, 52 + DURATION_SLACK), 52)).toBeNull();
  });

  it("warns beyond max_len + slack — the same absolute rule within_duration applies", () => {
    expect(durationWarning(clip(0, 55), 52)).toMatch(/max_len/);
  });

  it("flags a non-positive duration as an error", () => {
    expect(durationWarning(clip(30, 30), 52)).toMatch(/end/);
  });
});
```

- [ ] **Step 2: Run to verify failure** — `cd qatf-frontend && npm test` → FAIL.

- [ ] **Step 3: Add the rule to `lib/rules.ts`**

Append (and add `ClipModel` to the type import from `../api/types`):

```ts
/** Mirror of core.constants.DURATION_SLACK — the absolute allowance
 * within_duration grants either side of the requested bounds. */
export const DURATION_SLACK = 2.0;

/** Warn when a hand-edited clip would fall outside what the pipeline keeps.
 * Absolute slack, not proportional — that is a deliberate backend decision. */
export function durationWarning(clip: ClipModel, maxLen: number): string | null {
  const duration = clip.end - clip.start;
  if (duration <= 0) return "end must be after start";
  if (duration > maxLen + DURATION_SLACK) {
    return `${duration.toFixed(1)}s exceeds max_len ${maxLen}s + ${DURATION_SLACK}s slack — ` +
      "the render keeps it, but Shorts may reject it";
  }
  return null;
}
```

- [ ] **Step 4: Run to verify pass** — `npm test` → PASS.

- [ ] **Step 5: Write `components/PlanEditor.tsx`**

```tsx
import { useState } from "react";
import { ApiError, putPlan } from "../api/client";
import type { ClipModel, JobResponse } from "../api/types";
import { useToast } from "./Toasts";
import { durationWarning } from "../lib/rules";
import { formatSeconds } from "../lib/format";

interface Props {
  jobId: string;
  job: JobResponse;
  onSaved: () => Promise<void>;
}

/** Edit the plan the model produced. Boundaries typed here are SEMANTIC
 * guesses — the server re-snaps them onto Whisper word times (snap is always
 * true), and the saved response replaces the draft so the user sees where the
 * cuts actually landed. */
export function PlanEditor({ jobId, job, onSaved }: Props) {
  const [draft, setDraft] = useState<ClipModel[]>(() =>
    job.clips.map((clip) => ({ ...clip })));
  const [saving, setSaving] = useState(false);
  const [snapped, setSnapped] = useState(false);
  const { push } = useToast();

  const editable = job.state === "planned" || job.state === "done";
  if (job.clips.length === 0) return null;

  const update = (i: number, patch: Partial<ClipModel>) =>
    setDraft((current) => current.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  const move = (i: number, delta: number) =>
    setDraft((current) => {
      const j = i + delta;
      if (j < 0 || j >= current.length) return current;
      const next = [...current];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const remove = (i: number) =>
    setDraft((current) => current.filter((_, j) => j !== i));

  const add = () =>
    setDraft((current) => {
      const last = current[current.length - 1];
      const start = last ? last.end + 1 : 0;
      return [...current, {
        start, end: start + job.options.min_len,
        title: "clip", hook: "", why: "", score: 0,
      }];
    });

  async function save() {
    for (const [i, clip] of draft.entries()) {
      if (clip.end <= clip.start) {
        push(`clip ${i + 1}: end must be after start`);
        return;
      }
    }
    if (draft.length === 0) {
      push("a plan needs at least one clip — delete the job instead");
      return;
    }
    setSaving(true);
    try {
      const stored = await putPlan(jobId, draft);
      setDraft(stored.map((clip) => ({ ...clip })));
      setSnapped(true);
      push("Plan saved — boundaries re-snapped onto word times.", "ok");
      await onSaved();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <h2>plan</h2>
      <div className="panel">
        {!editable && (
          <div className="help">The plan is read-only while the job is working.</div>
        )}
        {snapped && (
          <div className="banner banner-info">
            These are the snapped boundaries the render will use.
          </div>
        )}
        <table className="plan-table">
          <thead>
            <tr>
              <th>#</th><th>start (s)</th><th>end (s)</th><th>length</th>
              <th>title</th><th>score</th><th></th>
            </tr>
          </thead>
          <tbody>
            {draft.map((clip, i) => {
              const warning = durationWarning(clip, job.options.max_len);
              return (
                <tr key={i}>
                  <td>{String(i + 1).padStart(2, "0")}</td>
                  <td>
                    <input type="number" step={0.01} min={0} value={clip.start}
                      disabled={!editable}
                      onChange={(e) => update(i, { start: Number(e.target.value) })} />
                  </td>
                  <td>
                    <input type="number" step={0.01} min={0} value={clip.end}
                      disabled={!editable}
                      onChange={(e) => update(i, { end: Number(e.target.value) })} />
                  </td>
                  <td className="mono">
                    {formatSeconds(Math.max(0, clip.end - clip.start))}
                    {warning && <div className="field-error">{warning}</div>}
                  </td>
                  <td>
                    <input value={clip.title} dir="auto" disabled={!editable}
                      title={clip.hook ? `hook: ${clip.hook}\nwhy: ${clip.why}` : undefined}
                      onChange={(e) => update(i, { title: e.target.value })} />
                  </td>
                  <td className="muted">{clip.score.toFixed(2)}</td>
                  <td className="row-actions">
                    <button className="btn" disabled={!editable || i === 0}
                      onClick={() => move(i, -1)}>↑</button>
                    <button className="btn" disabled={!editable || i === draft.length - 1}
                      onClick={() => move(i, 1)}>↓</button>
                    <button className="btn btn-danger" disabled={!editable}
                      onClick={() => remove(i)}>✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row-actions" style={{ marginTop: "0.6rem" }}>
          <button className="btn" disabled={!editable} onClick={add}>+ add clip</button>
          <button className="btn btn-primary" disabled={!editable || saving} onClick={save}>
            {saving ? "saving…" : "save plan (re-snaps)"}
          </button>
          <button className="btn" disabled={!editable}
            onClick={() => {
              setDraft(job.clips.map((clip) => ({ ...clip })));
              setSnapped(false);
            }}>
            reset to stored plan
          </button>
        </div>
        <div className="help">
          Typed seconds are semantic guesses — the server snaps them onto Whisper word
          boundaries on save. Titles become the output filenames (ASCII-slugified).
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 6: Mount it in `JobDetail.tsx`**

Replace the comment `{/* PlanEditor mounts here — Task 9 */}` with:

```tsx
<PlanEditor key={`${job.id}-${job.clips.length}`} jobId={job.id} job={job} onSaved={reload} />
```

and add the import:

```tsx
import { PlanEditor } from "../components/PlanEditor";
```

(The `key` remounts the editor if the server's plan appears or changes size underneath it — e.g. the job just reached `planned` — so the draft never shows a stale empty list.)

- [ ] **Step 7: Verify and commit**

```bash
cd qatf-frontend && npm test && npm run build
git add qatf-frontend/src
git commit -m "feat(frontend): plan editor with re-snap round trip and duration warnings"
```

---

### Task 10: Frontend Dockerfile, nginx proxy, compose service — one command starts both

**Files:**
- Create: `qatf-frontend/Dockerfile`, `qatf-frontend/nginx.conf`
- Modify: `docker-compose.yaml` (add the `frontend` service)

**Interfaces:**
- Consumes: Task 3's `package-lock.json` (`npm ci`), the compose service name `qatf` from Task 2 (nginx's upstream), Task 4's `/api` prefix convention.
- Produces: `docker compose up` starts backend on :8000 and frontend on :3000.

- [ ] **Step 1: Write `qatf-frontend/nginx.conf`**

```nginx
# Serves the static build and proxies /api/* to the backend service.
# The trailing slash on proxy_pass STRIPS the /api prefix — the backend's
# routes are /jobs, /healthz, and it never learns a proxy exists.
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location /api/ {
        proxy_pass http://qatf:8000/;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

        # Uploads are multi-GB. nginx's 1 MB default would kill every upload
        # before FastAPI's own QATF_MAX_UPLOAD_MB check ever ran; 0 disables
        # the nginx limit so the backend stays the single authority.
        client_max_body_size 0;
        # Stream the body through instead of spooling gigabytes to nginx's
        # temp dir first — also what makes upload progress bars honest.
        proxy_request_buffering off;

        # Clip downloads and slow endpoints.
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }

    # SPA fallback: /jobs/abc123 is a client-side route, not a file.
    location / {
        try_files $uri /index.html;
    }
}
```

- [ ] **Step 2: Write `qatf-frontend/Dockerfile`**

```dockerfile
# Build stage: strict type check + bundle. npm ci needs package-lock.json.
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json vite.config.ts index.html ./
COPY src ./src
RUN npm run build

# Runtime: static files + the /api proxy. No node in the final image.
FROM nginx:alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
```

- [ ] **Step 3: Add the frontend service to `docker-compose.yaml`**

Insert after the `qatf` service (same indentation level), before the ollama section:

```yaml
  # ---- web UI ---------------------------------------------------------
  # Serves the SPA on :3000 and proxies /api/* to the qatf service, so the
  # browser sees one origin and the backend needs no CORS. The backend still
  # publishes :8000 for curl, /docs and the test suites.
  frontend:
    build: ./qatf-frontend
    ports: ["3000:80"]
    depends_on: [qatf]
```

- [ ] **Step 4: Build and start the stack**

```bash
docker compose config -q
docker compose build
docker compose up -d
```
Expected: both images build; both containers start. (No Docker daemon? Stop here, mark the remaining steps blocked in the task report, and still commit — the files are inert without Docker.)

- [ ] **Step 5: Verify the wiring**

```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/            # 200, the SPA
curl -s http://localhost:3000/api/healthz                                # JSON through the proxy
curl -s http://localhost:8000/healthz                                    # JSON direct — unchanged
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000/jobs/nope   # 200 — SPA fallback, NOT 404
```
Expected: exactly as annotated. The two `/healthz` bodies must be identical. Then:

```bash
docker compose down
```

- [ ] **Step 6: Commit**

```bash
git add qatf-frontend/Dockerfile qatf-frontend/nginx.conf docker-compose.yaml
git commit -m "build: frontend image with nginx /api proxy; compose starts both services"
```

---

### Task 11: Documentation sweep — CLAUDE.md, README, docs/

**Files:**
- Modify: `CLAUDE.md`, `README.md`, `docs/operations.md`, `docs/providers.md`, `docs/security.md`, `docs/api.md`, `docs/architecture.md`, `docs/cli.md` (the last four only where grep finds stale paths)

**Interfaces:**
- Consumes: the final layout from Tasks 1-10.
- Produces: documentation that matches the repo. No measured numbers move (they live in `docs/quality.md` and `CLAUDE.md` only).

- [ ] **Step 1: Find every stale reference**

```bash
grep -rn "docker-compose.yml\|build: \.\b" README.md docs/*.md CLAUDE.md
grep -rn "pip install -e\|python tests/\|qatf talk\|uvicorn qatf" README.md docs/*.md CLAUDE.md
```
Fix each hit so paths and commands reflect `qatf-backend/` and `docker-compose.yaml`. Commands that ran from the repo root now say `cd qatf-backend` first (or use the path prefix where a one-liner reads better).

- [ ] **Step 2: Update CLAUDE.md**

Three edits, exact content:

1. In the **Layout** section, wrap the existing tree: the `qatf/` package tree and `tests/` lines now sit under a `qatf-backend/` root, and add these lines to the tree (keep the existing per-module annotations untouched):

```text
qatf-backend/      the Python pipeline, CLI and API — everything below lives here
  qatf/            (the package tree, unchanged)
  tests/           (unchanged)
  prompts/         vocab and fixup lists
  Dockerfile       backend image; build context is qatf-backend/
qatf-frontend/     the web UI. React + Vite + TS, served by nginx in Docker
  src/api/         hand-written mirror of the wire contract + fetch client
  src/pages/       JobsList, NewJob, JobDetail
  src/components/  OptionsForm, PlanEditor, TranscriptEditor, ClipGrid, …
  src/lib/rules.ts client-side mirrors of server rules (never the only line)
  nginx.conf       serves the SPA, proxies /api/* -> qatf:8000 with prefix stripped
docker-compose.yaml  one `docker compose up` starts backend (:8000) + frontend (:3000)
```

2. In the **Commands** section, prefix the pip/test/ruff/CLI examples with `cd qatf-backend`, and add at the top:

```bash
docker compose up                    # backend :8000 + web UI :3000
cd qatf-frontend && npm run dev      # UI dev server; proxies /api to localhost:8000
cd qatf-frontend && npm test         # vitest — the client-side rule mirrors
```

3. Add a short section after "API shape":

```markdown
## The web UI

`qatf-frontend/` is a React SPA over the existing HTTP API — **purely additive;
no UI feature may require a backend change**, the same philosophy as "a provider
swap cannot affect cut accuracy". nginx serves the static build and proxies
`/api/*` to the backend with the prefix stripped (trailing slash on
`proxy_pass`), so the backend has no CORS and never learns a proxy exists.
`client_max_body_size 0` + `proxy_request_buffering off` are load-bearing:
uploads are multi-GB and the backend's `QATF_MAX_UPLOAD_MB` must stay the single
authority on size.

The UI enforces the core invariant by construction: the transcript editor has no
timing inputs, `PUT /plan` is always sent with `snap: true`, and the saved
response replaces the draft so the user sees where cuts actually landed.
Client-side rule mirrors live in `src/lib/rules.ts` and are exactly that —
mirrors for instant feedback; the server stays the authority on every one.
```

- [ ] **Step 3: Update README.md**

Quickstart becomes (adjust surrounding prose to match):

```bash
docker compose up          # backend on :8000, web UI on http://localhost:3000
```

with the CLI/API installs pointing into `qatf-backend/`.

- [ ] **Step 4: Update docs/operations.md, providers.md, security.md**

- `operations.md`: compose filename, build contexts, the frontend service, and a note that the UI adds no auth — same trust model as the API (anything in front of the server is the only gate).
- `providers.md`: compose profile commands unchanged in behaviour; fix any `docker-compose.yml` spelling.
- `security.md`: one added paragraph — the nginx proxy is not a new trust boundary (it forwards to the same unauthenticated API on the same host); the upload size check stays in FastAPI because `client_max_body_size` is deliberately 0.

- [ ] **Step 5: Verify and commit**

```bash
grep -rn "docker-compose.yml" README.md docs CLAUDE.md   # expect: no hits
cd qatf-backend && python tests/smoke_api.py             # docs task broke nothing
git add CLAUDE.md README.md docs
git commit -m "docs: two-package layout, web UI section, compose quickstart"
```

---

## Final verification (after all tasks)

From the repo root, on the `web-ui` branch:

```bash
cd qatf-backend && python tests/smoke_pipeline.py && python tests/smoke_db.py \
  && python tests/smoke_llm.py && python tests/smoke_api.py && ruff check . && cd ..
cd qatf-frontend && npm test && npm run build && cd ..
docker compose build && docker compose up -d
curl -s http://localhost:3000/api/healthz    # through the proxy
docker compose down
```

Then the manual walk (needs a small fixture in `./media/`): open `http://localhost:3000`, start a job from the Server-path tab with `auto_render` off, watch it reach `planned`, nudge one clip boundary, save (observe the snapped values return), render, preview the clip in the browser.

