import { Link, Route, Routes } from "react-router-dom";

export default function App() {
  return (
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
          <Route path="/" element={<p>jobs dashboard — Task 5</p>} />
          <Route path="/new" element={<p>new job — Task 6</p>} />
          <Route path="/jobs/:id" element={<p>job detail — Task 7</p>} />
        </Routes>
      </main>
    </div>
  );
}
