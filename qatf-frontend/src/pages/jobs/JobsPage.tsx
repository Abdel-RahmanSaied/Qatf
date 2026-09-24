import { useCallback, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, cancelJob, deleteJob, listJobs } from "../../api/client";
import { usePolling } from "../../api/poll";
import { JOB_STATES, RUNNING_STATES, TERMINAL_STATES } from "../../api/types";
import type { JobResponse, JobState } from "../../api/types";
import { HarvestStrip } from "../../components/HarvestStrip";
import { HealthBanner } from "../../components/HealthBanner";
import { Icon } from "../../components/Icon";
import { PageHeader } from "../../components/PageHeader";
import { StateBadge } from "../../components/StateBadge";
import { Thumb } from "../../components/Thumb";
import { useToast } from "../../components/Toasts";
import { useI18n } from "../../i18n/I18nProvider";
import { videoName } from "../../lib/display";
import "./JobsPage.css";

/** The API refuses both of these, so the UI must not offer them:
 * cancelling a job that has already stopped moving, or deleting one that
 * has not. `planned` counts as stopped — the worker is waiting on a render. */
function canCancel(state: JobState): boolean {
  return !TERMINAL_STATES.has(state) && state !== "planned";
}

function canDelete(state: JobState): boolean {
  return TERMINAL_STATES.has(state) || state === "planned";
}

export default function JobsPage() {
  const { t, ago, languageName } = useI18n();
  const tj = t.jobs;
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

  // The overview row. "In progress" is RUNNING_STATES, the shared definition,
  // not a hand-rolled list — see the note on RUNNING_STATES in api/types.ts.
  const stats = useMemo(() => {
    const all = jobs ?? [];
    return {
      working: all.filter((job) => RUNNING_STATES.has(job.state)).length,
      review: counts.get("planned") ?? 0,
      done: counts.get("done") ?? 0,
      clips: all.reduce((n, job) => n + job.outputs.length, 0),
    };
  }, [jobs, counts]);

  async function onCancel(id: string) {
    try {
      await cancelJob(id);
      push(tj.stopped, "ok");
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  async function onDelete(job: JobResponse) {
    if (!window.confirm(tj.confirmDelete(videoName(job)))) return;
    try {
      await deleteJob(job.id);
      push(tj.deleted, "ok");
      await refresh();
    } catch (exc) {
      push(exc instanceof ApiError ? exc.message : String(exc));
    }
  }

  return (
    <div className="page">
      {/* No "Add video" button here: the sidebar already carries one, and two
          identical primary buttons a few pixels apart read as two different
          actions. The empty state below still offers one, where it is the
          only thing to do. */}
      <PageHeader eyebrow={tj.eyebrow} title={tj.title} lede={tj.lede} />

      <HealthBanner />
      {unreachable && (
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <span>{t.common.connectionLost}</span>
        </div>
      )}

      {jobs !== null && jobs.length > 0 && (
        <dl className="stats">
          <div className="stat">
            <dt className="stat-label">{tj.statWorking}</dt>
            <dd className="stat-value tnum">{stats.working}</dd>
          </div>
          <div className={stats.review > 0 ? "stat is-hot" : "stat"}>
            <dt className="stat-label">{tj.statReview}</dt>
            <dd className="stat-value tnum">{stats.review}</dd>
          </div>
          <div className="stat">
            <dt className="stat-label">{tj.statDone}</dt>
            <dd className="stat-value tnum">{stats.done}</dd>
          </div>
          <div className="stat">
            <dt className="stat-label">{tj.statClips}</dt>
            <dd className="stat-value tnum">{stats.clips}</dd>
          </div>
        </dl>
      )}

      {jobs !== null && jobs.length > 0 && (
        <div className="chips" role="group" aria-label={tj.filterLabel}>
          <button
            type="button"
            className={filter === "" ? "chip active" : "chip"}
            aria-pressed={filter === ""}
            onClick={() => setFilter("")}
          >
            {tj.all} <span className="chip-count">{jobs.length}</span>
          </button>
          {chipStates.map((s) => (
            <button
              key={s}
              type="button"
              className={filter === s ? "chip active" : "chip"}
              aria-pressed={filter === s}
              onClick={() => setFilter(s)}
            >
              {t.state[s]} <span className="chip-count">{counts.get(s) ?? 0}</span>
            </button>
          ))}
        </div>
      )}

      {jobs === null && (
        <div className="job-list" aria-busy="true" aria-label={tj.title}>
          <div className="skeleton-row" />
          <div className="skeleton-row" />
          <div className="skeleton-row" />
        </div>
      )}

      {jobs !== null && jobs.length === 0 && (
        <div className="empty empty-hero">
          <span className="empty-icon" aria-hidden="true">
            <Icon name="scissors" size={28} />
          </span>
          <p className="empty-title">{tj.emptyTitle}</p>
          <p className="empty-body">{tj.emptyBody}</p>
          <Link className="btn btn-primary btn-lg" to="/new">
            <Icon name="plus" />
            {tj.emptyCta}
          </Link>
        </div>
      )}

      {jobs !== null && jobs.length > 0 && visible.length === 0 && filter !== "" && (
        <div className="empty">
          <p className="empty-title">{tj.noneTitle(t.state[filter])}</p>
          <p className="empty-body">{tj.noneBody(jobs.length)}</p>
          <button type="button" className="btn" onClick={() => setFilter("")}>
            {tj.showAll}
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <ul className="job-list">
          {visible.map((job) => {
            const name = videoName(job);
            return (
              <li className="job-row" key={job.id}>
                <Thumb outputs={job.outputs} state={job.state} />

                <div className="job-main">
                  <div className="job-top">
                    {/* The link stretches over the whole card (.job-link::after),
                        so a click anywhere opens the job — while the actions sit
                        above that layer and keep their own clicks. */}
                    <Link to={`/jobs/${job.id}`} className="job-link" dir="auto"
                      aria-label={tj.open(name)}>
                      {name}
                    </Link>
                    <StateBadge state={job.state} />
                  </div>

                  <div className="job-meta">
                    <span>{ago(job.created_at)}</span>
                    {job.outputs.length > 0 && <span>{tj.clipsMade(job.outputs.length)}</span>}
                    {job.outputs.length === 0 && job.clips.length > 0 && (
                      <span>{tj.clipsPlanned(job.clips.length)}</span>
                    )}
                    {job.language && <span>{languageName(job.language)}</span>}
                    {job.word_count > 0 && <span>{t.common.words(job.word_count)}</span>}
                    {name !== job.id && <span className="mono job-id">{job.id}</span>}
                  </div>

                  {(job.error ?? job.message) && (
                    <div
                      className={job.error ? "job-msg is-error" : "job-msg"}
                      dir="auto"
                      title={job.error ?? job.message}
                    >
                      {job.error ?? job.message}
                    </div>
                  )}

                  <HarvestStrip clips={job.clips} mini />
                </div>

                <div className="job-actions">
                  {job.state === "planned" && <span className="job-cue">{tj.needsReview}</span>}
                  {canCancel(job.state) && (
                    <button type="button" className="btn btn-sm" onClick={() => onCancel(job.id)}>
                      <Icon name="pause" size={14} />
                      {t.common.stop}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-sm btn-icon btn-danger"
                    disabled={!canDelete(job.state)}
                    aria-label={`${t.common.delete}: ${name}`}
                    title={canDelete(job.state) ? tj.deleteTitle : tj.deleteBlocked}
                    onClick={() => onDelete(job)}
                  >
                    <Icon name="trash" size={15} />
                  </button>
                  <Icon name="arrowRight" className="job-go" />
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
