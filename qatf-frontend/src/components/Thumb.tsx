import { clipUrl } from "../api/client";
import type { ClipOutput, JobState } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";
import { Icon } from "./Icon";
import type { IconName } from "./Icon";
import { TONE } from "./StateBadge";

/** One icon per state, for a job with nothing rendered yet. */
const GLYPH: Record<JobState, IconName> = {
  queued: "clock",
  fetching: "download",
  extracting: "audio",
  transcribing: "text",
  selecting: "scissors",
  planned: "film",
  rendering: "film",
  done: "check",
  failed: "x",
  cancelled: "ban",
};

/**
 * A 9:16 tile for a job: the first rendered clip if there is one, otherwise an
 * icon in the state's colour. Deliberately no autoplay and no controls — this
 * is a still identity for the row, not a player. The `.state--*` modifiers set
 * `color` only, which is all the icon needs.
 */
export function Thumb({ outputs, state }: { outputs: ClipOutput[]; state: JobState }) {
  const { t } = useI18n();
  if (outputs.length > 0) {
    return (
      <div className="job-thumb">
        <video src={clipUrl(outputs[0])} preload="metadata" muted playsInline />
      </div>
    );
  }
  return (
    <div className="job-thumb">
      <span className={`job-thumb-glyph ${TONE[state]}`}>
        <Icon name={GLYPH[state]} size={22} label={t.state[state]} />
      </span>
    </div>
  );
}
