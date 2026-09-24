import { useCallback, useRef, useState } from "react";
import type { DragEvent, KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ApiError, createJobFromPath, createJobFromUrl, health, uploadJob,
} from "../../api/client";
import { usePolling } from "../../api/poll";
import { DEFAULT_OPTIONS } from "../../api/types";
import type { JobOptions } from "../../api/types";
import { HealthBanner } from "../../components/HealthBanner";
import { Icon } from "../../components/Icon";
import type { IconName } from "../../components/Icon";
import { OptionsForm } from "../../components/OptionsForm";
import { PageHeader } from "../../components/PageHeader";
import { useToast } from "../../components/Toasts";
import { useI18n } from "../../i18n/I18nProvider";
import { formatBytes } from "../../lib/format";
import { validateOptions } from "../../lib/rules";
import "./NewJobPage.css";

type Source = "upload" | "url" | "path";

const SOURCES: { key: Source; icon: IconName }[] = [
  { key: "upload", icon: "upload" },
  { key: "url", icon: "link" },
  { key: "path", icon: "folder" },
];

export default function NewJobPage() {
  const { t, languageName } = useI18n();
  const tn = t.newJob;
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
  const sourceButtons = useRef<(HTMLButtonElement | null)[]>([]);
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
      push(tn.toastFix);
      return;
    }
    setBusy(true);
    try {
      let job;
      if (source === "upload") {
        if (!file) {
          push(tn.toastFile);
          return;
        }
        setProgress(0);
        job = await uploadJob(file, options, setProgress);
      } else if (source === "url") {
        job = await createJobFromUrl(url.trim(), options);
      } else {
        job = await createJobFromPath(path.trim(), options);
      }
      push(tn.toastStarted, "ok");
      navigate(`/jobs/${job.id}`);
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setBusy(false);
      setProgress(null);
    }
  }

  // A radio group moves with the arrow keys and is ONE tab stop, which is what
  // a keyboard user expects from three mutually exclusive choices. In RTL the
  // horizontal arrows swap meaning, as they do in every native radio group.
  function onSourceKey(e: KeyboardEvent<HTMLDivElement>) {
    const rtl = t.dir === "rtl";
    const forward = ["ArrowDown", rtl ? "ArrowLeft" : "ArrowRight"];
    const back = ["ArrowUp", rtl ? "ArrowRight" : "ArrowLeft"];
    if (!forward.includes(e.key) && !back.includes(e.key)) return;
    e.preventDefault();
    const step = forward.includes(e.key) ? 1 : -1;
    const at = SOURCES.findIndex((s) => s.key === source);
    const next = (at + step + SOURCES.length) % SOURCES.length;
    setSource(SOURCES[next].key);
    sourceButtons.current[next]?.focus();
  }

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

  const status =
    errorCount ? tn.needFix(errorCount) :
    !sourceReady ? (source === "upload" ? tn.needFile : source === "url" ? tn.needUrl : tn.needPath) :
    tn.ready;

  const sourceLabel =
    source === "upload" ? (file ? file.name : tn.sumNoFile) :
    source === "url" ? (url.trim() || tn.sumNoUrl) :
    (path.trim() || tn.sumNoPath);

  const sourceText: Record<Source, { title: string; body: string }> = {
    upload: { title: tn.upload, body: tn.uploadSub },
    url: { title: tn.url, body: tn.urlSub },
    path: { title: tn.path, body: tn.pathSub },
  };

  const o = t.options;
  const captionStyle = options.caption_style ?? DEFAULT_OPTIONS.caption_style;
  const reframe =
    options.reframe === "blur" ? o.reframeBlurShort :
    options.reframe === "track" ? o.reframeTrackShort :
    o.reframeCropShort;

  return (
    <div className="page">
      <PageHeader eyebrow={tn.eyebrow} title={tn.title} lede={tn.lede} />
      <HealthBanner />

      <div className="compose">
        <div className="compose-main">
          <section className="step" aria-labelledby="step-source">
            <div className="step-head">
              <span className="step-num" aria-hidden="true">1</span>
              <div>
                <h2 className="step-title" id="step-source">{tn.step1}</h2>
                <p className="step-sub">{tn.step1Sub}</p>
              </div>
            </div>

            <div className="source-grid" role="radiogroup" aria-label={tn.sourceGroup}
              onKeyDown={onSourceKey}>
              {SOURCES.map((s, i) => (
                <button
                  key={s.key}
                  ref={(el) => { sourceButtons.current[i] = el; }}
                  type="button"
                  role="radio"
                  aria-checked={source === s.key}
                  tabIndex={source === s.key ? 0 : -1}
                  className={source === s.key ? "source-card active" : "source-card"}
                  onClick={() => setSource(s.key)}
                >
                  <span className="source-icon" aria-hidden="true">
                    <Icon name={s.icon} size={20} />
                  </span>
                  <span className="source-title">{sourceText[s.key].title}</span>
                  <span className="source-body">{sourceText[s.key].body}</span>
                </button>
              ))}
            </div>

            <div className="panel">
              {source === "upload" && (
                <div>
                  <div
                    className={`dropzone ${dragging ? "is-dragging" : ""} ${file ? "has-file" : ""}`}
                    role="button"
                    tabIndex={0}
                    aria-label={tn.dropAria}
                    onClick={() => picker.current?.click()}
                    onKeyDown={onZoneKey}
                    onDragOver={onDragOver}
                    onDragLeave={onDragLeave}
                    onDrop={onDrop}>
                    <span className="dropzone-icon" aria-hidden="true">
                      <Icon name="upload" size={24} />
                    </span>
                    <div className="dropzone-title">{dragging ? tn.dropRelease : tn.dropTitle}</div>
                    <div className="dropzone-hint">{tn.dropHint}</div>
                  </div>
                  <input ref={picker} id="src-file" type="file" hidden
                    accept="video/*,.mkv,.mov,.m4v"
                    onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
                  {file && (
                    <div className="file-pill">
                      <Icon name="film" size={16} />
                      <span className="truncate" dir="auto">{file.name}</span>
                      <span className="mono muted">{formatBytes(file.size)}</span>
                      <button type="button" className="btn btn-ghost btn-sm btn-icon"
                        aria-label={tn.remove(file.name)}
                        onClick={() => {
                          setFile(null);
                          if (picker.current) picker.current.value = "";
                        }}>
                        <Icon name="x" size={14} />
                      </button>
                    </div>
                  )}
                  {progress !== null && (
                    <div className="field">
                      <div className="progress"><div style={{ width: `${progress * 100}%` }} /></div>
                      <div className="field-help mono">{tn.uploaded(Math.round(progress * 100))}</div>
                    </div>
                  )}
                </div>
              )}

              {source === "url" && (
                <div className="field field-flush">
                  <label className="field-label" htmlFor="src-url">{tn.urlLabel}</label>
                  <input id="src-url" type="url" inputMode="url" autoComplete="off" dir="ltr"
                    placeholder="https://youtu.be/…" value={url}
                    aria-describedby="src-url-help"
                    onChange={(e) => setUrl(e.target.value)} />
                  <div className="field-help" id="src-url-help">{tn.urlHelp}</div>
                </div>
              )}

              {source === "path" && (
                <div className="field field-flush">
                  <label className="field-label" htmlFor="src-path">{tn.pathLabel}</label>
                  <input id="src-path" autoComplete="off" spellCheck={false} dir="ltr"
                    placeholder="talks/keynote.mov" value={path}
                    aria-describedby="src-path-help"
                    onChange={(e) => setPath(e.target.value)} />
                  <div className="field-help" id="src-path-help">{tn.pathHelp(mediaRoot)}</div>
                </div>
              )}
            </div>
          </section>

          <section className="step" aria-labelledby="step-options">
            <div className="step-head">
              <span className="step-num" aria-hidden="true">2</span>
              <div>
                <h2 className="step-title" id="step-options">{tn.step2}</h2>
                <p className="step-sub">{tn.step2Sub}</p>
              </div>
            </div>
            <OptionsForm value={options} onChange={setOptions} errors={errors} />
          </section>
        </div>

        {/* The summary follows the reader down the page on wide screens, so
            what is about to run is always in view next to the button that
            runs it. On narrow screens it drops below the form. */}
        <aside className="compose-aside" aria-label={tn.summaryTitle}>
          <div className="summary">
            <div className="summary-title">{tn.summaryTitle}</div>
            <ol className="summary-list">
              <li>
                <span className="summary-k">{tn.sumVideo}</span>
                <span className="summary-v truncate" dir="auto" title={sourceLabel}>{sourceLabel}</span>
              </li>
              <li>
                <span className="summary-k">{tn.sumText}</span>
                <span className="summary-v">
                  {languageName(options.language) ?? o.autoLanguage} ·{" "}
                  <span className="mono">{options.whisper}</span>
                </span>
              </li>
              <li>
                <span className="summary-k">{tn.sumClips}</span>
                <span className="summary-v">
                  {tn.sumClipsValue(options.clips, options.min_len, options.max_len)}
                </span>
              </li>
              <li>
                <span className="summary-k">{tn.sumCaptions}</span>
                <span className="summary-v">
                  {options.captions
                    ? (captionStyle === "pop" ? o.stylePop : o.styleYoutube)
                    : tn.sumNoCaptions}
                </span>
              </li>
              <li>
                <span className="summary-k">{tn.sumQuality}</span>
                <span className="summary-v">
                  {reframe} · <span className="mono">{options.resolution}</span>
                </span>
              </li>
              <li>
                <span className="summary-k">{tn.sumThen}</span>
                <span className="summary-v">{options.auto_render ? tn.sumRender : tn.sumStop}</span>
              </li>
            </ol>
          </div>
        </aside>
      </div>

      <div className="sticky-bar">
        <div className={`sticky-bar-status truncate ${errorCount ? "is-error" : sourceReady ? "is-ready" : ""}`}>
          <span className="status-dot" aria-hidden="true" />
          {status}
        </div>
        <div className="sticky-bar-actions">
          <button className="btn btn-primary btn-lg" disabled={busy || !sourceReady}
            onClick={submit}>
            {busy ? tn.starting : <>{tn.start} <Icon name="arrowRight" /></>}
          </button>
        </div>
      </div>
    </div>
  );
}
