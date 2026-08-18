import { useState } from "react";
import { ApiError, getTranscript, putTranscript } from "../api/client";
import type { JobResponse, TranscriptResponse, WordModel } from "../api/types";
import { useToast } from "./Toasts";
import { transcriptEditGuard } from "../lib/rules";

const RTL_LANGS = new Set(["ar", "he", "fa", "ur"]);

interface Props {
  jobId: string;
  job: JobResponse;
}

/** Word-level text corrections. Loaded on demand (a transcript can be 27k
 * words), edited word by word, submitted wholesale — the server diffs it. */
export function TranscriptEditor({ jobId, job }: Props) {
  const [transcript, setTranscript] = useState<TranscriptResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [edits, setEdits] = useState<Record<number, string>>({});
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);
  const { push } = useToast();

  const running = !["planned", "done", "failed", "cancelled"].includes(job.state);

  async function load() {
    setLoading(true);
    try {
      setTranscript(await getTranscript(jobId));
      setEdits({});
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setLoading(false);
    }
  }

  function beginEdit(index: number) {
    if (!transcript) return;
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

  const language = transcript?.language ?? job.language;
  const dir = language && RTL_LANGS.has(language.split("-")[0]) ? "rtl" : "auto";
  const dirty = Object.keys(edits).length > 0;

  return (
    <div>
      <h2>transcript</h2>
      {!transcript && (
        <button className="btn" onClick={load} disabled={loading}>
          {loading ? "loading…" : `show ${job.word_count} words`}
        </button>
      )}
      {transcript && (
        <div className="panel">
          <div className="muted">
            {transcript.language ?? "unknown language"}
            {transcript.language_probability !== null
              ? ` (p=${transcript.language_probability.toFixed(3)})` : ""}
            {" · timings from "}{transcript.timing_source}
            {" · "}{transcript.edits_applied} correction(s) in effect
            {transcript.edits_stale > 0 ? ` · ${transcript.edits_stale} STALE` : ""}
          </div>
          <div className="help">
            Click a word to correct its text. Timings are not editable — a correction
            changes what a caption reads and can never move a cut.
          </div>
          <div className="words" dir={dir}>
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
                <span
                  key={i}
                  className={`word ${i in edits ? "edited" : ""}`}
                  title={`${word.start.toFixed(2)}–${word.end.toFixed(2)}s`}
                  onClick={() => beginEdit(i)}
                >
                  {(i in edits ? edits[i] : word.text) + " "}
                </span>
              ),
            )}
          </div>
          <div className="row-actions">
            <button className="btn btn-primary" onClick={save}
              disabled={!dirty || saving || running}
              title={running ? "the job is running — corrections are refused until it stops" : ""}>
              {saving ? "saving…" : `save ${Object.keys(edits).length} correction(s)`}
            </button>
            {dirty && (
              <button className="btn" onClick={() => setEdits({})}>discard</button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
