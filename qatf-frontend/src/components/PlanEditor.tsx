import { useState } from "react";
import { ApiError, putPlan } from "../api/client";
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

  const editable = job.state === "planned" || job.state === "done";
  if (job.clips.length === 0) return null;

  const update = (i: number, patch: Partial<ClipModel>) =>
    setDraft((current) => current.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  const move = (i: number, delta: number) =>
    setDraft((current) => {
      const j = i + delta;
      if (j < 0 || j >= current.length) return current;
      const next = [...current];
      [next[i], next[j]] = [next[j], next[i]];
      return next;
    });

  const remove = (i: number) =>
    setDraft((current) => current.filter((_, j) => j !== i));

  const add = () =>
    setDraft((current) => {
      const last = current[current.length - 1];
      const start = last ? last.end + 1 : 0;
      return [...current, {
        start, end: start + job.options.min_len,
        title: "clip", hook: "", why: "", score: 0,
      }];
    });

  async function save() {
    for (const [i, clip] of draft.entries()) {
      if (clip.end <= clip.start) {
        push(`clip ${i + 1}: end must be after start`);
        return;
      }
    }
    if (draft.length === 0) {
      push("a plan needs at least one clip — delete the job instead");
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

  return (
    <div>
      <h2>plan</h2>
      <div className="panel">
        {!editable && (
          <div className="help">The plan is read-only while the job is working.</div>
        )}
        {snapped && (
          <div className="banner banner-info">
            These are the snapped boundaries the render will use.
          </div>
        )}
        <table className="plan-table">
          <thead>
            <tr>
              <th>#</th><th>start (s)</th><th>end (s)</th><th>length</th>
              <th>title</th><th>score</th><th></th>
            </tr>
          </thead>
          <tbody>
            {draft.map((clip, i) => {
              const warning = durationWarning(clip, job.options.max_len);
              return (
                <tr key={i}>
                  <td>{String(i + 1).padStart(2, "0")}</td>
                  <td>
                    <input type="number" step={0.01} min={0} value={clip.start}
                      disabled={!editable}
                      onChange={(e) => update(i, { start: Number(e.target.value) })} />
                  </td>
                  <td>
                    <input type="number" step={0.01} min={0} value={clip.end}
                      disabled={!editable}
                      onChange={(e) => update(i, { end: Number(e.target.value) })} />
                  </td>
                  <td className="mono">
                    {formatSeconds(Math.max(0, clip.end - clip.start))}
                    {warning && <div className="field-error">{warning}</div>}
                  </td>
                  <td>
                    <input value={clip.title} dir="auto" disabled={!editable}
                      title={clip.hook ? `hook: ${clip.hook}\nwhy: ${clip.why}` : undefined}
                      onChange={(e) => update(i, { title: e.target.value })} />
                  </td>
                  <td className="muted">{clip.score.toFixed(2)}</td>
                  <td className="row-actions">
                    <button className="btn" disabled={!editable || i === 0}
                      onClick={() => move(i, -1)}>↑</button>
                    <button className="btn" disabled={!editable || i === draft.length - 1}
                      onClick={() => move(i, 1)}>↓</button>
                    <button className="btn btn-danger" disabled={!editable}
                      onClick={() => remove(i)}>✕</button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        <div className="row-actions" style={{ marginTop: "0.6rem" }}>
          <button className="btn" disabled={!editable} onClick={add}>+ add clip</button>
          <button className="btn btn-primary" disabled={!editable || saving} onClick={save}>
            {saving ? "saving…" : "save plan (re-snaps)"}
          </button>
          <button className="btn" disabled={!editable}
            onClick={() => {
              setDraft(job.clips.map((clip) => ({ ...clip })));
              setSnapped(false);
            }}>
            reset to stored plan
          </button>
        </div>
        <div className="help">
          Typed seconds are semantic guesses — the server snaps them onto Whisper word
          boundaries on save. Titles become the output filenames (ASCII-slugified).
        </div>
      </div>
    </div>
  );
}
