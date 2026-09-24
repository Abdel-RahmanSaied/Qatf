import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, getJob } from "../../api/client";
import { usePolling } from "../../api/poll";
import { TERMINAL_STATES } from "../../api/types";
import type { JobResponse } from "../../api/types";
import { Icon } from "../../components/Icon";
import { PageHeader } from "../../components/PageHeader";
import { StateBadge } from "../../components/StateBadge";
import { TranscriptEditor } from "../../components/TranscriptEditor";
import { useI18n } from "../../i18n/I18nProvider";
import { videoName } from "../../lib/display";
import "./TranscriptPage.css";

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
  const { t } = useI18n();
  const tx = t.transcript;
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
        <PageHeader
          eyebrow={
            <nav className="crumbs" aria-label="Breadcrumb">
              <Link to="/" className="crumb-link">
                <Icon name="arrowLeft" size={14} />
                {t.app.navVideos}
              </Link>
            </nav>
          }
          title={tx.title}
        />
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <span>{problem}</span>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="page" aria-busy="true">
        <div className="skeleton-title" />
        <div className="skeleton-row" />
      </div>
    );
  }

  const name = videoName(job);

  return (
    <div className="page">
      <PageHeader
        eyebrow={
          <nav className="crumbs" aria-label="Breadcrumb">
            <Link to="/" className="crumb-link">{t.app.navVideos}</Link>
            <span className="crumb-sep" aria-hidden="true">/</span>
            <Link to={`/jobs/${job.id}`} className="crumb-link crumb-name" dir="auto">{name}</Link>
            <span className="crumb-sep" aria-hidden="true">/</span>
            <span aria-current="page">{tx.eyebrowText}</span>
          </nav>
        }
        title={tx.title}
        aside={<StateBadge state={job.state} />}
        lede={tx.lede}
      />

      {job.word_count === 0 ? (
        <div className="empty">
          <p className="empty-title">{tx.noneTitle}</p>
          <p className="empty-body">{tx.noneBody}</p>
          <Link to={`/jobs/${job.id}`} className="btn">{tx.backToVideo}</Link>
        </div>
      ) : (
        <TranscriptEditor jobId={job.id} job={job} />
      )}
    </div>
  );
}
