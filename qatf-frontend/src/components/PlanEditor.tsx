import { useState } from "react";
import { ApiError, putPlan } from "../api/client";
import { RUNNING_STATES } from "../api/types";
import type { ClipModel, JobResponse } from "../api/types";
import { useToast } from "./Toasts";
import { durationWarning } from "../lib/rules";
import { formatSeconds } from "../lib/format";

interface Props {
  jobId: string;
  job: JobResponse;
  onSaved: () => Promise<void>;
}

/** Edit the plan the model produced. Boundaries typed here are SEMANTIC
 * guesses — the server re-snaps them onto Whisper word times (snap is always
 * true), and the saved response replaces the draft so the user sees where the
 * cuts actually landed. */
export function PlanEditor({ jobId, job, onSaved }: Props) {
  const [draft, setDraft] = useState<ClipModel[]>(() =>
    job.clips.map((clip) => ({ ...clip })));
  const [saving, setSaving] = useState(false);
  const [snapped, setSnapped] = useState(false);
  const { push } = useToast();

  // Mirror the server's rule, which is `not running` — NOT `planned or done`.
  // `PUT /plan` is accepted on a failed or cancelled job too, and being
  // stricter here locked the plan on exactly the job most likely to need
  // fixing: a failed one, whose Render button was offered while its table sat
  // read-only.
  const editable = !RUNNING_STATES.has(job.state);
  if (job.clips.length === 0) return null;

  // Every edit invalidates the snapped notice: the numbers on screen are no
  // longer the ones the server returned, so the notice would be describing
  // boundaries that no longer exist.
  const update = (i: number, patch: Partial<ClipModel>) => {
    setSnapped(false);
    setDraft((current) => current.map((c, j) => (j === i ? { ...c, ...patch } : c)));
  };

  const move = (i: number, delta: number) => {
    setSnapped(false);
    setDraft((current) => {
      const j = i + delta;
      if (j < 0 || j >= current.length) return current;
      const next = [...current];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });
  };

  const remove = (i: number) => {
    setSnapped(false);
    setDraft((current) => current.filter((_, j) => j !== i));
  };

  const add = () => {
    setSnapped(false);
    setDraft((current) => {
      const last = current[current.length - 1];
      const start = last ? last.end + 1 : 0;
      return [...current, {
        start, end: start + job.options.min_len,
        title: "clip", hook: "", why: "", score: 0,
      }];
    });
  };

  async function save() {
    for (const [i, clip] of draft.entries()) {
      if (clip.end <= clip.start) {
        push(`Clip ${i + 1}: end must be after start.`);
        return;
      }
    }
    if (draft.length === 0) {
      push("A plan needs at least one clip — delete the job instead.");
      return;
    }
    setSaving(true);
    try {
      const stored = await putPlan(jobId, draft);
      setDraft(stored.map((clip) => ({ ...clip })));
      setSnapped(true);
      push("Plan saved — boundaries re-snapped onto word times.", "ok");
      await onSaved();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  const warnings = draft.reduce(
    (n, clip) => n + (durationWarning(clip, job.options.max_len) ? 1 : 0), 0);
  const picked = draft.reduce((total, clip) => total + Math.max(0, clip.end - clip.start), 0);

  return (
    <div className="card">
      <div className="card-head">
        <h2 className="card-title">Plan</h2>
        <p className="card-sub">
          The model chooses the passage; the server snaps each boundary onto a Whisper
          word time on save.
        </p>
      </div>

      {!editable && (
        <div className="banner banner-warn">
          The plan is read-only while the job is working. It opens again once the job
          reaches planned or done.
        </div>
      )}
      {snapped && (
        <div className="banner banner-info">
          These are the snapped boundaries the render will use.
        </div>
      )}

      <div className="card-body">
        <table className="plan">
          <thead>
            <tr>
              <th>#</th>
              <th>start (s)</th>
              <th>end (s)</th>
              <th>length</th>
              <th>title</th>
              <th>score</th>
              <th><span className="dim">actions</span></th>
            </tr>
          </thead>
          <tbody>
            {draft.map((clip, i) => {
              const warning = durationWarning(clip, job.options.max_len);
              return (
                <tr key={i}>
                  <td className="mono dim">{String(i + 1).padStart(2, "0")}</td>
                  <td className="plan-num">
                    <input type="number" step={0.01} min={0} value={clip.start}
                      aria-label={`Clip ${i + 1} start in seconds`}
                      disabled={!editable}
                      onChange={(e) => update(i, { start: Number(e.target.value) })} />
                  </td>
                  <td className="plan-num">
                    <input type="number" step={0.01} min={0} value={clip.end}
                      aria-label={`Clip ${i + 1} end in seconds`}
                      disabled={!editable}
                      onChange={(e) => update(i, { end: Number(e.target.value) })} />
                  </td>
                  <td className="plan-num">
                    {formatSeconds(Math.max(0, clip.end - clip.start))}
                    {warning && <div className="plan-warn">{warning}</div>}
                  </td>
                  <td>
                    <input value={clip.title} dir="auto" disabled={!editable}
                      aria-label={`Clip ${i + 1} title`}
                      title={clip.hook ? `hook: ${clip.hook}\nwhy: ${clip.why}` : undefined}
                      onChange={(e) => update(i, { title: e.target.value })} />
                  </td>
                  <td className="mono muted">{clip.score.toFixed(2)}</td>
                  <td>
                    <div className="row">
                      <button className="btn btn-sm" disabled={!editable || i === 0}
                        aria-label={`Move clip ${i + 1} up`}
                        onClick={() => move(i, -1)}>↑</button>
                      <button className="btn btn-sm" disabled={!editable || i === draft.length - 1}
                        aria-label={`Move clip ${i + 1} down`}
                        onClick={() => move(i, 1)}>↓</button>
                      <button className="btn btn-sm btn-danger" disabled={!editable}
                        aria-label={`Remove clip ${i + 1}`}
                        onClick={() => remove(i)}>✕</button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="field-help">
        Typed seconds are semantic guesses — the server snaps them onto Whisper word
        boundaries on save. Titles become the output filenames (ASCII-slugified).
      </p>

      <div className="sticky-bar">
        <div className="sticky-bar-status">
          {draft.length === 0 ? (
            <span>No clips left. Add one, or reset to the stored plan.</span>
          ) : (
            <>
              <span className="tnum">{draft.length}</span>{" "}
              <span>clip{draft.length === 1 ? "" : "s"}, </span>
              <span className="tnum">{formatSeconds(picked)}</span>{" "}
              <span>picked</span>
              {warnings > 0 && (
                <span className="plan-warn"> · {warnings} clip{warnings === 1 ? "" : "s"} run
                  past max_len {job.options.max_len}s
                </span>
              )}
            </>
          )}
        </div>
        <div className="sticky-bar-actions">
          <button className="btn btn-ghost" disabled={!editable}
            onClick={() => {
              setDraft(job.clips.map((clip) => ({ ...clip })));
              setSnapped(false);
            }}>
            Reset to stored plan
          </button>
          <button className="btn" disabled={!editable} onClick={add}>Add clip</button>
          <button className="btn btn-primary" disabled={!editable || saving} onClick={save}>
            {saving ? "Saving…" : "Save plan"}
          </button>
        </div>
      </div>
    </div>
  );
}
