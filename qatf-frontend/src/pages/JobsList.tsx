import { useCallback, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, cancelJob, deleteJob, listJobs } from "../api/client";
import { usePolling } from "../api/poll";
import { JOB_STATES, TERMINAL_STATES } from "../api/types";
import type { JobResponse, JobState } from "../api/types";
import { HealthBanner } from "../components/HealthBanner";
import { StateBadge } from "../components/StateBadge";
import { useToast } from "../components/Toasts";
import { formatAge } from "../lib/format";

export default function JobsList() {
  const [jobs, setJobs] = useState<JobResponse[] | null>(null);
  const [filter, setFilter] = useState<JobState | "">("");
  const [unreachable, setUnreachable] = useState(false);
  const { push } = useToast();

  const refresh = useCallback(async () => {
    try {
      setJobs(await listJobs(filter || undefined));
      setUnreachable(false);
    } catch {
      setUnreachable(true); // banner, not a toast per tick
    }
  }, [filter]);
  usePolling(refresh, 3000);

  async function onCancel(id: string) {
    try {
      await cancelJob(id);
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onDelete(id: string) {
    if (!window.confirm(`Delete job ${id} and all its files?`)) return;
    try {
      await deleteJob(id);
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  return (
    <div>
      <h1>Jobs</h1>
      <HealthBanner />
      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying.</div>
      )}
      <label htmlFor="state-filter">Filter by state</label>
      <select
        id="state-filter"
        style={{ maxWidth: "14rem" }}
        value={filter}
        onChange={(e) => setFilter(e.target.value as JobState | "")}
      >
        <option value="">all</option>
        {JOB_STATES.map((s) => <option key={s} value={s}>{s}</option>)}
      </select>
      {jobs && jobs.length === 0 && (
        <p className="muted">No jobs yet. <Link to="/new">Start one.</Link></p>
      )}
      {jobs && jobs.length > 0 && (
        <table>
          <thead>
            <tr><th>id</th><th>state</th><th>progress</th><th>created</th><th></th></tr>
          </thead>
          <tbody>
            {jobs.map((job) => (
              <tr key={job.id}>
                <td><Link to={`/jobs/${job.id}`} className="mono">{job.id}</Link></td>
                <td><StateBadge state={job.state} /></td>
                <td className="muted">{job.error ?? job.message}</td>
                <td className="muted">{formatAge(job.created_at)}</td>
                <td className="row-actions">
                  {!TERMINAL_STATES.has(job.state) && job.state !== "planned" && (
                    <button className="btn" onClick={() => onCancel(job.id)}>cancel</button>
                  )}
                  <button
                    className="btn btn-danger"
                    disabled={!TERMINAL_STATES.has(job.state) && job.state !== "planned"}
                    title="the API refuses deletion while a job is running"
                    onClick={() => onDelete(job.id)}
                  >
                    delete
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
