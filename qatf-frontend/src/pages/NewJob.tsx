import { useCallback, useState } from "react";
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
import { validateOptions } from "../lib/rules";

type Source = "upload" | "url" | "path";

export default function NewJob() {
  const [source, setSource] = useState<Source>("upload");
  const [options, setOptions] = useState<JobOptions>({ ...DEFAULT_OPTIONS });
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [path, setPath] = useState("");
  const [mediaRoot, setMediaRoot] = useState<string | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
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

  async function submit() {
    if (Object.keys(errors).length) {
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
      onClick={() => setSource(key)}>
      {title}
    </button>
  );

  const sourceReady =
    source === "upload" ? file !== null :
    source === "url" ? url.trim() !== "" :
    path.trim() !== "";

  return (
    <div>
      <h1>New job</h1>
      <HealthBanner />
      <div className="tabs">
        {tab("upload", "Upload")}
        {tab("url", "YouTube URL")}
        {tab("path", "Server path")}
      </div>
      <div className="panel">
        {source === "upload" && (
          <div>
            <label htmlFor="src-file">video file</label>
            <input id="src-file" type="file" accept="video/*,.mkv,.mov,.m4v"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            {progress !== null && (
              <div>
                <div className="progress"><div style={{ width: `${progress * 100}%` }} /></div>
                <span className="muted">{Math.round(progress * 100)}% uploaded</span>
              </div>
            )}
          </div>
        )}
        {source === "url" && (
          <div>
            <label htmlFor="src-url">YouTube URL</label>
            <input id="src-url" placeholder="https://youtu.be/…" value={url}
              onChange={(e) => setUrl(e.target.value)} />
            <div className="help">Only YouTube hosts are accepted — anything else is a 403.</div>
          </div>
        )}
        {source === "path" && (
          <div>
            <label htmlFor="src-path">path on the server</label>
            <input id="src-path" placeholder="talks/keynote.mov" value={path}
              onChange={(e) => setPath(e.target.value)} />
            <div className="help">
              Relative to {mediaRoot ? <span className="mono">{mediaRoot}</span> : "the media root"};
              absolute paths must still resolve inside it.
            </div>
          </div>
        )}
      </div>
      <OptionsForm value={options} onChange={setOptions} errors={errors} />
      <button className="btn btn-primary" disabled={busy || !sourceReady} onClick={submit}>
        {busy ? "starting…" : "Start job"}
      </button>
    </div>
  );
}
