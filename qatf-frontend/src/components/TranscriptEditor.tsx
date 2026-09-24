import { useCallback, useEffect, useState } from "react";
import { ApiError, getTranscript, putTranscript, suggestCorrections } from "../api/client";
import { RUNNING_STATES } from "../api/types";
import type {
  JobResponse, SuggestionModel, TranscriptResponse, WordModel,
} from "../api/types";
import { Icon } from "./Icon";
import { useI18n } from "../i18n/I18nProvider";
import "./TranscriptEditor.css";
import { useToast } from "./Toasts";
import { transcriptEditGuard } from "../lib/rules";

const RTL_LANGS = new Set(["ar", "he", "fa", "ur"]);

interface Props {
  jobId: string;
  job: JobResponse;
}

/** Word-level text corrections. The transcript is this page's whole purpose, so
 * it loads on mount; words are edited one at a time and submitted wholesale —
 * the server diffs them.
 *
 * THREE RULES THIS COMPONENT ENFORCES, none of them decoration:
 *  - `transcriptEditGuard` runs before every submit. It mirrors the server's
 *    contract: only text may differ.
 *  - There is NO timing input anywhere. Timings come from the audio and every
 *    cut is snapped to them; a spelling fix may never move one.
 *  - Saving is refused while the job is running, and `beginEdit` early-returns
 *    while a save is in flight so a click cannot edit words already submitted.
 */
export function TranscriptEditor({ jobId, job }: Props) {
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  // The proposed pass, held separately from `edits` until it is accepted —
  // accepting is what turns suggestions into ordinary pending corrections.
  const [pass, setPass] = useState<SuggestionModel[] | null>(null);
  const [passInfo, setPassInfo] = useState<{ dropped: number; terms: number; model: string } | null>(null);
  const [thinking, setThinking] = useState(false);
  const { push } = useToast();
  const { t, languageName } = useI18n();
  const tx = t.transcript;

  const running = RUNNING_STATES.has(job.state);

  /** Ask the server for candidate corrections. Writes nothing — accepting is
   * what folds them into `edits`, and saving still goes through the same
   * `putTranscript` the manual path uses. */
  async function enhance() {
    if (thinking) return;
    setThinking(true);
    try {
      // The job's own hotwords, plus whatever the server ships. The server
      // unions in its list too; sending these covers a job that set its own.
      const terms = (job.options.hotwords ?? "").split(/\s+/).filter(Boolean);
      const r = await suggestCorrections(jobId, terms);
      setPass(r.suggestions);
      setPassInfo({ dropped: r.dropped, terms: r.terms_used, model: r.model });
      if (r.suggestions.length === 0) {
        push(r.terms_used === 0 ? tx.noTerms : tx.noSuggestions(r.dropped));
      }
    } catch (e) {
      push(e instanceof ApiError ? e.message : tx.aiDown);
    } finally {
      setThinking(false);
    }
  }

  /** Fold the whole pass into the pending corrections.
   *
   * Whole-diff accept is safe because the per-word editor is still right there:
   * accept, then click any single word the model got wrong. You are never made
   * to discard the good ones over one bad one. */
  function acceptPass() {
    if (!pass) return;
    setEdits((prev) => {
      const next = { ...prev };
      for (const s of pass) next[s.index] = s.text;
      return next;
    });
    setPass(null);
    setPassInfo(null);
  }

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setTranscript(await getTranscript(jobId));
      setEdits({});
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }, [jobId, push]);

  const hasWords = job.word_count > 0;
  useEffect(() => {
    if (!hasWords) return;
    void load();
  }, [hasWords, load]);

  function beginEdit(index: number) {
    if (saving || !transcript) return;
    setEditing(index);
    setDraft(edits[index] ?? transcript.words[index].text);
  }

  function commitEdit() {
    if (editing === null || !transcript) return;
    const originalText = transcript.words[editing].text;
    setEdits((current) => {
      const next = { ...current };
      if (draft === originalText || draft === "") delete next[editing];
      else next[editing] = draft;
      return next;
    });
    setEditing(null);
  }

  async function save() {
    if (!transcript) return;
    const edited: WordModel[] = transcript.words.map((word, i) =>
      i in edits ? { ...word, text: edits[i] } : word);
    // The guard decides; the reader gets the plain-language reason.
    if (transcriptEditGuard(transcript.words, edited)) {
      push(tx.guard);
      return;
    }
    setSaving(true);
    try {
      const response = await putTranscript(jobId, edited);
      setTranscript(response);
      setEdits({});
      push(tx.saved(response.edits_applied, response.edits_stale), "ok");
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  if (job.word_count === 0) return null;

  if (!transcript) {
    return loading ? (
      <div className="skeleton-row" aria-busy="true" aria-label={tx.title} />
    ) : (
      <div className="empty">
        <p className="empty-title">{tx.failTitle}</p>
        <p className="empty-body">{tx.failBody}</p>
        <button className="btn" onClick={() => void load()}>{tx.retry}</button>
      </div>
    );
  }

  const language = transcript.language ?? job.language;
  const dir = language && RTL_LANGS.has(language.split("-")[0]) ? "rtl" : "auto";
  const pending = Object.keys(edits).length;
  const dirty = pending > 0;

  return (
    <div>
      <div className="transcript-head">
        <span className="transcript-stat">{tx.words(transcript.word_count)}</span>
        <span className="transcript-stat">
          {languageName(transcript.language) ?? t.common.notYet}
          {transcript.language_probability !== null
            ? ` · ${tx.confidence(Math.round(transcript.language_probability * 1000) / 10)}` : ""}
        </span>
        <span className="transcript-stat" title={tx.timingTip}>
          {transcript.timing_source === "captions" ? tx.timingCaptions : tx.timingAsr}
        </span>
        <span className="transcript-stat">{tx.fixesSaved(transcript.edits_applied)}</span>
        {transcript.edits_stale > 0 && (
          <span className="transcript-stat is-warn" title={tx.staleTip}>
            {tx.stale(transcript.edits_stale)}
          </span>
        )}
      </div>

      <p className="transcript-hint">
        {tx.hint}
        <span className="legend"><span className="legend-swatch" aria-hidden="true" /> {tx.legend}</span>
      </p>

      <div className="words" dir={dir} lang={language ?? undefined}>
        {transcript.words.map((word, i) =>
          editing === i ? (
            <input
              key={i}
              className="word-input"
              dir={dir}
              autoFocus
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                if (e.key === "Enter") commitEdit();
                if (e.key === "Escape") setEditing(null);
              }}
            />
          ) : (
            // A real control, not a click target: correcting a word is this
            // page's only interaction, so it has to be reachable by keyboard —
            // and an element that cannot take focus can never show the focus
            // ring the rest of the app has. Same pattern as the dropzone.
            <span
              key={i}
              className={`word ${i in edits ? "is-edited" : ""}`}
              role="button"
              tabIndex={0}
              title={`${word.start.toFixed(2)}–${word.end.toFixed(2)}s`}
              onClick={() => beginEdit(i)}
              onKeyDown={(e) => {
                if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault();
                  beginEdit(i);
                }
              }}
            >
              {(i in edits ? edits[i] : word.text) + " "}
            </span>
          ),
        )}
      </div>

      {pass && pass.length > 0 && passInfo && (
        <div className="banner banner-warn suggest">
          <p className="suggest-head">
            {tx.suggestions(pass.length, passInfo.model, passInfo.terms)}
            {passInfo.dropped > 0 && tx.rejected(passInfo.dropped)}{" "}
            {tx.suggestNote}
          </p>
          <ul className="suggest-list">
            {pass.map((s) => (
              <li className="suggest-item" key={s.index}>
                <span className="tnum suggest-idx">{s.index}</span>
                <span className="suggest-was" dir="auto">{s.was}</span>
                <Icon name="arrowRight" size={14} className="suggest-arrow" />
                <span className="suggest-new" dir="auto">
                  {s.text === "" ? <em>{tx.removeWord}</em> : s.text}
                </span>
                <span className="suggest-why" dir="auto">{s.why}</span>
              </li>
            ))}
          </ul>
          <div className="row">
            <button className="btn btn-primary" onClick={acceptPass}>
              <Icon name="check" size={16} />
              {tx.acceptAll(pass.length)}
            </button>
            <button
              className="btn btn-ghost"
              onClick={() => { setPass(null); setPassInfo(null); }}
            >
              {tx.ignore}
            </button>
          </div>
        </div>
      )}

      <div className="sticky-bar">
        <div className={`sticky-bar-status ${running ? "is-error" : dirty ? "is-ready" : ""}`}>
          <span className="status-dot" aria-hidden="true" />
          <span>{running ? tx.running : dirty ? tx.pending(pending) : tx.nonePending}</span>
        </div>
        <div className="sticky-bar-actions">
          <button
            className="btn"
            onClick={() => void enhance()}
            disabled={thinking || saving || running}
            title={tx.suggestTip}
          >
            <Icon name="sparkle" size={16} />
            {thinking ? tx.thinking : tx.suggest}
          </button>
          {dirty && (
            <button className="btn btn-ghost" onClick={() => setEdits({})} disabled={saving}>
              {t.common.discard}
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={!dirty || saving || running}
            title={running ? tx.running : ""}
          >
            {saving ? t.common.saving : pending === 0 ? tx.save : tx.saveN(pending)}
          </button>
        </div>
      </div>
    </div>
  );
}
