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
