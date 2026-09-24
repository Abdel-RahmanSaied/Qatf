import { useState } from "react";
import { ApiError, putPlan } from "../api/client";
import { RUNNING_STATES } from "../api/types";
import type { ClipModel, JobResponse } from "../api/types";
import { Icon } from "./Icon";
import { useToast } from "./Toasts";
import { useI18n } from "../i18n/I18nProvider";
import "./PlanEditor.css";
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
  const { t } = useI18n();
  const p = t.plan;

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
        title: p.newTitle, hook: "", why: "", score: 0,
      }];
    });
  };

  async function save() {
    for (const [i, clip] of draft.entries()) {
      if (clip.end <= clip.start) {
        push(p.toastOrder(i + 1));
        return;
      }
    }
    if (draft.length === 0) {
      push(p.toastEmpty);
      return;
    }
    setSaving(true);
    try {
      const stored = await putPlan(jobId, draft);
      setDraft(stored.map((clip) => ({ ...clip })));
      setSnapped(true);
      push(p.toastSaved, "ok");
      await onSaved();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    } finally {
      setSaving(false);
    }
  }

  const maxLen = job.options.max_len;
  // `durationWarning` decides WHETHER to warn — it mirrors the server's rule —
  // but writes developer English, so the words shown come from the dictionary.
  const warningText = (clip: ClipModel) => {
    if (!durationWarning(clip, maxLen)) return null;
    return clip.end - clip.start <= 0 ? p.endBeforeStart : p.tooLong(maxLen);
  };
  const warnings = draft.reduce((n, clip) => n + (warningText(clip) ? 1 : 0), 0);
  const picked = draft.reduce((total, clip) => total + Math.max(0, clip.end - clip.start), 0);

  return (
    <div className="card">
      <div className="card-head">
        <h2 className="card-title">{p.title}</h2>
        <p className="card-sub">{p.sub}</p>
      </div>

      {!editable && (
        <div className="banner banner-warn">
          <Icon name="clock" />
          <span>{p.locked}</span>
        </div>
      )}
      {snapped && (
        <div className="banner banner-info" role="status">
          <Icon name="check" />
          <span>{p.snapped}</span>
        </div>
      )}

      <div className="card-body">
        <table className="plan">
          <thead>
            <tr>
              <th>#</th>
              <th>{p.colStart}</th>
              <th>{p.colEnd}</th>
              <th>{p.colLength}</th>
              <th>{p.colTitle}</th>
              <th title={p.colScoreTip}>{p.colScore}</th>
              <th><span className="visually-hidden">{p.colActions}</span></th>
            </tr>
          </thead>
          <tbody>
            {draft.map((clip, i) => {
              const warning = warningText(clip);
              return (
                <tr key={i}>
                  <td className="plan-idx mono">{String(i + 1).padStart(2, "0")}</td>
                  <td className="plan-num">
                    <input type="number" lang="en" step={0.01} min={0} value={clip.start} dir="ltr"
                      aria-label={p.startAria(i + 1)}
                      disabled={!editable}
                      onChange={(e) => update(i, { start: Number(e.target.value) })} />
                  </td>
                  <td className="plan-num">
                    <input type="number" lang="en" step={0.01} min={0} value={clip.end} dir="ltr"
                      aria-label={p.endAria(i + 1)}
                      disabled={!editable}
                      onChange={(e) => update(i, { end: Number(e.target.value) })} />
                  </td>
                  <td className="plan-num">
                    {formatSeconds(Math.max(0, clip.end - clip.start))}
                    {warning && <div className="plan-warn">{warning}</div>}
                  </td>
                  <td>
                    <input value={clip.title} dir="auto" disabled={!editable}
                      aria-label={p.titleAria(i + 1)}
                      title={clip.hook ? `${clip.hook}
${clip.why}` : undefined}
                      onChange={(e) => update(i, { title: e.target.value })} />
                  </td>
                  <td className="plan-score mono" title={p.colScoreTip}>{clip.score.toFixed(2)}</td>
                  <td>
                    <div className="row row-tight">
                      <button className="btn btn-sm btn-icon" disabled={!editable || i === 0}
                        aria-label={p.up(i + 1)} title={p.up(i + 1)}
                        onClick={() => move(i, -1)}><Icon name="arrowUp" size={14} /></button>
                      <button className="btn btn-sm btn-icon" disabled={!editable || i === draft.length - 1}
                        aria-label={p.down(i + 1)} title={p.down(i + 1)}
                        onClick={() => move(i, 1)}><Icon name="arrowDown" size={14} /></button>
                      <button className="btn btn-sm btn-icon btn-danger" disabled={!editable}
                        aria-label={p.remove(i + 1)} title={p.remove(i + 1)}
                        onClick={() => remove(i)}><Icon name="trash" size={14} /></button>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <p className="field-help">{p.help}</p>

      <div className="sticky-bar">
        <div className={`sticky-bar-status ${warnings > 0 ? "is-error" : ""}`}>
          <span className="status-dot" aria-hidden="true" />
          <span>
            {draft.length === 0
              ? p.empty
              : <>
                  {p.status(draft.length, formatSeconds(picked))}
                  {warnings > 0 && p.statusLong(warnings, maxLen)}
                </>}
          </span>
        </div>
        <div className="sticky-bar-actions">
          <button className="btn btn-ghost" disabled={!editable}
            onClick={() => {
              setDraft(job.clips.map((clip) => ({ ...clip })));
              setSnapped(false);
            }}>
            {p.undo}
          </button>
          <button className="btn" disabled={!editable} onClick={add}>
            <Icon name="plus" size={16} />
            {p.add}
          </button>
          <button className="btn btn-primary" disabled={!editable || saving} onClick={save}>
            {saving ? t.common.saving : p.save}
          </button>
        </div>
      </div>
    </div>
  );
}
