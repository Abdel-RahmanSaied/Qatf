import { clipUrl } from "../api/client";
import type { ClipOutput } from "../api/types";
import { formatBytes } from "../lib/format";

export function ClipGrid({ outputs }: { outputs: ClipOutput[] }) {
  if (outputs.length === 0) return null;
  return (
    <div className="clips-grid">
      {outputs.map((clip) => (
        <div key={clip.name} className="clip-card">
          <video src={clipUrl(clip)} controls preload="metadata" />
          <div className="mono">{clip.name}</div>
          <div className="muted">
            {formatBytes(clip.size_bytes)} ·{" "}
            <a href={clipUrl(clip)} download={clip.name}>download</a>
          </div>
        </div>
      ))}
    </div>
  );
}
