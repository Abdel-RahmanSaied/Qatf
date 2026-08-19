import { useCallback, useRef, useState } from "react";
import type { DragEvent, KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, createJobFromPath, createJobFromUrl, health, uploadJob,
} from "../api/client";
import { usePolling } from "../api/poll";
import { DEFAULT_OPTIONS } from "../api/types";
import type { JobOptions } from "../api/types";
import { HealthBanner } from "../components/HealthBanner";
import { OptionsForm } from "../components/OptionsForm";
import { useToast } from "../components/Toasts";
import { formatBytes } from "../lib/format";
import { validateOptions } from "../lib/rules";

type Source = "upload" | "url" | "path";

export default function NewJob() {
  const [source, setSource] = useState<Source>("upload");
  const [options, setOptions] = useState<JobOptions>({ ...DEFAULT_OPTIONS });
  const [file, setFile] = useState<File | null>(null);
  const [dragging, setDragging] = useState(false);
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [mediaRoot, setMediaRoot] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);
  const navigate = useNavigate();
  const { push } = useToast();

  const refreshRoot = useCallback(async () => {
    try {
      setMediaRoot((await health()).media_root);
    } catch {
      // HealthBanner reports unreachability; the path hint just stays generic
    }
  }, []);
  usePolling(refreshRoot, 60_000);

  const errors = validateOptions(options);
  const errorCount = Object.keys(errors).length;

  async function submit() {
    if (errorCount) {
      push("Fix the highlighted fields first.");
      return;
    }
    setBusy(true);
    try {
      let job;
      if (source === "upload") {
        if (!file) {
          push("Choose a video file first.");
          return;
        }
        setProgress(0);
        job = await uploadJob(file, options, setProgress);
      } else if (source === "url") {
        job = await createJobFromUrl(url.trim(), options);
      } else {
        job = await createJobFromPath(path.trim(), options);
      }
      push("Job started.", "ok");
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  const tab = (key: Source, title: string) => (
    <button type="button"
      className={`tab ${source === key ? "active" : ""}`}
      aria-pressed={source === key}
      onClick={() => setSource(key)}>
      {title}
    </button>
  );

  // Drag state is set on dragover and cleared only when the pointer actually
  // leaves the zone — a child element's dragleave would otherwise flicker it.
  function onDragOver(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    if (!dragging) setDragging(true);
  }
  function onDragLeave(e: DragEvent<HTMLDivElement>) {
    const next = e.relatedTarget as Node | null;
    if (next && e.currentTarget.contains(next)) return;
    setDragging(false);
  }
  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files?.[0];
    if (dropped) setFile(dropped);
  }
  function onZoneKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      picker.current?.click();
    }
  }

  const sourceReady =
    source === "upload" ? file !== null :
    source === "url" ? url.trim() !== "" :
    path.trim() !== "";

  const missingSource =
    source === "upload" ? "Choose a video file to start" :
    source === "url" ? "Paste a YouTube URL to start" :
    "Name a file on the server to start";

  const status =
    errorCount ? `${errorCount} field${errorCount === 1 ? "" : "s"} need attention` :
    !sourceReady ? missingSource :
    "Ready to start";

  return (
    <div className="page">
      <div className="page-head">
        <h1 className="page-title">New job</h1>
      </div>
      <HealthBanner />

      <div className="tabs">
        {tab("upload", "Upload")}
        {tab("url", "YouTube URL")}
        {tab("path", "Server path")}
      </div>

      <div className="panel">
        {source === "upload" && (
          <div>
            <div
              className={`dropzone ${dragging ? "is-dragging" : ""}`}
              role="button"
              tabIndex={0}
              aria-label="Choose a video file, or drop one here"
              onClick={() => picker.current?.click()}
              onKeyDown={onZoneKey}
              onDragOver={onDragOver}
              onDragLeave={onDragLeave}
              onDrop={onDrop}>
              <div>Drop a video here, or click to browse</div>
              <div className="dropzone-hint">
                mp4, mov, mkv, m4v — it uploads to the server and stays there.
              </div>
            </div>
            <input ref={picker} id="src-file" type="file" hidden
              accept="video/*,.mkv,.mov,.m4v"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {file && (
              <div className="field">
                <div className="file-pill">
                  <span className="truncate">{file.name}</span>
                  <span className="mono">{formatBytes(file.size)}</span>
                  <button type="button" className="btn btn-ghost btn-sm"
                    aria-label={`Remove ${file.name}`}
                    onClick={() => {
                      setFile(null);
                      if (picker.current) picker.current.value = "";
                    }}>
                    Remove
                  </button>
                </div>
              </div>
            )}
            {progress !== null && (
              <div className="field">
                <div className="progress"><div style={{ width: `${progress * 100}%` }} /></div>
                <div className="field-help mono">{Math.round(progress * 100)}% uploaded</div>
              </div>
            )}
          </div>
        )}

        {source === "url" && (
          <div className="field">
            <label className="field-label" htmlFor="src-url">YouTube URL</label>
            <input id="src-url" placeholder="https://youtu.be/…" value={url}
              onChange={(e) => setUrl(e.target.value)} />
            <div className="field-help">
              Only YouTube hosts are accepted — anything else comes back as a 403.
            </div>
          </div>
        )}

        {source === "path" && (
          <div className="field">
            <label className="field-label" htmlFor="src-path">Path on the server</label>
            <input id="src-path" placeholder="talks/keynote.mov" value={path}
              onChange={(e) => setPath(e.target.value)} />
            <div className="field-help">
              Relative to {mediaRoot ? <span className="mono">{mediaRoot}</span> : "the media root"};
              absolute paths must still resolve inside it.
            </div>
          </div>
        )}
      </div>

      <OptionsForm value={options} onChange={setOptions} errors={errors} />

      <div className="sticky-bar">
        <div className="sticky-bar-status truncate">{status}</div>
        <div className="sticky-bar-actions">
          <button className="btn btn-primary" disabled={busy || !sourceReady}
            onClick={submit}>
            {busy ? "Starting…" : "Start job"}
          </button>
        </div>
      </div>
    </div>
  );
}
