import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, cancelJob, getJob, renderJob } from "../api/client";
import { usePolling } from "../api/poll";
import { TERMINAL_STATES } from "../api/types";
import type { JobResponse } from "../api/types";
import { ClipGrid } from "../components/ClipGrid";
import { StateBadge } from "../components/StateBadge";
import { TranscriptEditor } from "../components/TranscriptEditor";
import { useToast } from "../components/Toasts";
import { formatAge } from "../lib/format";

export default function JobDetail() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [missing, setMissing] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  const { push } = useToast();

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setJob(await getJob(id));
      setUnreachable(false);
    } catch (exc) {
      if (exc instanceof ApiError && exc.status === 404) setMissing(true);
      else setUnreachable(true);
    }
  }, [id]);

  // 2s while working, 10s parked at planned, stopped once terminal.
  const interval =
    job === null ? 2000 :
    TERMINAL_STATES.has(job.state) ? null :
    job.state === "planned" ? 10_000 : 2000;
  usePolling(reload, missing ? null : interval);

  async function onRender() {
    if (!id) return;
    try {
      setJob(await renderJob(id)); // 202 -> queued; polling resumes automatically
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onCancel() {
    if (!id) return;
    try {
      setJob(await cancelJob(id));
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  if (missing) {
    return <p>No such job. <Link to="/">Back to jobs.</Link></p>;
  }
  if (!job) return <p className="muted">loading…</p>;

  const running = !TERMINAL_STATES.has(job.state) && job.state !== "planned";
  const canRender = job.clips.length > 0 && !running;

  return (
    <div>
      <h1>
        <span className="mono">{job.id}</span> <StateBadge state={job.state} />
      </h1>
      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying.</div>
      )}
      <div className="panel">
        <div>{job.error ?? job.message}</div>
        <div className="muted">
          source: {job.source}{job.url ? <> · <span className="mono">{job.url}</span></> : null}
          {" · created "}{formatAge(job.created_at)}
          {" · updated "}{formatAge(job.updated_at)}
        </div>
        <div className="muted">
          {job.language ? `language: ${job.language} · ` : ""}
          {job.device ? `transcribed on: ${job.device} · ` : ""}
          {job.word_count > 0 ? `${job.word_count} words` : "no transcript yet"}
          {job.transcript_cached ? " (cached)" : ""}
        </div>
        <div className="row-actions" style={{ marginTop: "0.6rem" }}>
          {running && <button className="btn" onClick={onCancel}>cancel</button>}
          {canRender && (
            <button className="btn btn-primary" onClick={onRender}
              title="encodes the current plan; replaces any previous outputs">
              {job.outputs.length > 0 ? "re-render" : "render"}
            </button>
          )}
        </div>
        {canRender && job.outputs.length > 0 && (
          <div className="help">Re-rendering replaces the previous outputs.</div>
        )}
      </div>

      {/* PlanEditor mounts here — Task 9 */}

      <TranscriptEditor jobId={job.id} job={job} />

      {job.outputs.length > 0 && (
        <>
          <h2>clips {job.state === "rendering" ? "(rendering…)" : ""}</h2>
          <ClipGrid outputs={job.outputs} />
        </>
      )}
    </div>
  );
}
