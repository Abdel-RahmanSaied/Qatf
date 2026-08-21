import type { FetchProgressModel } from "../api/types";
import { describeFetch } from "../lib/format";

interface Props {
  progress: FetchProgressModel | null;
}

/** Stage 0's download, while it runs.
 *
 * `fetching` is the one stage whose duration depends on somebody else's
 * network, which is why it has its own job state at all — and until now the
 * only thing it could say was "[0/5] fetching the video" for however many
 * minutes a multi-GB source takes. This is the number behind that sentence.
 *
 * Renders nothing at all when `progress` is null. That is a job which never
 * fetched — an upload or a server path — and it is a different fact from a job
 * sitting at zero bytes. Only a URL job can be the second, so drawing an empty
 * bar for the first would be inventing a download that does not exist.
 *
 * When the size is unknown the bar is likewise omitted and only the byte count
 * shows, for the reason `describeFetch` returns a null percent: a bar needs a
 * denominator, and there isn't one to be had.
 */
export function FetchProgress({ progress }: Props) {
  if (!progress) return null;
  const { percent, text } = describeFetch(progress);
  return (
    <div className="fetch-progress">
      {percent !== null && (
        <div
          className="progress"
          role="progressbar"
          aria-label="Downloading the source video"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
        >
          <div style={{ width: `${percent}%` }} />
        </div>
      )}
      <span className="field-help mono">
        {percent !== null ? `${percent}% · ${text}` : text}
      </span>
    </div>
  );
}
