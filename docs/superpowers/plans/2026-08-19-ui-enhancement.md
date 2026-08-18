# qatf UI Enhancement Implementation Plan

**Goal:** Give the qatf SPA a distinctive dark "confident editorial" identity, a dedicated transcript page, and prominent clips.

**Architecture:** One CSS token/class system authored first; every component then written against that fixed class-name contract. No new npm dependencies, no backend/nginx/compose changes, no API-contract changes.

**Tech Stack:** React 19 + TypeScript strict + Vite (existing). Fonts via Google Fonts `<link>`. Existing vitest suite must stay green.

**Spec:** `docs/superpowers/specs/2026-08-19-ui-enhancement-design.md`

---

## Global Constraints

- **No new npm dependencies.** No Tailwind, no icon library, no animation library. SVG is inlined by hand where needed.
- **No backend, nginx, Dockerfile, or compose changes.** The frontend stays purely additive.
- **No changes to `src/api/client.ts` or `src/api/types.ts`** — the wire contract is fixed. `putPlan` keeps `snap: true` hardcoded; the transcript editor keeps having no timing inputs.
- **The class-name contract in this plan is binding.** CSS defines exactly these names; components use exactly these names. Neither side invents new ones without the other.
- Every change must keep `npm run build` (`tsc --noEmit && vite build`) and `npm test` green.
- Dark theme only. Quality floor, unannounced: responsive to 380px, visible `:focus-visible` rings, `prefers-reduced-motion: reduce` disables transitions/animations.
- Work in `qatf-frontend/`. Windows host; use the Bash tool.
- **Commits carry no co-author or attribution trailer.**
- **Honest data only.** The API gives no source-video duration. The harvest strip scales `0 → max(clip.end)` and labels both ends; nothing may imply a total duration we do not have.

---

## The design system (authored once, in Task 1)

### Tokens — use these exact values

```css
:root {
  /* ground: warm brown-black, deliberately NOT blue-gray */
  --ink:          #14100C;
  --ink-raised:   #1E1813;
  --ink-sunken:   #0D0A07;
  --line:         #2E261D;
  --line-strong:  #43382B;

  /* text */
  --parchment:    #F2E8D8;
  --muted:        #9A8B77;
  --dim:          #6B5F4F;

  /* accents — two, not one */
  --saffron:      #E8A33D;   /* harvest: primary action, brand, picked spans */
  --saffron-dim:  #7A5518;
  --sabr:         #6E8F6B;   /* done: earthy olive, deliberately desaturated */
  --clay:         #C4573F;   /* failure */
  --sky:          #6B8CAF;   /* in-progress / info */

  --radius:       10px;
  --radius-sm:    6px;
  --gap:          1rem;
  --shadow:       0 1px 0 rgba(242,232,216,.04), 0 8px 24px rgba(0,0,0,.45);

  --font-ui:      "IBM Plex Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-ar:      "IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif;
  --font-mono:    "IBM Plex Mono", ui-monospace, Consolas, monospace;
  --font-brand:   "Aref Ruqaa", "IBM Plex Sans Arabic", serif;
}
```

### Fonts — exact `<link>` for `index.html` `<head>`

```html
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link
  href="https://fonts.googleapis.com/css2?family=Aref+Ruqaa:wght@400;700&family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans+Arabic:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap"
  rel="stylesheet"
/>
```

### Class-name contract (binding both directions)

| Group | Classes |
| --- | --- |
| Shell | `.app` `.topbar` `.brand` `.brand-ar` `.brand-latin` `.nav` `.nav-link` `.nav-link.active` `.page` `.page-head` `.page-title` `.back-link` |
| Buttons | `.btn` `.btn-primary` `.btn-ghost` `.btn-danger` `.btn-sm` |
| Chips | `.chips` `.chip` `.chip.active` `.chip-count` |
| State | `.state` `.state-dot` and modifiers `.state--running` `.state--planned` `.state--done` `.state--failed` `.state--cancelled` |
| Surfaces | `.card` `.card-head` `.card-title` `.card-sub` `.card-body` `.panel` |
| Job list | `.job-list` `.job-row` `.job-thumb` `.job-thumb-glyph` `.job-main` `.job-id` `.job-meta` `.job-msg` `.job-actions` |
| Harvest strip | `.strip` `.strip-track` `.strip-span` `.strip-scale` |
| Stage timeline | `.timeline` `.timeline-step` `.timeline-step.is-done` `.timeline-step.is-current` `.timeline-dot` `.timeline-label` |
| Empty / loading | `.empty` `.empty-title` `.empty-body` `.skeleton` `.skeleton-row` |
| Forms | `.field` `.field-label` `.field-help` `.field-error` `.fieldset` `.fieldset-title` `.grid-2` `.grid-3` `.switch` `.tabs` `.tab` `.tab.active` |
| Upload | `.dropzone` `.dropzone.is-dragging` `.dropzone-hint` `.file-pill` `.progress` |
| Sticky bar | `.sticky-bar` `.sticky-bar-status` `.sticky-bar-actions` |
| Clips | `.clips` `.clip` `.clip-video` `.clip-meta` `.clip-name` |
| Plan | `.plan` `.plan-num` `.plan-warn` |
| Transcript | `.transcript-head` `.transcript-stat` `.words` `.word` `.word.is-edited` `.word-input` |
| Feedback | `.banner` `.banner-warn` `.banner-error` `.banner-info` `.toasts` `.toast` `.toast-error` `.toast-ok` |
| Utility | `.mono` `.muted` `.dim` `.row` `.spread` `.stack` `.tnum` `.truncate` |

### Voice for copy

Active, sentence case, specific. Buttons name the action and keep the same word through the flow ("Start job" → toast "Job started"). Empty states invite an action. Errors say what happened and what to do — never apologize, never vague.

---

## Task 1: Design system — tokens, fonts, all component classes

**Files:** Rewrite `qatf-frontend/src/styles.css`; edit `qatf-frontend/index.html` (font links + `<title>qatf — pick the good parts</title>`).

**This is the only task that writes CSS.** Every class in the contract table above must exist and be styled; components in later tasks add no CSS of their own.

Requirements:

- Tokens exactly as given. Warm ground, two accents, no pure black or pure white anywhere.
- `body` uses `--font-ui`; `[dir="rtl"]`, `.words`, `.word`, `.word-input`, and any element with `lang="ar"` use `--font-ar`. `.mono`, `.tnum`, timecodes, ids, and sizes use `--font-mono` with `font-variant-numeric: tabular-nums`.
- `.brand-ar` uses `--font-brand` at ~2rem, `--saffron`; `.brand-latin` is small, `--dim`, letterspaced, lowercase.
- `.topbar` is asymmetric: brand left, nav right, one hairline `--line` bottom border, sticky at top with the page background.
- `.state` is a dot + label: `.state-dot` is an 8px circle colored by modifier — running `--sky`, planned `--saffron`, done `--sabr`, failed `--clay`, cancelled `--dim`. Running's dot pulses gently (2.4s), disabled under reduced motion.
- `.strip` (the signature): `.strip-track` is a 10px-tall `--ink-sunken` bar with `--radius-sm`; `.strip-span` is an absolutely positioned `--saffron` block inside it (left/width set inline by the component), `--radius-sm`, subtle glow `0 0 0 1px rgba(232,163,61,.35)`. `.strip-scale` is a mono `--dim` row under the track, `.spread`, for the two end labels. Full-size and a `.strip.is-mini` variant (6px track, no scale row).
- `.timeline` is a horizontal row of seven `.timeline-step`s connected by a hairline; `.timeline-dot` is 7px; `.is-done` steps are `--sabr`, `.is-current` is `--saffron` and its `.timeline-label` is `--parchment` and 600 weight, future steps are `--dim`. Labels shrink to icons-only under 640px (hide `.timeline-label` text with a `sr-only` pattern or `display:none` — pick one and be consistent).
- `.job-row` is a card: 84px-wide 9:16 `.job-thumb` on the left (black, `--radius-sm`, `overflow:hidden`, video fills it via `object-fit: cover`), `.job-main` flexible, `.job-actions` right. Hover raises the border to `--line-strong`. Grid collapses to thumb-above-content under 560px.
- `.clips` is `repeat(auto-fill, minmax(190px, 1fr))`; `.clip-video` is `aspect-ratio: 9/16`, black, `--radius`. `.clip` hover lifts 2px with a shadow (reduced-motion: no transform).
- `.dropzone` is a dashed `--line-strong` box, min-height 140px, centered content; `.is-dragging` switches border to `--saffron` and background to a 6% saffron tint.
- `.sticky-bar` is `position: sticky; bottom: 0`, `--ink-raised` background, top hairline, `--shadow`, padding, `.spread` layout, and sits above content (`z-index: 5`).
- `.skeleton` shimmers between `--ink-raised` and `--line` (1.4s), disabled under reduced motion (flat `--ink-raised`).
- `.words` sets `line-height: 2.2`, `font-size: 1.05rem`, max-height none (the page scrolls, not an inner box). `.word` has a 4px radius, hover `--line`, `.is-edited` gets a saffron underline (`box-shadow: inset 0 -2px 0 var(--saffron)`) and `--parchment` text — never a filled block, so RTL text stays readable.
- Focus: `:focus-visible { outline: 2px solid var(--saffron); outline-offset: 2px; }` on every interactive element.
- End the file with `@media (prefers-reduced-motion: reduce) { *, *::before, *::after { animation-duration: .01ms !important; animation-iteration-count: 1 !important; transition-duration: .01ms !important; } }`.
- Beware selector-specificity collisions: keep component classes flat (single class selectors), never nest a type selector that could cancel a utility.

Verify: `npm run build` succeeds. Commit `style(frontend): warm editorial dark design system`.

---

## Task 2: `stageIndex` helper (TDD)

**Files:** `qatf-frontend/src/lib/format.ts` (append), `qatf-frontend/src/lib/format.test.ts` (append).

Add to `format.ts`:

```ts
import type { JobState } from "../api/types";

/** The seven pipeline stages a job walks, in order. `fetching` only occurs on
 * a URL-sourced job, but it keeps its slot so the timeline never re-flows. */
export const STAGES: readonly JobState[] = [
  "queued", "fetching", "extracting", "transcribing", "selecting", "planned", "rendering",
];

/** How far along `state` is: the index into STAGES, `STAGES.length` for done,
 * and -1 for a state that is not on the happy path (failed, cancelled).
 * Derived from the state alone — never from the progress message, which is
 * free text and would break the moment the worker's wording changes. */
export function stageIndex(state: JobState): number {
  if (state === "done") return STAGES.length;
  if (state === "failed" || state === "cancelled") return -1;
  return STAGES.indexOf(state);
}
```

Tests to write first (they must fail before the implementation exists):

```ts
describe("stageIndex", () => {
  it("orders the happy path", () => {
    expect(stageIndex("queued")).toBe(0);
    expect(stageIndex("transcribing")).toBe(3);
    expect(stageIndex("rendering")).toBe(6);
  });
  it("puts done past the last stage", () => {
    expect(stageIndex("done")).toBe(STAGES.length);
  });
  it("returns -1 for states off the happy path", () => {
    expect(stageIndex("failed")).toBe(-1);
    expect(stageIndex("cancelled")).toBe(-1);
  });
  it("covers every stage exactly once", () => {
    expect(new Set(STAGES).size).toBe(STAGES.length);
  });
});
```

Verify: `npm test` green (RED first, then GREEN). Commit `feat(frontend): stageIndex helper for the pipeline timeline`.

---

## Task 3: Shared primitives — HarvestStrip, StageTimeline, Thumb

**Files:** create `qatf-frontend/src/components/HarvestStrip.tsx`, `StageTimeline.tsx`, `Thumb.tsx`. **Depends on Task 2's `STAGES`/`stageIndex` and Task 1's classes.**

`HarvestStrip` — the signature element:

```tsx
interface Props { clips: ClipModel[]; mini?: boolean }
```

- Returns `null` for an empty list.
- Extent is `Math.max(...clips.map(c => c.end))`; each span is positioned `left: start/extent*100%`, `width: max((end-start)/extent*100%, 1.5%)` (the floor keeps a short clip visible).
- Non-mini renders `.strip-scale` with `formatSeconds(0)` and `formatSeconds(extent)`.
- Each span gets `title={`${index+1}. ${clip.title} — ${formatSeconds(clip.start)}–${formatSeconds(clip.end)}`}`.
- The label under a full-size strip must read as what it is: spans within the picked range, not "the whole video".

`StageTimeline` — `{ state: JobState }`: renders the seven `STAGES` as `.timeline-step`s, marking `is-done` for index < current, `is-current` for equality; renders nothing (return `null`) when `stageIndex(state) === -1`, since failed/cancelled jobs have no meaningful position — the page shows the error instead.

`Thumb` — `{ outputs: ClipOutput[]; state: JobState }`: a 9:16 tile. With outputs, `<video src={clipUrl(outputs[0])} preload="metadata" muted playsInline />`; without, a `.job-thumb-glyph` showing a single character that reflects the state (e.g. `▣` planned, `⋯` running, `✕` failed, `⊘` cancelled) in the state color. No autoplay, no controls.

Verify: `npm run build`. Commit `feat(frontend): harvest strip, stage timeline and clip thumbnail`.

---

## Task 4: Dashboard

**Files:** rewrite `qatf-frontend/src/pages/JobsList.tsx`.

- Replace the `<select>` with `.chips`: "All" plus one chip per state **that has at least one job**, each showing `.chip-count`. Counts come from an unfiltered list held alongside the filtered one — poll `listJobs()` unfiltered every 3s and filter client-side, so counts stay honest and only one request per tick is made. (This replaces the server-side `?state=` filter; that is a deliberate simplification, not an oversight.)
- Rows become `.job-row` cards: `<Thumb>`, then `.job-main` with `.job-id` (mono, links to the job), a `.state` badge, `.job-meta` (clip count, language, device, word count — only the fields present), `.job-msg` (truncated, `title` for full text), and a `.strip is-mini` when the job has clips. `.job-actions` keeps cancel (hidden for `planned` and terminal states — the API refuses those) and delete (disabled while running).
- Keep the existing polling, the connection-lost banner, and the HealthBanner.
- Add `.empty` state: "No jobs yet" + a line explaining what a job does + a primary link to `/new`.
- Add `.skeleton-row` x3 while the first fetch is in flight (`jobs === null`).

Verify: `npm test && npm run build`. Commit `feat(frontend): dashboard with clip thumbnails, filter chips and harvest strips`.

---

## Task 5: New-job page

**Files:** rewrite `qatf-frontend/src/pages/NewJob.tsx`, restyle `qatf-frontend/src/components/OptionsForm.tsx`.

- Upload tab gets a `.dropzone`: click opens the file picker, drag-over sets `.is-dragging`, drop takes `e.dataTransfer.files[0]`. Selected file shows as a `.file-pill` with name and `formatBytes(size)` and a way to clear it. Keep the existing XHR `.progress` bar.
- `OptionsForm` fieldsets become `.fieldset` with `.fieldset-title`; all 22 JobOptions fields stay, same names, same validation wiring. Checkbox rows use `.switch`. Add a per-row delete to the fixups editor (a ledgered v1 gap: rows could only be added or wiped).
- Add a `.sticky-bar`: left `.sticky-bar-status` reads "Ready to start" or "N field(s) need attention"; right `.sticky-bar-actions` holds the primary button ("Start job", "Starting…" while busy).
- Keep `HealthBanner`, the three tabs, and all existing submit/error behavior.

Verify: `npm test && npm run build`. Commit `feat(frontend): dropzone upload, carded options and a sticky start bar`.

---

## Task 6: Job detail + clips + the transcript page

**Files:** rewrite `qatf-frontend/src/pages/JobDetail.tsx` and `qatf-frontend/src/components/ClipGrid.tsx`; create `qatf-frontend/src/pages/TranscriptPage.tsx`; edit `qatf-frontend/src/components/TranscriptEditor.tsx` and `qatf-frontend/src/App.tsx`.

**Route:** add `<Route path="/jobs/:id/transcript" element={<TranscriptPage />} />` to `App.tsx`.

**JobDetail:**
- `.page-head`: back link to jobs, the id in mono as `.page-title`, the `.state` badge, and the render/cancel actions.
- `<StageTimeline state={job.state} />` under the head; the error (if any) in a `.banner-error` when the state is `failed`.
- A `.dl` definition grid for meta: source (with the URL for youtube jobs), device actually used, language, word count, transcript cached, created/updated.
- **Clips first** when `outputs.length > 0` — heading with the count, then `<ClipGrid>`. Below it, the full-size `<HarvestStrip clips={job.clips} />` when a plan exists.
- Transcript becomes a summary `.card`: word count and language from the job record, plus a "Edit transcript" `.btn` linking to `/jobs/:id/transcript`. Shown only when `job.word_count > 0`. **Remove the inline `<TranscriptEditor>` mount.**
- Plan editor stays mounted where it is.

**ClipGrid:** `.clips` grid of `.clip` cards — `.clip-video` (`controls`, `preload="metadata"`), `.clip-name` (mono, truncated), `.clip-meta` with `formatBytes(size)`, a download link, and the clip's duration when `clips[i]` exists at the same index (pass `clips: ClipModel[]` as an optional prop from JobDetail; render duration only when the index resolves — never guess).

**TranscriptPage:** reads `:id` from the route, fetches the job (`getJob`) for the header context, renders a `.page-head` with a back link to `/jobs/:id`, and mounts `<TranscriptEditor jobId job />`.

**TranscriptEditor changes (behavior preserved exactly):** it now loads its transcript automatically on mount (it is the page's whole purpose — no "show N words" button), renders `.transcript-head` with `.transcript-stat` items (words, language + probability, timing source, corrections applied, stale count), and moves its save/discard controls into a `.sticky-bar` showing the pending-correction count. The guard, the no-timing-inputs rule, the running-state gate, and the `saving` click-guard all stay exactly as they are.

Verify: `npm test && npm run build`. Commit `feat(frontend): clips-first job page and a dedicated transcript editor page`.

---

## Task 7: Plan editor polish

**Files:** `qatf-frontend/src/components/PlanEditor.tsx`.

- Restyle to `.plan` / `.plan-num` / `.plan-warn`; move save/reset/add into a `.sticky-bar`.
- Fix the ledgered v1 minor: clear the "snapped boundaries" notice on the next edit — set `snapped` to `false` inside `update`, `move`, `remove`, and `add`, so the notice can never describe stale numbers.
- Everything else unchanged: `putPlan` still always re-snaps, the response still replaces the draft, `durationWarning` still drives `.plan-warn`, editability still limited to `planned`/`done`.

Verify: `npm test && npm run build`. Commit `feat(frontend): plan editor sticky actions and honest snap notice`.

---

## Task 8: Verification

1. `cd qatf-frontend && npm test && npm run build` — all green.
2. `docker compose build frontend && docker compose up -d`.
3. Screenshot at 1280px and 420px: dashboard, new job, job detail, transcript page. Confirm no horizontal overflow, the harvest strip renders, Arabic renders RTL in the correct face, focus rings are visible.
4. `docker compose down` when finished.

Commit any fixes the screenshots reveal.
