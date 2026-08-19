import type { JobState } from "../api/types";

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
