import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, cancelJob, getJob, renderJob } from "../api/client";
import { usePolling } from "../api/poll";
import { RUNNING_STATES, TERMINAL_STATES } from "../api/types";
import type { JobResponse } from "../api/types";
import { ClipGrid } from "../components/ClipGrid";
import { RangeNotice } from "../components/RangeNotice";
import { FetchProgress } from "../components/FetchProgress";
import { HarvestStrip } from "../components/HarvestStrip";
import { StageTimeline } from "../components/StageTimeline";
import { StateBadge } from "../components/StateBadge";
import { PlanEditor } from "../components/PlanEditor";
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
    return (
      <div className="page">
        <div className="empty">
          <p className="empty-title">No such job</p>
          <p className="empty-body">
            It was deleted, or the id in the address is wrong.
          </p>
          <Link to="/" className="btn">Back to jobs</Link>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="page">
        <div className="skeleton-row" />
      </div>
    );
  }

  const running = RUNNING_STATES.has(job.state);
  const canRender = job.clips.length > 0 && !running;

  return (
    <div className="page">
      <div className="page-head">
        <Link to="/" className="back-link">← Jobs</Link>
        <h1 className="page-title mono">{job.id}</h1>
        <StateBadge state={job.state} />
        <div className="row">
          {running && <button className="btn" onClick={onCancel}>Cancel job</button>}
          {canRender && (
            <button
              className="btn btn-primary"
              onClick={onRender}
              title="Encodes the current plan. Any previous outputs are replaced."
            >
              {job.outputs.length > 0 ? "Re-render clips" : "Render clips"}
            </button>
          )}
        </div>
      </div>

      {unreachable && (
        <div className="banner banner-error">Connection lost — retrying.</div>
      )}

      <StageTimeline state={job.state} source={job.source} />

      {job.state === "failed" && (
        <div className="banner banner-error">{job.error ?? "The job failed."}</div>
      )}
      {job.state !== "failed" && job.message && (
        <p className="muted">{job.message}</p>
      )}

      {/* Directly under the message it quantifies. Only while state=fetching:
          afterwards the record still holds the closing reading, but a finished
          download reported as live progress is the timeline lying, and the
          state machine has already moved on. */}
      {job.state === "fetching" && <FetchProgress progress={job.fetch_progress} />}

      {/* Above the clips, because it qualifies every one of them. A viewer who
          has already scrolled into the grid is not coming back up to find out
          that two of what they are looking at are 24 seconds long. */}
      <RangeNotice clips={job.clips} options={job.options} />

      {/* CLIPS FIRST. They are what the pipeline exists to produce, so nothing
          — least of all a six-row provenance table — goes above them. The meta
          card sits at the bottom with the other secondary blocks. */}
      {job.outputs.length > 0 && (
        <>
          <h2>
            {job.outputs.length} clip{job.outputs.length === 1 ? "" : "s"}
            {job.state === "rendering" ? " — still rendering" : ""}
          </h2>
          {canRender && (
            <p className="muted">Re-rendering replaces every clip below.</p>
          )}
          <ClipGrid outputs={job.outputs} clips={job.clips} />
        </>
      )}

      {job.clips.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2 className="card-title">Where the picks sit</h2>
            <p className="card-sub">
              Spans within the picked range — the source may run longer.
            </p>
          </div>
          <HarvestStrip clips={job.clips} />
        </div>
      )}

      {/* The key forces exactly ONE remount: the transition from "no plan yet"
          to "a plan arrived", which is when the editor's lazily-seeded draft
          must be re-seeded. Keying on the clip COUNT remounted it on every save
          that added or removed a clip too, which threw away the "these are the
          snapped boundaries" notice at the moment it matters most. */}
      <PlanEditor
        key={`${job.id}-${job.clips.length === 0 ? "empty" : "seeded"}`}
        jobId={job.id}
        job={job}
        onSaved={reload}
      />

      {job.word_count > 0 && (
        <div className="card">
          <div className="card-head">
            <h2 className="card-title">Transcript</h2>
            <p className="card-sub">
              <span className="tnum">{job.word_count}</span> words
              {job.language ? ` · ${job.language}` : ""}
              {job.transcript_cached ? " · cached" : ""}
            </p>
          </div>
          <p className="muted">
            Correct a misheard word and the captions change. Timings never move,
            so a correction can never shift a cut.
          </p>
          <Link className="btn" to={`/jobs/${job.id}/transcript`}>Edit transcript</Link>
        </div>
      )}

      <div className="card">
        <div className="card-head">
          <h2 className="card-title">Provenance</h2>
          <p className="card-sub">Where this job came from and what ran it.</p>
        </div>
        <dl className="dl">
          <dt>Source</dt>
          <dd>
            {job.source}
            {job.source === "youtube" && job.url
              ? <> · <span className="mono">{job.url}</span></>
              : null}
          </dd>

          <dt>Transcribed on</dt>
          <dd>{job.device ?? "not transcribed yet"}</dd>

          <dt>Language</dt>
          <dd>{job.language ?? "not detected yet"}</dd>

          <dt>Words</dt>
          <dd>
            {job.word_count > 0
              ? <><span className="tnum">{job.word_count}</span>
                  {job.transcript_cached ? " (from the transcript cache)" : ""}</>
              : "no transcript yet"}
          </dd>

          <dt>Created</dt>
          <dd>{formatAge(job.created_at)}</dd>

          <dt>Updated</dt>
          <dd>{formatAge(job.updated_at)}</dd>
        </dl>
      </div>
    </div>
  );
}
