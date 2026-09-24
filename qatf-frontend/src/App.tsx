import { lazy, Suspense, useEffect, useRef } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import { Icon } from "./components/Icon";
import { LanguageSwitch } from "./components/LanguageSwitch";
import { ServerStatus } from "./components/ServerStatus";
import { ToastProvider } from "./components/Toasts";
import { useI18n } from "./i18n/I18nProvider";

// One chunk per page — its component AND its stylesheet — fetched the first
// time the page is opened. The shell and base.css are the only things every
// visit pays for.
const JobsPage = lazy(() => import("./pages/jobs/JobsPage"));
const NewJobPage = lazy(() => import("./pages/new-job/NewJobPage"));
const JobPage = lazy(() => import("./pages/job/JobPage"));
const TranscriptPage = lazy(() => import("./pages/transcript/TranscriptPage"));
const SettingsPage = lazy(() => import("./pages/settings/SettingsPage"));

const navClass = ({ isActive }: { isActive: boolean }) =>
  isActive ? "nav-link active" : "nav-link";

function PageLoading() {
  return (
    <div className="page" aria-busy="true">
      <div className="skeleton-title" />
      <div className="skeleton-row" />
    </div>
  );
}

export default function App() {
  const { t } = useI18n();
  const { pathname } = useLocation();
  const main = useRef<HTMLElement>(null);

  // A route change in an SPA announces nothing and leaves focus on the link
  // that was clicked, somewhere in the sidebar. Moving focus to the content
  // (without scrolling or drawing a ring) puts a keyboard or screen-reader user
  // where a real page load would have. Skipped on first render: focus there
  // belongs to the browser.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    window.scrollTo(0, 0);
    main.current?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <ToastProvider>
      <a className="skip-link" href="#main">{t.app.skip}</a>
      <div className="shell">
        <aside className="sidebar">
          <Link to="/" className="brand" aria-label={`qatf — ${t.app.navVideos}`}>
            <span className="brand-ar" lang="ar">قطف</span>
            <span className="brand-latin">qatf</span>
          </Link>
          <p className="brand-tag">{t.app.tagline}</p>

          <Link to="/new" className="btn btn-primary sidebar-cta">
            <Icon name="plus" />
            <span>{t.app.newVideo}</span>
          </Link>

          <nav className="nav" aria-label={t.app.navMain}>
            <NavLink to="/" end className={navClass}>
              <Icon name="video" />
              <span>{t.app.navVideos}</span>
            </NavLink>
            <NavLink to="/settings" className={navClass}>
              <Icon name="settings" />
              <span>{t.app.navSettings}</span>
            </NavLink>
          </nav>

          <div className="sidebar-foot">
            <LanguageSwitch />
            <ServerStatus />
          </div>
        </aside>

        <main id="main" ref={main} tabIndex={-1} className="main">
          <Suspense fallback={<PageLoading />}>
            <Routes>
              <Route path="/" element={<JobsPage />} />
              <Route path="/new" element={<NewJobPage />} />
              <Route path="/jobs/:id" element={<JobPage />} />
              <Route path="/jobs/:id/transcript" element={<TranscriptPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </ToastProvider>
  );
}
