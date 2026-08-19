import { clipUrl } from "../api/client";
import type { ClipModel, ClipOutput } from "../api/types";
import { formatBytes, formatSeconds } from "../lib/format";

interface Props {
  outputs: ClipOutput[];
  /** The plan the outputs were rendered from, if the caller has it. Used ONLY
   * to label a clip's duration, and only when the index resolves — a render
   * that ran before the current plan must never be labelled with its numbers. */
  clips?: ClipModel[];
}

/** The rendered clips — the thing the whole pipeline exists to produce, so they
 * lead the job page. `preload="metadata"` fetches a poster frame and the
 * duration without pulling the file. */
export function ClipGrid({ outputs, clips }: Props) {
  if (outputs.length === 0) return null;
  return (
    <div className="clips">
      {outputs.map((output, index) => {
        const clip = clips?.[index];
        return (
          <div key={output.name} className="clip">
            <video
              className="clip-video"
              src={clipUrl(output)}
              controls
              preload="metadata"
              playsInline
            />
            <div className="clip-name" title={clip ? `${output.name} — ${clip.title}` : output.name}>
              {output.name}
            </div>
            <div className="clip-meta">
              <span>{formatBytes(output.size_bytes)}</span>
              {clip && (
                <span title={`${formatSeconds(clip.start)}–${formatSeconds(clip.end)} in the source`}>
                  {formatSeconds(clip.end - clip.start)}
                </span>
              )}
              <a href={clipUrl(output)} download={output.name}>Download</a>
            </div>
          </div>
        );
      })}
    </div>
  );
}
