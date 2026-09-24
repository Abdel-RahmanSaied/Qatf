import type { JobState } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";

/** The design system carries one badge shape — `.state` plus a `.state-dot`
 * coloured by modifier. Every job state maps onto one of the five modifiers;
 * the seven working states all read as "running". */
export const TONE: Record<JobState, string> = {
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

/** Shows what the state MEANS ("Turning speech into text"), not the API's
 * word for it ("transcribing"). */
export function StateBadge({ state }: { state: JobState }) {
  const { t } = useI18n();
  return (
    <span className={`state ${TONE[state]}`}>
      <span className="state-dot" aria-hidden="true" />
      {t.state[state]}
    </span>
  );
}
