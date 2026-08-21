import { useRef, useState } from "react";
import { ApiError, clipUrl, downloadClip } from "../api/client";
import type { ClipOutput } from "../api/types";
import { formatBytes } from "../lib/format";

interface Props {
  output: ClipOutput;
}

/** Hand `blob` to the browser's downloader under `name`.
 *
 * The object URL is revoked on the next macrotask, NOT synchronously after
 * `.click()` — the click only schedules the save, so revoking in the same tick
 * races it and yields a zero-byte or failed download in Chromium.
 */
function save(blob: Blob, name: string): void {
  const href = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = href;
  anchor.download = name;
  // Appended rather than clicked detached: Chromium and Firefox honour a
  // detached anchor, Safari has historically ignored it, and the append costs
  // nothing next to the transfer that just finished.
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(href), 0);
}

/** The Download control for one rendered clip, metered while it transfers.
 *
 * This replaces a plain `<a download>`, which trades the browser's own download
 * manager (and with it resume, and surviving a navigation) for in-page
 * progress. That is the deliberate bargain: a clip is bounded by `--max-len`,
 * so the transfer is short and the page is where the user is already looking.
 *
 * When the size is unknown the bar is not drawn at all and only a byte counter
 * appears. A progress bar with a guessed denominator would be the UI lying
 * about the one thing it exists to report — the same rule that stops the
 * harvest strip assuming a source duration.
 */
export function ClipDownload({ output }: Props) {
  const [loaded, setLoaded] = useState<number | null>(null);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Guards against a second click while the first transfer is in flight. A ref,
  // not the `loaded` state: state updates are batched, so two clicks in one
  // tick would both read the stale idle value and start two downloads.
  const busy = useRef(false);

  async function start(event: React.MouseEvent) {
    // Leave modified clicks alone — ctrl/cmd/shift and middle-click mean "open
    // or save this yourself", and the href is a real, working clip URL.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return;
    event.preventDefault();
    if (busy.current) return;
    busy.current = true;
    setError(null);
    setLoaded(0);
    setTotal(output.size_bytes > 0 ? output.size_bytes : null);
    try {
      const blob = await downloadClip(output, (bytes, size) => {
        setLoaded(bytes);
        setTotal(size);
      });
      save(blob, output.name);
    } catch (exc) {
      setError(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      busy.current = false;
      setLoaded(null);
    }
  }

  const running = loaded !== null;
  // A percentage exists only with BOTH a byte count and a real denominator.
  // Null here is the honest answer, not a zero to render.
  const percent = loaded !== null && total !== null
    ? Math.round((loaded / total) * 100)
    : null;

  return (
    <>
      {/* The href stays the real clip URL: right-click "Save link as", a
          middle-click, and a page where the JS failed all still download it.
          The handler is an enhancement over that, never a replacement for it. */}
      <a
        href={clipUrl(output)}
        download={output.name}
        onClick={start}
        aria-disabled={running}
      >
        {running ? "Downloading…" : "Download"}
      </a>
      {running && (
        <div className="clip-download">
          {percent !== null && (
            <div
              className="progress progress-slim"
              role="progressbar"
              aria-label={`Downloading ${output.name}`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
            >
              <div style={{ width: `${percent}%` }} />
            </div>
          )}
          <span className="field-help mono">
            {total !== null
              ? `${percent}% · ${formatBytes(loaded)} of ${formatBytes(total)}`
              : `${formatBytes(loaded)} downloaded`}
          </span>
        </div>
      )}
      {error && <span className="clip-download-error">{error}</span>}
    </>
  );
}
