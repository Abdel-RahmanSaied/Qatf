import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, cancelJob, deleteJob, listJobs } from "../api/client";
import { usePolling } from "../api/poll";
import { JOB_STATES, TERMINAL_STATES } from "../api/types";
import type { JobResponse, JobState } from "../api/types";
import { HarvestStrip } from "../components/HarvestStrip";
import { HealthBanner } from "../components/HealthBanner";
import { StateBadge } from "../components/StateBadge";
import { Thumb } from "../components/Thumb";
import { useToast } from "../components/Toasts";
import { formatAge } from "../lib/format";

/** The API refuses both of these, so the UI must not offer them:
 * cancelling a job that has already stopped moving, or deleting one that
 * has not. `planned` counts as stopped — the worker is waiting on a render. */
function canCancel(state: JobState): boolean {
  return !TERMINAL_STATES.has(state) && state !== "planned";
}

function canDelete(state: JobState): boolean {
  return TERMINAL_STATES.has(state) || state === "planned";
}

export default function JobsList() {
  // The FULL list, unfiltered, is what gets polled. Filtering happens below in
  // the browser: the chip counts have to describe every job, and asking the
  // server per state would cost one request per chip per tick to say it.
  const [jobs, setJobs] = useState<JobResponse[] | null>(null);
  const [filter, setFilter] = useState<JobState | "">("");
  const [unreachable, setUnreachable] = useState(false);
  const { push } = useToast();

  const refresh = useCallback(async () => {
    try {
      setJobs(await listJobs());
      setUnreachable(false);
    } catch {
      setUnreachable(true); // banner, not a toast per tick
    }
  }, []);
  usePolling(refresh, 3000);

  const counts = useMemo(() => {
    const tally = new Map<JobState, number>();
    for (const job of jobs ?? []) tally.set(job.state, (tally.get(job.state) ?? 0) + 1);
    return tally;
  }, [jobs]);

  // A state with no jobs gets no chip — except the one currently selected, which
  // stays so a list that empties out under polling still explains itself.
  const chipStates = useMemo(
    () => JOB_STATES.filter((s) => (counts.get(s) ?? 0) > 0 || s === filter),
    [counts, filter],
  );

  const visible = useMemo(
    () => (jobs ?? []).filter((job) => filter === "" || job.state === filter),
    [jobs, filter],
  );

  async function onCancel(id: string) {
    try {
      await cancelJob(id);
      push(`Job ${id} cancelled.`, "ok");
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onDelete(id: string) {
    if (!window.confirm(`Delete job ${id} and all its files?`)) return;
    try {
      await deleteJob(id);
      push(`Job ${id} deleted.`, "ok");
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  return (
    <div className="page">
      {/* No "New job" button here: the top bar already carries one, and two
          identical primary buttons a few pixels apart read as two different
          actions. The empty state below still offers one, where it is the
          only thing to do. */}
      <div className="page-head spread">
        <h1 className="page-title">Jobs</h1>
      </div>

      <HealthBanner />
      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying every 3 seconds.</div>
      )}

      {jobs !== null && jobs.length > 0 && (
        <div className="chips" role="group" aria-label="Filter jobs by state">
          <button
            type="button"
            className={filter === "" ? "chip active" : "chip"}
            aria-pressed={filter === ""}
            onClick={() => setFilter("")}
          >
            All <span className="chip-count">{jobs.length}</span>
          </button>
          {chipStates.map((s) => (
            <button
              key={s}
              type="button"
              className={filter === s ? "chip active" : "chip"}
              aria-pressed={filter === s}
              onClick={() => setFilter(s)}
            >
              {s} <span className="chip-count">{counts.get(s) ?? 0}</span>
            </button>
          ))}
        </div>
      )}

      {jobs === null && (
        <div aria-busy="true" aria-label="Loading jobs">
          <div className="skeleton-row" />
          <div className="skeleton-row" />
          <div className="skeleton-row" />
        </div>
      )}

      {jobs !== null && jobs.length === 0 && (
        <div className="empty">
          <p className="empty-title">No jobs yet</p>
          <p className="empty-body">
            A job takes one long video, transcribes it, picks the passages worth
            keeping, and renders them as vertical clips with burned-in captions.
          </p>
          <Link className="btn btn-primary" to="/new">Start a job</Link>
        </div>
      )}

      {jobs !== null && jobs.length > 0 && visible.length === 0 && (
        <div className="empty">
          <p className="empty-title">No {filter} jobs</p>
          <p className="empty-body">
            {jobs.length} job{jobs.length === 1 ? "" : "s"} exist in other states.
          </p>
          <button type="button" className="btn" onClick={() => setFilter("")}>
            Show all jobs
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <div className="job-list">
          {visible.map((job) => (
            <div className="job-row" key={job.id}>
              <Thumb outputs={job.outputs} state={job.state} />

              <div className="job-main">
                <div className="row spread">
                  <Link to={`/jobs/${job.id}`} className="job-id">{job.id}</Link>
                  <StateBadge state={job.state} />
                </div>

                <div className="job-meta">
                  <span className="tnum">{formatAge(job.created_at)}</span>
                  {job.outputs.length > 0 && (
                    <span>{job.outputs.length} clip{job.outputs.length === 1 ? "" : "s"} rendered</span>
                  )}
                  {job.outputs.length === 0 && job.clips.length > 0 && (
                    <span>{job.clips.length} clip{job.clips.length === 1 ? "" : "s"} planned</span>
                  )}
                  {job.language && <span>{job.language}</span>}
                  {job.device && <span>{job.device}</span>}
                  {job.word_count > 0 && (
                    <span className="tnum">{job.word_count.toLocaleString()} words</span>
                  )}
                </div>

                {(job.error ?? job.message) && (
                  <div className="job-msg" title={job.error ?? job.message}>
                    {job.error ?? job.message}
                  </div>
                )}

                <HarvestStrip clips={job.clips} mini />
              </div>

              <div className="job-actions">
                {canCancel(job.state) && (
                  <button
                    type="button"
                    className="btn btn-sm"
                    onClick={() => onCancel(job.id)}
                  >
                    Cancel
                  </button>
                )}
                <button
                  type="button"
                  className="btn btn-sm btn-danger"
                  disabled={!canDelete(job.state)}
                  title={
                    canDelete(job.state)
                      ? "Delete the job and every file it wrote"
                      : "Cancel the job first — the server refuses to delete one that is still running"
                  }
                  onClick={() => onDelete(job.id)}
                >
                  Delete
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
