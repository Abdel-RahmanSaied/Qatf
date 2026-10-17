import type { RenderProgressModel } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";
import { describeRender } from "../lib/format";

interface Props {
  progress: RenderProgressModel | null | undefined;
}

/** `m:ss` for whole seconds of video — rounded first, so 59.6s is 1:00. */
function clock(seconds: number): string {
  const s = Math.round(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** Stage 5's encode, while it runs — the twin of FetchProgress.
 *
 * "[5/5] rendering 8 clips" said nothing for the minutes a 4K clip takes to
 * encode. This is ffmpeg's own reading of how much video it has written,
 * against the plan's clip lengths, so the bar has a real denominator.
 *
 * Renders nothing when `progress` is null or absent: a job that never
 * rendered, or a server older than the field. Drawing an empty bar for that
 * would invent a render that is not happening. What is left is shown as video
 * still to make, never as a time estimate — encode speed depends on codec,
 * preset and resolution, and a guessed countdown is worse than none. */
export function RenderProgress({ progress }: Props) {
  const { t } = useI18n();
  if (!progress) return null;
  const { percent, clip, count, left } = describeRender(progress);
  return (
    <div className="fetch-progress">
      {percent !== null && (
        <div
          className="progress"
          role="progressbar"
          aria-label={t.job.renderAria}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div style={{ width: `${percent}%` }} />
        </div>
      )}
      {/* Not `mono`: unlike FetchProgress's byte counter this line is words,
          and the mono face's spacing pulls Arabic letters apart. */}
      <span className="field-help">
        {t.job.renderClip(clip, count)}
        {percent !== null && ` · ${percent}%`}
        {left > 0 && ` · ${t.job.renderLeft(clock(left))}`}
      </span>
    </div>
  );
}
