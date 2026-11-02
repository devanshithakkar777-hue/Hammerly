import { Link, NavLink, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Discover from "./pages/Discover";
import AuthPage from "./pages/AuthPage";
import Lot from "./pages/Lot";
import Rooms, { RoomView } from "./pages/Rooms";
import Sell from "./pages/Sell";
import Dashboard from "./pages/Dashboard";
import Verify from "./pages/Verify";
import Admin from "./pages/Admin";

export default function App() {
  const { me, logout, toasts, dismiss, ready } = useAuth();
  const nl = ({ isActive }: { isActive: boolean }) => "link" + (isActive ? " active" : "");
  return (
    <div className={me?.calm_mode ? "calm" : ""}>
      <nav className="nav"><div className="wrap">
        <Link to="/" className="brand">hammer<b>ly</b></Link>
        <NavLink to="/" end className={nl}>Discover</NavLink>
        <NavLink to="/rooms" className={nl}>Live Rooms</NavLink>
        <NavLink to="/sell" className={nl}>Sell</NavLink>
        <span className="grow" />
        {me ? <>
          {me.is_admin && <NavLink to="/admin" className={nl}>Admin</NavLink>}
          <NavLink to="/dashboard" className={nl}>{me.name}</NavLink>
          <button className="btn ghost" onClick={logout}>Sign out</button>
        </> : <Link to="/auth" className="btn primary">Sign in</Link>}
      </div></nav>
      <main className="wrap" style={{ padding: "24px 16px 80px" }}>
        {ready && <Routes>
          <Route path="/" element={<Discover />} />
          <Route path="/auth" element={<AuthPage />} />
          <Route path="/lots/:id" element={<Lot />} />
          <Route path="/verify/:id" element={<Verify />} />
          <Route path="/rooms" element={<Rooms />} />
          <Route path="/rooms/:id" element={<RoomView />} />
          <Route path="/sell" element={<Sell />} />
          <Route path="/dashboard/*" element={<Dashboard />} />
          <Route path="/orders/*" element={<Dashboard />} />
          <Route path="/admin" element={<Admin />} />
          <Route path="*" element={<div className="empty">Page not found.</div>} />
        </Routes>}
      </main>
      <div className="toasts" aria-live="polite">{toasts.map(t => <div key={t.id} className="toast" onClick={() => dismiss(t.id)}><b>{t.title}</b>{t.body}</div>)}</div>
    </div>
  );
}
