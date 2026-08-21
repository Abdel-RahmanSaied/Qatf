import { summariseRange } from "../lib/format";
import type { ClipModel, JobOptions } from "../api/types";

/** Which clips in this plan missed the length that was asked for.
 *
 * Every clip listed here IS in the plan and WILL be rendered. This used to be
 * a list of clips the server had thrown away for missing the range, and the
 * throwing away is what turned out to be wrong: on the run that settled it,
 * six of eight picks were 24-second shorts against a min_len of 30 — publishable
 * clips, deleted unseen for missing a number by four seconds.
 *
 * So the notice reports rather than apologises. It exists so a short clip is
 * something the operator chose to keep, not something they discover on upload. */
export function RangeNotice(
  { clips, options }: { clips: ClipModel[]; options: JobOptions },
) {
  const flagged = clips.filter((c) => c.out_of_range);
  if (flagged.length === 0) return null;
  const { short, long } = summariseRange(clips);
  const parts = [
    short > 0 ? `${short} under ${options.min_len}s` : null,
    long > 0 ? `${long} over ${options.max_len}s` : null,
  ].filter(Boolean);

  return (
    <div className="banner banner-warn dropped">
      <p className="dropped-head">
        <span className="tnum">{flagged.length}</span> of{" "}
        <span className="tnum">{clips.length}</span> clips fall outside the{" "}
        <span className="tnum">{options.min_len}–{options.max_len}s</span> you
        asked for ({parts.join(", ")}). They are kept and rendered — check them
        before publishing.
      </p>
      <ul className="dropped-list">
        {flagged.map((c, i) => (
          <li className="dropped-item" key={`${i}-${c.title}`}>
            <span className="tnum dropped-dur">{(c.end - c.start).toFixed(1)}s</span>
            <span className={`range-tag range-tag--${c.out_of_range}`}>
              {c.out_of_range === "short" ? "SHORT" : "LONG"}
            </span>
            <span className="dropped-title">{c.title}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
