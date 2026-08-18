import { Link, Route, Routes } from "react-router-dom";
import { ToastProvider } from "./components/Toasts";
import JobsList from "./pages/JobsList";
import NewJob from "./pages/NewJob";
import JobDetail from "./pages/JobDetail";

export default function App() {
  return (
    <ToastProvider>
      <div className="app">
        <header className="topbar">
          <Link to="/" className="brand">qatf <span className="brand-ar">قطف</span></Link>
          <nav>
            <Link to="/">Jobs</Link>
            <Link to="/new" className="btn btn-primary">New job</Link>
          </nav>
        </header>
        <main>
          <Routes>
            <Route path="/" element={<JobsList />} />
            <Route path="/new" element={<NewJob />} />
            <Route path="/jobs/:id" element={<JobDetail />} />
          </Routes>
        </main>
      </div>
    </ToastProvider>
  );
}
