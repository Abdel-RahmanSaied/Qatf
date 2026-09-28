import type { JobResponse, JobState } from "../api/types";
import { STAGES, stageIndex } from "../lib/format";
import { useI18n } from "../i18n/I18nProvider";
import { Icon } from "./Icon";
import "./StageTimeline.css";

/**
 * The seven pipeline stages, with the job's position marked.
 *
 * Position comes from `stageIndex(state)` — the state alone, never the progress
 * message, which is free text. A failed or cancelled job has no meaningful
 * position on the happy path, so this renders nothing and the page shows the
 * error instead.
 */
export function StageTimeline(
  { state, source }: { state: JobState; source: JobResponse["source"] },
) {
  const { t } = useI18n();
  const current = stageIndex(state);
  if (current === -1) return null;

  // `fetching` runs only for a job that had something to download. On an
  // upload or a server path it is skipped, and drawing it as completed would
  // claim a stage that never ran — the timeline would be lying about the one
  // thing it exists to report. It keeps its slot rather than being dropped so
  // the seven positions stay put between jobs.
  const skipped = (stage: string) => stage === "fetching" && source !== "youtube";

  return (
    <ol className="timeline" aria-label={t.stage.aria}>
      {STAGES.map((stage, index) => {
        const step = skipped(stage)
          ? "timeline-step is-skipped"
          : index < current
            ? "timeline-step is-done"
            : index === current
              ? "timeline-step is-current"
              : "timeline-step";
        return (
          // `title` carries the stage name under 640px, where the CSS hides
          // the label text entirely.
          <li
            key={stage}
            className={step}
            title={skipped(stage) ? t.stage.skipped : t.state[stage]}
            aria-current={index === current ? "step" : undefined}
          >
            <span className="timeline-dot" aria-hidden="true">
              {index < current && !skipped(stage) && <Icon name="check" size={10} />}
            </span>
            <span className="timeline-label">{t.stage[stage as keyof typeof t.stage]}</span>
          </li>
        );
      })}
    </ol>
  );
}
