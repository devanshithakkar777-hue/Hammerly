import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Card, money, money0, Order } from "../api";
import { useAuth } from "../auth";
import { Empty, Err, LotCard, Stars, StatusChip } from "../components";
import { useAsync } from "../hooks";

const TABS = [["orders", "Orders"], ["selling", "Selling"], ["wallet", "Wallet"], ["guardian", "Guardian & Calm"], ["inbox", "Notifications"]];
const toCents = (s: string) => Math.round(parseFloat(s || "0") * 100);

export default function Dashboard() {
  const { me } = useAuth(); const nav = useNavigate();
  const raw = (useParams()["*"] || "orders").split("/")[0]; const tab = TABS.some(t => t[0] === raw) ? raw : "orders";
  if (!me) return <Empty><Link to="/auth" className="btn primary">Sign in</Link></Empty>;
  return <div className="stack">
    <div className="row between"><h1>Hi, {me.name}</h1>{me.is_seller && <span className="chip good">Seller {me.verified ? "✓" : ""} · <Stars v={me.trust_score} n={me.ratings_count} /></span>}</div>
    <div className="tabs" role="tablist">{TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => nav(`/dashboard/${k}`)}>{l}</button>)}</div>
    {tab === "orders" && <Orders />}{tab === "selling" && <Selling />}{tab === "wallet" && <Wallet />}{tab === "guardian" && <Guardian />}{tab === "inbox" && <Inbox />}
  </div>;
}

function Orders() {
  const { toast, bumpWallet } = useAuth();
  const { data, error, reload } = useAsync(() => api<{ items: Order[] }>("/orders"), []);
  const [sel, setSel] = useState<number[]>([]); const [err, setErr] = useState<string | null>(null);
  const [track, setTrack] = useState<Record<number, string>>({}); const [dis, setDis] = useState<number | null>(null); const [reason, setReason] = useState("");
  const run = async (fn: () => Promise<any>, msg?: string) => { setErr(null); try { await fn(); msg && toast(msg); bumpWallet(); reload(); } catch (e: any) { setErr(e.message); } };
  if (!data) return <Err msg={error} />;
  if (!data.items.length) return <Empty>No orders yet. Win a lot and it appears here with its escrow status.</Empty>;
  const buyer = data.items.filter(o => o.role === "buyer");
  const bundleable = sel.length >= 2;
  return <div className="stack"><Err msg={err} />
    {bundleable && <div className="card row between"><span>{sel.length} orders selected</span><button className="btn primary" onClick={() => run(async () => { await api("/orders/bundle", { body: { order_ids: sel } }); setSel([]); }, "Bundled. Extra shipping refunded.")}>Bundle & refund extra shipping</button></div>}
    {data.items.map(o => <div className="card stack" key={o.id}>
      <div className="row between"><div className="row">{o.role === "buyer" && o.status === "held" && !o.squad_id && <input type="checkbox" style={{ width: "auto" }} checked={sel.includes(o.id)} aria-label="Select for bundle" onChange={e => setSel(e.target.checked ? [...sel, o.id] : sel.filter(x => x !== o.id))} />}
        <div><Link to={`/lots/${o.listing_id}`}><b>{o.title}</b></Link><div className="small muted">{o.role === "buyer" ? `from ${o.seller.name}` : `to ${o.buyer.name}`} · order #{o.id}{o.bundle_id ? ` · bundle #${o.bundle_id}` : ""}</div></div></div>
        <StatusChip s={o.status} /></div>
      <div className="row between small"><span>Sale {money(o.amount_cents)} + ship {money(o.shipping_cents)}</span>{o.role === "seller" ? <span>You receive {money(o.amount_cents - o.fee_cents + o.shipping_cents)} (fee {money(o.fee_cents)})</span> : o.my_share_cents != null && o.squad_id ? <span>Your share {money(o.my_share_cents)}</span> : null}</div>
      {o.payout_paused && o.role === "seller" && <div className="err small">Payout paused while Shield reviews this sale.</div>}
      {o.status === "shipped" && <div className="small muted">Tracking {o.tracking}. Auto-releases {o.release_at ? new Date(o.release_at).toLocaleDateString() : "in 7 days"} unless you confirm or dispute.</div>}
      <div className="row">
        {o.role === "seller" && o.status === "held" && <><input style={{ width: 200 }} placeholder="Tracking number" value={track[o.id] || ""} onChange={e => setTrack({ ...track, [o.id]: e.target.value })} aria-label="Tracking number" />
          <button className="btn primary" disabled={(track[o.id] || "").length < 3} onClick={() => run(() => api(`/orders/${o.id}/ship`, { body: { tracking: track[o.id] } }), "Marked shipped")}>Mark shipped</button></>}
        {o.role === "buyer" && o.status === "shipped" && <button className="btn primary" onClick={() => run(() => api(`/orders/${o.id}/confirm`, { method: "POST" }), "Delivery confirmed. Funds released.")}>I received it</button>}
        {["held", "shipped"].includes(o.status) && <button className="btn" onClick={() => setDis(dis === o.id ? null : o.id)}>Open a dispute</button>}
        {o.role === "buyer" && o.status === "released" && !o.rated && [5, 4, 3, 2, 1].map(n => <button key={n} className="btn" onClick={() => run(() => api(`/orders/${o.id}/rate`, { body: { stars: n } }), "Thanks for rating")}>{n}★</button>)}
      </div>
      {dis === o.id && <div className="stack"><textarea rows={3} placeholder="What went wrong? (10+ characters)" value={reason} onChange={e => setReason(e.target.value)} />
        <button className="btn danger" disabled={reason.length < 10} onClick={() => run(async () => { await api(`/orders/${o.id}/dispute`, { body: { reason } }); setDis(null); setReason(""); }, "Dispute opened. An admin will review it.")}>Submit dispute</button></div>}
    </div>)}
  </div>;
}

function Selling() {
  const { data, reload } = useAsync(() => api<{ items: Card[] }>("/listings/mine"), []);
  const { me } = useAuth();
  if (!me?.is_seller) return <Empty><Link to="/sell" className="btn primary">Become a seller</Link></Empty>;
  if (!data) return null;
  return <div className="stack"><div><Link to="/sell" className="btn primary">New listing</Link></div>
    {!data.items.length && <Empty>No listings yet.</Empty>}
    <div className="grid">{data.items.map(l => <div key={l.id} className="stack"><LotCard l={l} />
      {l.status === "draft" && <button className="btn block" onClick={async () => { await api(`/listings/${l.id}/publish`, { body: { duration_seconds: 3600 } }); reload(); }}>Publish for 1 hour</button>}</div>)}</div></div>;
}

function Wallet() {
  const { walletTick, bumpWallet } = useAuth();
  const { data, reload } = useAsync(() => api<any>("/wallet"), [walletTick]);
  const [v, setV] = useState("100.00"); const [err, setErr] = useState<string | null>(null);
  return <div className="stack">
    <div className="row">
      <div className="card grow"><div className="small muted">Available</div><div className="price">{money(data?.available_cents)}</div></div>
      <div className="card grow"><div className="small muted">On hold for bids & escrow</div><div className="price">{money(data?.held_cents)}</div></div></div>
    <div className="card stack"><h3>Add funds</h3>
      <p className="small muted">Payments run in <b>{data?.provider || "mock"}</b> mode: funds are credited instantly with no real money moving.</p>
      <div className="row"><input style={{ width: 140 }} value={v} onChange={e => setV(e.target.value)} aria-label="Amount" />
        <button className="btn primary" onClick={async () => { setErr(null); try { await api("/wallet/topup", { body: { amount_cents: toCents(v) } }); bumpWallet(); reload(); } catch (e: any) { setErr(e.message); } }}>Add</button></div><Err msg={err} /></div>
    <div className="card"><h3>Activity</h3><table><tbody>{data?.entries.map((e: any) => <tr key={e.id}><td>{e.kind}</td><td className="muted small">{e.ref_type} {e.ref_id || ""}</td><td className="right" style={{ color: e.amount_cents < 0 ? "var(--live)" : "var(--good)" }}>{money(e.amount_cents)}</td></tr>)}</tbody></table></div>
  </div>;
}

function Guardian() {
  const { me, setMe, toast } = useAuth(); const [cap, setCap] = useState("");
  useEffect(() => { if (me) setCap((me.daily_cap_cents / 100).toFixed(2)); }, [me?.daily_cap_cents]);
  const save = async (b: object) => { setMe(await api("/me", { method: "PATCH", body: b })); toast("Saved"); };
  if (!me) return null;
  return <div className="stack">
    <div className="card stack"><h3>Budget Guardian</h3><p className="small muted">A daily cap on money committed to bids. If a bid would pass it, you're asked to confirm. After being outbid five times in 30 minutes you also get a cool-off prompt.</p>
      <label>Daily cap ($)</label><div className="row"><input style={{ width: 160 }} value={cap} onChange={e => setCap(e.target.value)} /><button className="btn primary" onClick={() => save({ daily_cap_cents: toCents(cap) })}>Save</button></div></div>
    <div className="card stack"><h3>Calm Mode</h3><p className="small muted">Mutes outbid pings and live bid counts, stops pulsing animations, and only notifies you about wins, shipping and payouts.</p>
      <label className="row" style={{ fontWeight: 600 }}><input type="checkbox" style={{ width: "auto" }} checked={me.calm_mode} onChange={e => save({ calm_mode: e.target.checked })} /> Calm Mode is {me.calm_mode ? "on" : "off"}</label></div>
  </div>;
}

function Inbox() {
  const { walletTick } = useAuth();
  const { data, reload } = useAsync(() => api<any[]>("/notifications"), [walletTick]);
  if (!data) return null;
  const items = data;
  return <div className="card stack"><div className="row between"><h3>Notifications</h3><button className="btn" onClick={async () => { await api("/notifications/read-all", { method: "POST" }); reload(); }}>Mark all read</button></div>
    {!items.length && <span className="muted">Nothing yet.</span>}
    {items.map(n => <div key={n.id} style={{ borderTop: "1px solid var(--line)", paddingTop: 8, opacity: n.read ? .6 : 1 }}><Link to={n.link || "#"}><b>{n.title}</b></Link><div className="small muted">{n.body}</div></div>)}</div>;
}
