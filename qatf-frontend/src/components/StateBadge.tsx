import type { JobState } from "../api/types";

/** The design system carries one badge shape — `.state` plus a `.state-dot`
 * coloured by modifier. Every job state maps onto one of the five modifiers;
 * the seven working states all read as "running". */
const TONE: Record<JobState, string> = {
  queued: "state--running",
  fetching: "state--running",
  extracting: "state--running",
  transcribing: "state--running",
  selecting: "state--running",
  planned: "state--planned",
  rendering: "state--running",
  done: "state--done",
  failed: "state--failed",
  cancelled: "state--cancelled",
};

export function StateBadge({ state }: { state: JobState }) {
  return (
    <span className={`state ${TONE[state]}`}>
      <span className="state-dot" aria-hidden="true" />
      {state}
    </span>
  );
}
