import { useState } from "react";
import { api, money } from "../api";
import { useAuth } from "../auth";
import { Empty, Err } from "../components";
import { useAsync } from "../hooks";

export default function Admin() {
  const { me } = useAuth();
  const o = useAsync(() => api<any>("/admin/overview"), []);
  const flags = useAsync(() => api<{ items: any[] }>("/admin/flags"), []);
  const disputes = useAsync(() => api<{ items: any[] }>("/admin/disputes"), []);
  const [err, setErr] = useState<string | null>(null);
  if (!me?.is_admin) return <Empty>Admins only.</Empty>;
  const run = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); flags.reload(); disputes.reload(); o.reload(); } catch (e: any) { setErr(e.message); } };
  return <div className="stack">
    <h1>Admin</h1><Err msg={err || o.error} />
    {o.data && <div className="row">{[["Users", o.data.users], ["Live lots", o.data.live_listings], ["Open flags", o.data.open_flags], ["Open disputes", o.data.open_disputes], ["In escrow", money(o.data.escrow_held)]].map(([k, v]) =>
      <div className="card" key={k as string}><div className="small muted">{k}</div><div className="price" style={{ fontSize: 24 }}>{v}</div></div>)}</div>}
    <div className="card stack"><h2>Shield flags</h2>{flags.data?.items.length === 0 && <span className="muted">Nothing needs review.</span>}
      {flags.data?.items.map(f => <div key={f.id} className="row between" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <div><b>{f.kind}</b> <span className={"chip " + (f.score >= 0.8 ? "bad" : "warn")}>score {f.score.toFixed(2)}</span><div className="small muted">Lot #{f.listing_id} · user {f.user_id ?? "—"} · {JSON.stringify(f.details)}</div></div>
        <div className="row"><button className="btn" onClick={() => run(() => api(`/admin/flags/${f.id}`, { body: { status: "cleared" } }))}>Clear</button>
          <button className="btn danger" onClick={() => run(() => api(`/admin/flags/${f.id}`, { body: { status: "confirmed" } }))}>Confirm</button></div></div>)}</div>
    <div className="card stack"><h2>Disputes</h2>{disputes.data?.items.length === 0 && <span className="muted">No open disputes.</span>}
      {disputes.data?.items.map(d => <div key={d.id} style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}><b>Order #{d.escrow_id}</b><p>{d.reason}</p>
        <div className="row"><button className="btn" onClick={() => run(() => api(`/admin/disputes/${d.id}/resolve`, { body: { outcome: "buyer", note: "Refunded to buyer." } }))}>Refund buyer</button>
          <button className="btn" onClick={() => run(() => api(`/admin/disputes/${d.id}/resolve`, { body: { outcome: "seller", note: "Released to seller." } }))}>Release to seller</button></div></div>)}</div>
  </div>;
}
