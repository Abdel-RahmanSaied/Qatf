import { useRef, useState } from "react";
import { ApiError, downloadClip } from "../api/client";
import type { ClipOutput } from "../api/types";
import { useI18n } from "../i18n/I18nProvider";
import { describeBatch, formatBytes } from "../lib/format";
import { saveBlob } from "./ClipDownload";
import { Icon } from "./Icon";
import "./DownloadAll.css";

interface Props {
  outputs: ClipOutput[];
}

interface Running {
  index: number;      // the clip now transferring, 0-based
  loaded: number;     // its bytes so far
}

/** Download every rendered clip, one after another, with one progress line.
 *
 * Client-side on purpose: the web UI may not need a backend change (CLAUDE.md,
 * "The web UI"), so there is no zip endpoint — each clip goes through the same
 * metered `downloadClip` the per-clip link uses, and is handed to the browser
 * as soon as it lands. One at a time also keeps memory to one clip: a 4K clip
 * runs past 100 MB, and holding eight of them to zip in the page would not.
 *
 * Browsers ask once before letting a page save several files; the help line
 * says so before the first click rather than after the second file is blocked.
 * A failed clip does not stop the rest — it is named at the end. Stop aborts
 * the transfer in flight; clips already saved stay saved. */
export function DownloadAll({ outputs }: Props) {
  const { t } = useI18n();
  const tj = t.job;
  const [running, setRunning] = useState<Running | null>(null);
  const [result, setResult] = useState<{ saved: number; failed: string[]; stopped: boolean } | null>(null);
  // A ref, not state: two clicks in one tick would both read a stale idle
  // `running` and start two batches. Same reason ClipDownload uses one.
  const busy = useRef(false);
  const abort = useRef<AbortController | null>(null);

  const sizes = outputs.map((o) => o.size_bytes);
  const allKnown = sizes.length > 0 && sizes.every((s) => s > 0);
  const totalSize = allKnown ? sizes.reduce((a, b) => a + b, 0) : null;

  async function start() {
    if (busy.current) return;
    busy.current = true;
    setResult(null);
    const failed: string[] = [];
    let saved = 0;
    let stopped = false;
    // A snapshot: a render still in progress may add clips while this runs,
    // and the batch the button promised is the one that was on screen.
    const batch = [...outputs];
    for (let i = 0; i < batch.length; i++) {
      const controller = new AbortController();
      abort.current = controller;
      setRunning({ index: i, loaded: 0 });
      try {
        const blob = await downloadClip(
          batch[i],
          (loaded) => setRunning({ index: i, loaded }),
          controller.signal,
        );
        saveBlob(blob, batch[i].name);
        saved += 1;
      } catch (exc) {
        if (exc instanceof DOMException && exc.name === "AbortError") {
          stopped = true;
          break;
        }
        failed.push(`${batch[i].name} (${exc instanceof ApiError ? exc.message : String(exc)})`);
      }
    }
    abort.current = null;
    busy.current = false;
    setRunning(null);
    setResult({ saved, failed, stopped });
  }

  if (outputs.length < 2) return null;     // one clip has its own link

  const progress = running ? describeBatch(sizes, running.index, running.loaded) : null;

  return (
    <div className="download-all">
      {running ? (
        <button type="button" className="btn btn-ghost btn-sm" onClick={() => abort.current?.abort()}>
          <Icon name="x" size={14} />
          {t.common.stop}
        </button>
      ) : (
        <button type="button" className="btn btn-sm" onClick={() => void start()}>
          <Icon name="download" size={14} />
          {tj.downloadAll(outputs.length, totalSize !== null ? formatBytes(totalSize) : null)}
        </button>
      )}
      {running && progress && (
        <div className="download-all-progress" role="status">
          {progress.percent !== null && (
            <div
              className="progress progress-slim"
              role="progressbar"
              aria-label={tj.downloadAllAria}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={progress.percent}
            >
              <div style={{ width: `${progress.percent}%` }} />
            </div>
          )}
          <span className="field-help mono">
            {tj.downloadAllClip(running.index + 1, outputs.length)}
            {" · "}
            {progress.total !== null
              ? `${progress.percent}% · ${t.common.of(formatBytes(progress.done), formatBytes(progress.total))}`
              : t.common.downloaded(formatBytes(progress.done))}
          </span>
        </div>
      )}
      {!running && !result && <span className="field-help">{tj.downloadAllHint}</span>}
      {result && (
        <span className={result.failed.length ? "download-all-error" : "field-help"} role="status">
          {result.stopped ? tj.downloadAllStopped(result.saved) : tj.downloadAllDone(result.saved)}
          {result.failed.length > 0 && <> {tj.downloadAllFailed(result.failed.join(", "))}</>}
        </span>
      )}
    </div>
  );
}
