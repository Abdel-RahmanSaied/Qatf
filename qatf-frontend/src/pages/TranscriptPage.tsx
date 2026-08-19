import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getJob } from "../api/client";
import { usePolling } from "../api/poll";
import { TERMINAL_STATES } from "../api/types";
import type { JobResponse } from "../api/types";
import { StateBadge } from "../components/StateBadge";
import { TranscriptEditor } from "../components/TranscriptEditor";

/** The transcript gets its own page: a 27,000-word transcript is not a panel on
 * another screen. The job record carries header context and the two gates the
 * editor reads off it — word count and running state.
 *
 * The record is POLLED, not fetched once. The editor refuses to save while the
 * job is running, and that gate is derived from this record; a one-shot fetch
 * froze it at page-load time, so opening the page mid-render left Save disabled
 * forever and only a manual reload brought it back. Polling stops the moment
 * the job is terminal — there is nothing left to learn. */
export default function TranscriptPage() {
  const { id } = useParams<{ id: string }>();
  const [job, setJob] = useState<JobResponse | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!id) return;
    try {
      setJob(await getJob(id));
      setProblem(null);
    } catch (exc) {
      setProblem(exc instanceof ApiError ? exc.message : String(exc));
    }
  }, [id]);

  // Hoisted above every early return: hooks may not sit behind a branch.
  usePolling(reload, job !== null && TERMINAL_STATES.has(job.state) ? null : 5000);

  if (problem) {
    return (
      <div className="page">
        <div className="page-head">
          <Link to="/" className="back-link">← Jobs</Link>
          <h1 className="page-title">Transcript</h1>
        </div>
        <div className="banner banner-error">{problem}</div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="page">
        <div className="skeleton-row" aria-label="Loading the job" />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="page-head">
        <Link to={`/jobs/${job.id}`} className="back-link">← Back to the job</Link>
        <h1 className="page-title mono">{job.id}</h1>
        <StateBadge state={job.state} />
      </div>

      {job.word_count === 0 ? (
        <div className="empty">
          <p className="empty-title">No transcript yet</p>
          <p className="empty-body">
            This job has not reached stage 2. Watch its progress on the job page
            and come back once the words are in.
          </p>
          <Link to={`/jobs/${job.id}`} className="btn">Back to the job</Link>
        </div>
      ) : (
        <TranscriptEditor jobId={job.id} job={job} />
      )}
    </div>
  );
}
