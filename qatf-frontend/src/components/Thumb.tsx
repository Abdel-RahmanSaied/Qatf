import { clipUrl } from "../api/client";
import type { ClipOutput, JobState } from "../api/types";

/** One character per state, for a job with nothing rendered yet. */
const GLYPH: Record<JobState, string> = {
  queued: "⋯",
  fetching: "⋯",
  extracting: "⋯",
  transcribing: "⋯",
  selecting: "⋯",
  planned: "▣",
  rendering: "⋯",
  done: "▣",
  failed: "✕",
  cancelled: "⊘",
};

/** The state colours live in the design system; `.state--*` sets `color` only,
 * which is all the glyph needs — no `.state` wrapper, no compound selector. */
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

/**
 * A 9:16 tile for a job: the first rendered clip if there is one, otherwise a
 * glyph in the state's colour. Deliberately no autoplay and no controls — this
 * is a still identity for the row, not a player.
 */
export function Thumb({ outputs, state }: { outputs: ClipOutput[]; state: JobState }) {
  if (outputs.length > 0) {
    return (
      <div className="job-thumb">
        <video src={clipUrl(outputs[0])} preload="metadata" muted playsInline />
      </div>
    );
  }
  return (
    <div className="job-thumb">
      <span className={`job-thumb-glyph ${TONE[state]}`} aria-label={state} role="img">
        {GLYPH[state]}
      </span>
    </div>
  );
}
