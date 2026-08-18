import { Link, Route, Routes } from "react-router-dom";
import { ToastProvider } from "./components/Toasts";
import JobsList from "./pages/JobsList";

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
            <Route path="/new" element={<p>new job — Task 6</p>} />
            <Route path="/jobs/:id" element={<p>job detail — Task 7</p>} />
          </Routes>
        </main>
      </div>
    </ToastProvider>
  );
}
