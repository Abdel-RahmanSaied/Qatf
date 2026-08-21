import { useCallback, useEffect, useState } from "react";
import { ApiError, getTranscript, putTranscript, suggestCorrections } from "../api/client";
import { RUNNING_STATES } from "../api/types";
import type {
  JobResponse, SuggestionModel, TranscriptResponse, WordModel,
} from "../api/types";
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
        push(r.terms_used === 0
          ? "No terms to match against — add some vocabulary first."
          : `Nothing to suggest (${r.dropped} refused).`);
      }
    } catch (e) {
      push(e instanceof ApiError ? e.message : "the model could not be reached");
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
    const problem = transcriptEditGuard(transcript.words, edited);
    if (problem) {
      push(problem);
      return;
    }
    setSaving(true);
    try {
      const response = await putTranscript(jobId, edited);
      setTranscript(response);
      setEdits({});
      const stale = response.edits_stale > 0
        ? ` (${response.edits_stale} stale — the transcript moved underneath them)` : "";
      push(`${response.edits_applied} correction(s) in effect${stale}.`, "ok");
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  if (job.word_count === 0) return null;

  if (!transcript) {
    return loading ? (
      <div className="skeleton-row" aria-label="Loading the transcript" />
    ) : (
      <div className="empty">
        <p className="empty-title">The transcript did not load</p>
        <p className="empty-body">The server refused or could not be reached.</p>
        <button className="btn" onClick={() => void load()}>Load the transcript</button>
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
        <span className="transcript-stat">
          <span className="tnum">{transcript.word_count}</span> words
        </span>
        <span className="transcript-stat">
          {transcript.language ?? "language unknown"}
          {transcript.language_probability !== null
            ? ` · p=${transcript.language_probability.toFixed(3)}` : ""}
        </span>
        <span className="transcript-stat" title="Caption tracks carry a start and no end, so a word's end is the next word's start — an upper bound, not a measurement.">
          timings from {transcript.timing_source}
        </span>
        <span className="transcript-stat">
          <span className="tnum">{transcript.edits_applied}</span>{" "}
          {transcript.edits_applied === 1 ? "correction" : "corrections"} in effect
        </span>
        {transcript.edits_stale > 0 && (
          <span className="transcript-stat" title="The transcript moved underneath these corrections — a different Whisper size, or denoise toggled.">
            <span className="tnum">{transcript.edits_stale}</span> stale
          </span>
        )}
      </div>

      <p className="muted">
        Click a word to correct its text. Timings are not editable — a correction
        changes what a caption reads and can never move a cut.
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
            <span className="tnum">{pass.length}</span>{" "}
            {pass.length === 1 ? "suggestion" : "suggestions"} from{" "}
            <span className="mono">{passInfo.model}</span>, matched against{" "}
            <span className="tnum">{passInfo.terms}</span> terms
            {passInfo.dropped > 0 && (
              <> — <span className="tnum">{passInfo.dropped}</span> refused by the
                server for proposing something outside that list</>
            )}
            . Nothing is applied until you accept, and nothing is saved until you
            press Save corrections.
          </p>
          <ul className="suggest-list">
            {pass.map((s) => (
              <li className="suggest-item" key={s.index}>
                <span className="tnum suggest-idx">{s.index}</span>
                <span className="suggest-was">{s.was}</span>
                <span className="suggest-arrow">→</span>
                <span className="suggest-new">
                  {s.text === "" ? <em>(delete)</em> : s.text}
                </span>
                <span className="suggest-why">{s.why}</span>
              </li>
            ))}
          </ul>
          <div className="row">
            <button className="btn btn-primary" onClick={acceptPass}>
              Accept all {pass.length}
            </button>
            <button
              className="btn btn-ghost"
              onClick={() => { setPass(null); setPassInfo(null); }}
            >
              Discard suggestions
            </button>
          </div>
        </div>
      )}

      <div className="sticky-bar">
        <div className="sticky-bar-status">
          {running
            ? "The job is running — corrections are refused until it stops."
            : dirty
              ? <>
                  <span className="tnum">{pending}</span>{" "}
                  {pending === 1 ? "correction" : "corrections"} pending
                </>
              : "No corrections pending."}
        </div>
        <div className="sticky-bar-actions">
          <button
            className="btn"
            onClick={() => void enhance()}
            disabled={thinking || saving || running}
            title="Ask the model which words look misheard. Nothing is applied until you accept."
          >
            {thinking ? "Reading…" : "AI enhance"}
          </button>
          {dirty && (
            <button className="btn btn-ghost" onClick={() => setEdits({})} disabled={saving}>
              Discard
            </button>
          )}
          <button
            className="btn btn-primary"
            onClick={save}
            disabled={!dirty || saving || running}
            title={running ? "The job is running — corrections are refused until it stops." : ""}
          >
            {saving
              ? "Saving…"
              : pending === 0
                ? "Save corrections"
                : `Save ${pending} ${pending === 1 ? "correction" : "corrections"}`}
          </button>
        </div>
      </div>
    </div>
  );
}
