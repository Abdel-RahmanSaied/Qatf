import type { FetchProgressModel, JobState } from "../api/types";

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

/** Stage 0 download progress as a percent and a byte line.
 *
 * RECONSTRUCTED — the original was lost and this is rebuilt from the contract
 * `FetchProgress.tsx` relies on. Check it against what you meant.
 *
 * `percent` is null when `total_bytes` is null: a bar needs a denominator and
 * there is not one to be had, so the caller omits the bar rather than drawing a
 * guess. When the total IS known the percent is clamped to 100 but the byte
 * counts are left alone, because yt-dlp's total is an ESTIMATE it can overshoot
 * — the bytes are measured and the total may not be, so the honest thing is a
 * full bar next to numbers that read past it.
 *
 * `file_index` is surfaced because a merged DASH fetch downloads the video
 * stream and then the audio stream, restarting `downloaded_bytes` at zero. A
 * counter that visibly resets with nothing to explain it reads as a bug. */
export function describeFetch(
  p: FetchProgressModel,
): { percent: number | null; text: string } {
  const done = formatBytes(p.downloaded_bytes);
  const part = p.file_index > 0 ? ` · file ${p.file_index + 1}` : "";
  if (p.total_bytes === null || p.total_bytes <= 0) {
    return { percent: null, text: `${done}${part}` };
  }
  const pct = Math.min(100, Math.round((p.downloaded_bytes / p.total_bytes) * 100));
  return { percent: pct, text: `${done} / ${formatBytes(p.total_bytes)}${part}` };
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

/** Count how a plan sits against the range that was asked for.
 *
 * Counts the SERVER'S labels rather than re-deriving them from durations. The
 * server applies DURATION_SLACK before flagging, so a client-side
 * `duration < min_len` would mark a 29.5s clip that the server considers fine —
 * a mirror that is stricter than the server, which is the one direction a
 * mirror must never be wrong in. */
export function summariseRange(
  clips: readonly { out_of_range?: "short" | "long" | null }[],
): { short: number; long: number } {
  let short = 0;
  let long = 0;
  for (const c of clips) {
    if (c.out_of_range === "short") short += 1;
    else if (c.out_of_range === "long") long += 1;
  }
  return { short, long };
}
