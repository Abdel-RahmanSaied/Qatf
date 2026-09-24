import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ApiError, cancelJob, getJob, renderJob } from "../../api/client";
import { usePolling } from "../../api/poll";
import { RUNNING_STATES, TERMINAL_STATES } from "../../api/types";
import type { JobResponse } from "../../api/types";
import { ClipGrid } from "../../components/ClipGrid";
import { FetchProgress } from "../../components/FetchProgress";
import { HarvestStrip } from "../../components/HarvestStrip";
import { Icon } from "../../components/Icon";
import { PageHeader } from "../../components/PageHeader";
import { PlanEditor } from "../../components/PlanEditor";
import { RangeNotice } from "../../components/RangeNotice";
import { StageTimeline } from "../../components/StageTimeline";
import { StateBadge } from "../../components/StateBadge";
import { useToast } from "../../components/Toasts";
import { useI18n } from "../../i18n/I18nProvider";
import { videoName } from "../../lib/display";
import "./JobPage.css";

function Crumb() {
  const { t } = useI18n();
  return (
    <nav className="crumbs" aria-label="Breadcrumb">
      <Link to="/" className="crumb-link">
        <Icon name="arrowLeft" size={14} />
        {t.app.navVideos}
      </Link>
    </nav>
  );
}

export default function JobPage() {
  const { t, ago, languageName } = useI18n();
  const tj = t.job;
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
          <p className="empty-title">{tj.notFoundTitle}</p>
          <p className="empty-body">{tj.notFoundBody}</p>
          <Link to="/" className="btn">{t.common.back}</Link>
        </div>
      </div>
    );
  }

  if (!job) {
    return (
      <div className="page" aria-busy="true">
        <Crumb />
        <div className="skeleton-title" />
        <div className="skeleton-row" />
      </div>
    );
  }

  const running = RUNNING_STATES.has(job.state);
  const canRender = job.clips.length > 0 && !running;
  // The one moment the job is waiting on the READER rather than the server:
  // a plan exists and nothing has been rendered from it yet.
  const awaitingReview = job.state === "planned" && job.outputs.length === 0;
  const name = videoName(job);
  const language = languageName(job.language);

  return (
    <div className="page">
      <PageHeader
        eyebrow={<Crumb />}
        title={<span dir="auto">{name}</span>}
        aside={<StateBadge state={job.state} />}
        lede={name !== job.id ? <span className="mono job-page-id">{job.id}</span> : undefined}
        actions={
          <>
            {running && (
              <button className="btn" onClick={onCancel}>
                <Icon name="pause" size={16} />
                {tj.stop}
              </button>
            )}
            {canRender && !awaitingReview && (
              <button className="btn btn-primary" onClick={onRender} title={tj.makeTip}>
                <Icon name="film" size={16} />
                {job.outputs.length > 0 ? tj.remake : tj.make}
              </button>
            )}
          </>
        }
      />

      {unreachable && (
        <div className="banner banner-error" role="alert">
          <Icon name="alert" />
          <span>{t.common.connectionLost}</span>
        </div>
      )}

      <section className="status-panel" aria-label={t.stage.aria}>
        <StageTimeline state={job.state} source={job.source} />

        {job.state === "failed" && (
          <div className="banner banner-error" role="alert">
            <Icon name="alert" />
            <span dir="auto">{job.error ?? tj.failed}</span>
          </div>
        )}
        {/* The server's own progress line stays visible (it carries the live
            percentage), under a plain-language name for the current step. */}
        {job.state !== "failed" && (
          <p className="status-message">
            <strong>{t.state[job.state]}</strong>
            {job.message && <span className="status-detail" dir="auto">{job.message}</span>}
          </p>
        )}

        {/* Directly under the message it quantifies. Only while state=fetching:
            afterwards the record still holds the closing reading, but a finished
            download reported as live progress is the timeline lying, and the
            state machine has already moved on. */}
        {job.state === "fetching" && <FetchProgress progress={job.fetch_progress} />}
      </section>

      {/* The hand-off. The job is waiting on the reader, so this says so and
          puts the one action that moves it forward right next to the words. */}
      {awaitingReview && (
        <div className="callout">
          <div className="callout-body">
            <p className="callout-title">{tj.reviewTitle}</p>
            <p className="callout-text">{tj.reviewBody(job.clips.length)}</p>
          </div>
          <div className="callout-actions">
            <a className="btn btn-ghost" href="#plan">{tj.reviewCheck}</a>
            <button className="btn btn-primary" onClick={onRender} title={tj.makeTip}>
              <Icon name="film" size={16} />
              {tj.make}
            </button>
          </div>
        </div>
      )}

      {/* Above the clips, because it qualifies every one of them. A viewer who
          has already scrolled into the grid is not coming back up to find out
          that two of what they are looking at are 24 seconds long. */}
      <RangeNotice clips={job.clips} options={job.options} />

      {/* CLIPS FIRST. They are what the pipeline exists to produce, so nothing
          — least of all a six-row details table — goes above them. */}
      {job.outputs.length > 0 && (
        <section className="section" aria-labelledby="clips-title">
          <div className="section-head">
            <h2 className="section-title" id="clips-title">
              {tj.clipsTitle(job.outputs.length)}
              {job.state === "rendering" && <em className="section-note"> {tj.stillMaking}</em>}
            </h2>
            {canRender && <p className="section-sub">{tj.remakeNote}</p>}
          </div>
          <ClipGrid outputs={job.outputs} clips={job.clips} />
        </section>
      )}

      {job.clips.length > 0 && (
        <div className="card">
          <div className="card-head">
            <h2 className="card-title">{tj.mapTitle}</h2>
            <p className="card-sub">{tj.mapSub}</p>
          </div>
          <HarvestStrip clips={job.clips} />
        </div>
      )}

      {/* The key forces exactly ONE remount: the transition from "no plan yet"
          to "a plan arrived", which is when the editor's lazily-seeded draft
          must be re-seeded. Keying on the clip COUNT remounted it on every save
          that added or removed a clip too, which threw away the "these are the
          snapped boundaries" notice at the moment it matters most. */}
      <div id="plan" className="anchor">
        <PlanEditor
          key={`${job.id}-${job.clips.length === 0 ? "empty" : "seeded"}`}
          jobId={job.id}
          job={job}
          onSaved={reload}
        />
      </div>

      <div className="detail-grid">
        {job.word_count > 0 && (
          <div className="card card-feature">
            <div className="card-head">
              <h2 className="card-title">{tj.textTitle}</h2>
              <p className="card-sub">{tj.textSub(job.word_count, language ?? t.common.notYet)}</p>
            </div>
            <p className="muted">{tj.textBody}</p>
            <Link className="btn" to={`/jobs/${job.id}/transcript`}>
              <Icon name="edit" size={16} />
              {tj.textCta}
            </Link>
          </div>
        )}

        <div className="card">
          <div className="card-head">
            <h2 className="card-title">{tj.detailsTitle}</h2>
            <p className="card-sub">{tj.detailsSub}</p>
          </div>
          <dl className="dl">
            <dt>{tj.dSource}</dt>
            <dd>
              {t.source[job.source]}
              {job.source === "youtube" && job.url
                ? <> · <span className="mono" dir="ltr">{job.url}</span></>
                : null}
            </dd>

            <dt>{tj.dDevice}</dt>
            <dd>{job.device ?? t.common.notYet}</dd>

            {/* Only when a fallback actually happened: "" means no captions were
                burned in at all (not the same as "pop was used", so never shown
                as a style), and a match means the request was honoured — neither
                is news. A fallback the UI hides here is one discovered only in
                the rendered clip. */}
            {job.caption_style_used
              && job.caption_style_used !== job.options.caption_style
              && (
                <>
                  <dt>{tj.dCaptions}</dt>
                  <dd><span className="plan-warn">{tj.dCaptionsFallback}</span></dd>
                </>
              )}

            <dt>{tj.dLanguage}</dt>
            <dd>{language ?? t.common.notYet}</dd>

            <dt>{tj.dWords}</dt>
            <dd>
              {job.word_count > 0
                ? <>{t.common.words(job.word_count)}{job.transcript_cached ? ` ${tj.dCached}` : ""}</>
                : t.common.notYet}
            </dd>

            <dt>{tj.dCreated}</dt>
            <dd>{ago(job.created_at)}</dd>

            <dt>{tj.dUpdated}</dt>
            <dd>{ago(job.updated_at)}</dd>
          </dl>
        </div>
      </div>
    </div>
  );
}
