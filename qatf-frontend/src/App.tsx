import { Link, NavLink, Route, Routes } from "react-router-dom";
import { SettingsPage } from "./pages/SettingsPage";
import { ToastProvider } from "./components/Toasts";
import JobsList from "./pages/JobsList";
import NewJob from "./pages/NewJob";
import JobDetail from "./pages/JobDetail";
import TranscriptPage from "./pages/TranscriptPage";

export default function App() {
  return (
    <ToastProvider>
      <div className="app">
        <header className="topbar">
          <Link to="/" className="brand" aria-label="qatf — home">
            <span className="brand-ar" lang="ar">قطف</span>
            <span className="brand-latin">qatf</span>
          </Link>
          <nav className="nav">
            <NavLink
              to="/"
              end
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              Jobs
            </NavLink>
            <NavLink
              to="/settings"
              className={({ isActive }) => (isActive ? "nav-link active" : "nav-link")}
            >
              Settings
            </NavLink>
            <Link to="/new" className="btn btn-primary">New job</Link>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<JobsList />} />
            <Route path="/new" element={<NewJob />} />
            <Route path="/jobs/:id" element={<JobDetail />} />
            <Route path="/jobs/:id/transcript" element={<TranscriptPage />} />
            <Route path="/settings" element={<SettingsPage />} />
          </Routes>
        </main>
      </div>
    </ToastProvider>
  );
}
