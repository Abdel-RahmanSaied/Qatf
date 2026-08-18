# qatf web UI enhancement — design

Date: 2026-08-19
Status: approved in chat (design + two user additions), pending spec review

## Goal

Take the functional v1 SPA (built on the `web-ui` branch) to a distinctive,
polished product surface: a confident-editorial dark visual identity, UX
affordances, a richer dashboard, and — per direct user request — a dedicated
transcript-editing page and more prominent clips.

## Decisions (made with the user)

| Decision | Choice |
| --- | --- |
| Scope | Visual identity + UX affordances + dashboard upgrade |
| Theme | Dark only, polished |
| Boldness | Confident editorial (strong type, sparse accent, Arabic-forward brand) |
| Approach | Design-token CSS overhaul in place — **no new npm dependencies** |
| User addition 1 | Transcript editor becomes its own page `/jobs/:id/transcript` |
| User addition 2 | Clips made prominent (dashboard thumbnails, polished clip cards) |

Rejected: Tailwind/component library (violates the repo's dependency budget for
no structural gain); cosmetic-only tweaks (doesn't meet the identity ask).

## 1. Visual identity

- **Token rewrite** at the top of `styles.css`: warm near-black ground
  (`#0c0e12` family), two elevation steps (`--surface-1`, `--surface-2`), one
  harvest-saffron accent reserved for primary actions and the brand, 3-level
  text hierarchy, 10px card radius, visible `:focus-visible` rings,
  `prefers-reduced-motion` respected on every animation.
- **Typography**: Inter (UI) + IBM Plex Sans Arabic (Arabic content and the
  قطف brand mark), loaded via a Google Fonts `<link>` in `index.html`, with
  full system fallback stacks so the UI degrades gracefully offline.
  `font-variant-numeric: tabular-nums` on all timings and sizes.
- **Brand header**: قطف as the primary mark, "qatf" as the quiet Latin
  companion; nav simplified to Jobs / New job.

## 2. Dashboard upgrade (`JobsList`)

- **Filter chips** replace the `<select>`: All + one chip per state, each with
  a live count from the unfiltered list; active chip highlighted.
- **Refined rows**: state badge redesigned (colored dot + label), a
  **7-segment stage indicator** driven only by `job.state` via a new pure
  `stageIndex(state)` helper in `lib/format.ts` (with vitest coverage — no
  message-string parsing), truncated message with `title` hover, icon-style
  action buttons.
- **Thumbnails with no backend change**: jobs with outputs render a small 9:16
  `<video preload="metadata">` tile of the first clip (the browser fetches the
  poster frame itself through the existing `/api` download route); other jobs
  get a state-glyph placeholder tile.
- **Empty state** (headline + CTA to `/new`) and **skeleton rows** while the
  first fetch is in flight.

## 3. New-job page

- **Upload dropzone**: drag-and-drop + click-to-browse, showing the chosen
  file's name and size; the existing XHR progress bar restyled.
- Options fieldsets become titled cards with clearer group headers.
- **Sticky submit bar**: always-visible bottom bar with the Start button and a
  live validation summary (error count, or "ready").

## 4. Job detail + the transcript page (user addition)

- **Header card**: prominent state, a **stage timeline** of the seven pipeline
  states with the current one lit (reuses `stageIndex`), meta as a definition
  grid (source, device, words, cached, timestamps).
- **Clips section**: cards get duration (best-effort: from the plan clip at
  the same index, shown only when plan and outputs align), hover affordance,
  restyled download action; section moves above
  the plan editor when outputs exist — clips are the product.
- **Transcript moves to its own page** `/jobs/:id/transcript`:
  - New route + page component; `TranscriptEditor` becomes the page's body
    (same load-on-demand data flow, same guard, same save contract — the
    component's logic does not change, only its home).
  - The job page replaces the inline editor with a summary card built from
    fields the job record actually carries (word count, language, cached) and
    an "Edit transcript" link — corrections counts appear on the editor page
    itself once the transcript loads, because only `GET /transcript` has them.
  - The editor page gets a **sticky save bar** (corrections count, save,
    discard) and a back link to the job.
  - Arabic words render in IBM Plex Sans Arabic at a comfortable reading size.
- **Plan editor**: sticky save bar; the stale "snapped" banner clears on the
  next edit (fixes a ledgered v1 minor).

## 5. What does NOT change

No new npm dependencies. No backend, nginx, or compose changes. No changes to
`api/client.ts` contracts or the rule mirrors in `lib/rules.ts`. All existing
vitest tests keep passing; the only new logic is `stageIndex` (+ test) and the
plan-editor banner-clear (behavioral fix noted above).

## 6. Verification

- `npm test && npm run build` stays the per-change gate.
- Rebuild the frontend image, `docker compose up`, and **screenshot the four
  pages** (dashboard, new job, job detail, transcript page) via the browser
  tooling against the live stack — layout claims verified by looking, per the
  repo's own working agreement. RTL transcript rendering checked with Arabic
  words specifically.

## Out of scope

Light theme, i18n of UI chrome, drag-to-reorder plan clips (buttons remain),
per-word transcript timing display beyond the existing tooltip, auth.
