import type { ClipModel } from "../api/types";
import { formatSeconds } from "../lib/format";

interface Props {
  clips: ClipModel[];
  mini?: boolean;
}

/** A short span would otherwise render sub-pixel and vanish. */
const MIN_WIDTH_PCT = 1.5;

/** Clamp to the track and never let a non-finite timing reach the DOM as
 * `NaN%` — the API's numbers are checked at the boundary, not trusted here. */
function pct(value: number): number {
  if (!Number.isFinite(value)) return 0;
  return Math.min(100, Math.max(0, value));
}

/**
 * The signature element: where in the source the good parts were picked.
 *
 * HONEST SCALE. The API reports no source-video duration, so the track cannot
 * represent the whole video. It spans `0 → max(clip.end)` and both ends carry a
 * timecode, so the reader can see exactly what the scale is. Nothing here may
 * imply a total duration we do not have — the right-hand label is the end of the
 * last pick, and says so.
 */
export function HarvestStrip({ clips, mini = false }: Props) {
  if (clips.length === 0) return null;

  const ends = clips.map((c) => c.end).filter((e) => Number.isFinite(e));
  const extent = ends.length > 0 ? Math.max(...ends) : 0;
  if (extent <= 0) return null;

  const label = `${clips.length} clip${clips.length === 1 ? "" : "s"} picked`;

  return (
    <div
      className={mini ? "strip is-mini" : "strip"}
      role="img"
      aria-label={`${label}, between ${formatSeconds(0)} and ${formatSeconds(extent)}`}
    >
      <div className="strip-track">
        {clips.map((clip, index) => {
          // The geometry is clamped HERE, not by `overflow: hidden` on the
          // track: clipping the track also clipped every span's outer ring, so
          // the one detail that separates two touching picks never rendered.
          // The floor can push a very late, very short span past the right
          // edge, so the span slides back to sit flush instead of overhanging.
          const width = Math.min(
            100, Math.max(MIN_WIDTH_PCT, pct(((clip.end - clip.start) / extent) * 100)));
          const left = Math.min(pct((clip.start / extent) * 100), 100 - width);
          return (
            <div
              key={`${index}-${clip.start}-${clip.end}`}
              className="strip-span"
              style={{ left: `${left.toFixed(3)}%`, width: `${width.toFixed(3)}%` }}
              title={
                `${index + 1}. ${clip.title} — ` +
                `${formatSeconds(clip.start)}–${formatSeconds(clip.end)}`
              }
            />
          );
        })}
      </div>
      {!mini && (
        <div className="strip-scale">
          <span>{formatSeconds(0)}</span>
          <span className="muted">{label}</span>
          <span title="End of the last clip. The source video may run longer — the API does not report its duration.">
            {formatSeconds(extent)}
          </span>
        </div>
      )}
    </div>
  );
}
