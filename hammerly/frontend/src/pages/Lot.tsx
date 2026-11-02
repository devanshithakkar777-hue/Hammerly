import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, ApiError, Detail, money, money0, serverNow, Squad } from "../api";
import { useAuth } from "../auth";
import { Err, FORMAT_LABEL, HoldButton, Sheet, StatusChip, Stars, Timer } from "../components";
import { useAsync, useChannel } from "../hooks";

const dollars = (c: number) => (c / 100).toFixed(2);
const toCents = (s: string) => Math.round(parseFloat(s || "0") * 100);

function dutchNow(d: Detail) {
  if (!d.dutch || !d.starts_at) return d.price_cents;
  const mins = Math.max(0, (serverNow() - new Date(d.starts_at).getTime()) / 60000);
  return Math.max(d.dutch.floor_cents, Math.round(d.start_price_cents - d.dutch.drop_cents_per_min * mins));
}

export default function Lot() {
  const { id } = useParams(); const { me, toast, bumpWallet, refresh } = useAuth(); const nav = useNavigate();
  const { data: d, error, reload, setData } = useAsync(() => api<Detail>(`/listings/${id}`), [id]);
  const [img, setImg] = useState(0);
  const [, tick] = useState(0);
  useEffect(() => { const t = setInterval(() => tick(x => x + 1), 500); return () => clearInterval(t); }, []);

  useChannel(`/ws/listings/${id}`, ev => {
    if (ev.type === "closed") { reload(); return; }
    if (ev.type === "bid") setData(p => p && ({ ...p, price_cents: ev.price_cents, leader_alias: ev.leader_alias, bid_count: ev.bid_count,
      ends_at: ev.ends_at, extensions_used: ev.extensions_used, ledger: [...ev.entries.slice().reverse(), ...p.ledger].slice(0, 30) }));
    if (ev.type === "bid") reload(); // refreshes min next bid and my own state
  });

  if (error) return <Err msg={error} />;
  if (!d) return <div className="empty">Loading…</div>;
  const live = d.status === "live";
  const isMine = d.me?.is_seller;
  const shown = d.format === "dutch" && live ? dutchNow(d) : d.price_cents;

  return (
    <div className="stack">
      <div className="small"><Link to="/" className="muted">← All lots</Link></div>
      <div className="split">
        <div>
          <div className="gallery" style={{ backgroundImage: d.images[img] ? `url(${d.images[img]})` : undefined }} role="img" aria-label={d.title} />
          {d.images.length > 1 && <div className="thumbs">{d.images.map((u, i) => <i key={u} className={i === img ? "on" : ""} style={{ backgroundImage: `url(${u})` }} onClick={() => setImg(i)} />)}</div>}
          <div className="card stack" style={{ marginTop: 16 }}>
            <h2>About this lot</h2><p style={{ whiteSpace: "pre-wrap" }}>{d.description || "No description."}</p>
            {d.condition_grade && <div className="kv"><span className="muted">Condition</span><b>{d.condition_grade}/10</b></div>}
            <div className="kv"><span className="muted">Shipping</span><b>{money(d.shipping_cents)}</b></div>
            <div className="kv"><span className="muted">Seller</span><b>{d.seller.name} {d.seller.verified && "✓"} <Stars v={d.seller.trust_score} n={d.seller.ratings_count} /></b></div>
          </div>
          <Passport d={d} />
          <Atlas d={d} />
        </div>
        <div className="stack" style={{ position: "sticky", top: 76 }}>
          <div className="card stack">
            <div className="row between"><span className="chip">{FORMAT_LABEL[d.format]}</span>{live && <span className="chip live"><i className="dot" /> Live</span>}{!live && <StatusChip s={d.status} />}</div>
            <h1 style={{ fontSize: 28 }}>{d.title}</h1>
            <div className="row between" style={{ alignItems: "flex-end" }}>
              <div><div className="small muted">{d.format === "sealed" ? "Starting price" : d.format === "dutch" ? "Price right now" : d.bid_count ? "Current bid" : "Starting bid"}</div>
                <div className="price">{money(shown)}</div>
                {d.format === "english" && d.leader_alias && <div className="small">Leading: <b>{d.leader_alias}</b>{d.me?.leading && " (you)"}</div>}</div>
              <div className="right"><Timer endsAt={d.ends_at} status={d.status} />
                {!me?.calm_mode && d.format === "english" && <div className="small muted">{d.bid_count} {d.bid_count === 1 ? "bid" : "bids"}{d.extensions_used ? ` · +${d.extensions_used} fair-close` : ""}</div>}
                {d.format === "english" && d.reserve_met !== null && <div className="small">{d.reserve_met ? <span style={{ color: "var(--good)" }}>Reserve met</span> : <span className="muted">Reserve not met</span>}</div>}</div>
            </div>
            {live && !isMine && <BidPanel d={d} reload={reload} shown={shown} />}
            {isMine && <div className="ok">This is your listing.{d.settlement_hold ? " Payout is paused for a Shield review." : ""}</div>}
            {!live && d.status === "sold" && <div className="ok">Sold for {money(d.final_price_cents)}{d.winner_user_id === me?.id && <> — <Link to="/dashboard/orders"><b>view your order</b></Link></>}</div>}
            {!live && d.status === "unsold" && <div className="muted">This auction ended without a sale.</div>}
            {me && !isMine && <WatchBtn d={d} reload={reload} />}
          </div>
          <Squads d={d} reload={reload} />
          <Ledger d={d} />
        </div>
      </div>
    </div>
  );
}

function BidPanel({ d, reload, shown }: { d: Detail; reload: () => void; shown: number }) {
  const { me, toast, bumpWallet } = useAuth(); const nav = useNavigate();
  const [amt, setAmt] = useState(dollars(d.min_next_cents)); const [max, setMax] = useState("");
  const [err, setErr] = useState<ApiError | null>(null); const [busy, setBusy] = useState(false);
  const [guard, setGuard] = useState<ApiError | null>(null);
  useEffect(() => { setAmt(dollars(d.min_next_cents)); }, [d.min_next_cents]);
  if (!me) return <button className="btn primary big block" onClick={() => nav("/auth")}>Sign in to bid</button>;

  const amount = d.format === "dutch" ? shown : toCents(amt);
  const hold = amount + d.shipping_cents;

  const send = async (confirm: string[] = []) => {
    setBusy(true); setErr(null); setGuard(null);
    try {
      const path = d.format === "dutch" ? "claim" : d.format === "sealed" ? "sealed" : "bids";
      const body = d.format === "dutch" ? { confirm } : { amount_cents: amount, max_cents: max ? toCents(max) : undefined, confirm };
      const r = await api(`/listings/${d.id}/${path}`, { body });
      bumpWallet(); reload();
      toast(d.format === "dutch" ? "Claimed" : d.format === "sealed" ? "Sealed bid placed" : "Bid placed", d.format === "sealed" ? "Your amount stays hidden until the auction closes." : undefined);
    } catch (e: any) {
      if (e instanceof ApiError && ["cap_exceeded", "cooloff"].includes(e.code)) setGuard(e); else setErr(e);
      reload();
    } finally { setBusy(false); }
  };

  const label = d.format === "dutch" ? `Hold to claim at ${money(shown)}` : d.format === "sealed" ? "Hold to seal your bid" : "Hold to place bid";
  if (d.format === "sealed" && d.me?.sealed_placed) return <div className="ok">Your sealed bid is in. It stays hidden until the auction closes.</div>;

  return (
    <div className="stack">
      {d.format !== "dutch" && <div>
        <label>{d.format === "sealed" ? "Your sealed bid" : "Your bid"} <small>min {money(d.min_next_cents)}</small></label>
        <input inputMode="decimal" value={amt} onChange={e => setAmt(e.target.value)} aria-label="Bid amount" />
        {d.format === "english" && <div className="row" style={{ marginTop: 8 }}>
          {[0, 1, 2, 5].map(n => <button key={n} className="btn" onClick={() => setAmt(dollars(d.min_next_cents + n * 1000))}>{n ? `+${money0(n * 1000)}` : "Min"}</button>)}</div>}
      </div>}
      {d.format === "english" && <div><label>Auto-bid up to <small>(optional — Smart Proxy bids for you, only as much as needed)</small></label>
        <input inputMode="decimal" placeholder="e.g. 1500.00" value={max} onChange={e => setMax(e.target.value)} aria-label="Maximum auto-bid" />
        {d.me?.my_max_cents && <div className="small muted">Your current max: {money(d.me.my_max_cents)}</div>}</div>}
      <div className="small muted">We hold {money(hold)} ({money(amount)} + {money(d.shipping_cents)} shipping) in your wallet. {d.format === "sealed" && d.sealed_pricing === "second" ? "You pay the second-highest bid if you win." : "Released immediately if you're outbid."}</div>
      <Err msg={err?.message} />
      {err?.code === "insufficient_funds" && <TopupInline need={err.extra.needed_cents} onDone={() => { setErr(null); bumpWallet(); }} />}
      <HoldButton label={label} onConfirm={() => send()} disabled={busy || amount <= 0} />
      {guard && <Sheet onClose={() => setGuard(null)}>
        <h2>{guard.code === "cap_exceeded" ? "This passes your daily cap" : "Take a breath"}</h2>
        <p>{guard.message}</p>
        {guard.code === "cap_exceeded" && <p className="muted">Exposure would be {money(guard.extra.exposure_cents)}; your cap is {money(guard.extra.cap_cents)}.</p>}
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn" onClick={() => setGuard(null)}>Not now</button>
          <HoldButton className="btn danger" label="Hold to bid anyway" onConfirm={() => send([guard.code === "cap_exceeded" ? "cap" : "cooloff"])} />
        </div>
      </Sheet>}
    </div>
  );
}

export function TopupInline({ need, onDone }: { need?: number; onDone: () => void }) {
  const [v, setV] = useState(dollars(Math.max(need ?? 0, 5000)));
  const [busy, setBusy] = useState(false);
  return <div className="row"><input style={{ width: 120 }} value={v} onChange={e => setV(e.target.value)} aria-label="Top-up amount" />
    <button className="btn" disabled={busy} onClick={async () => { setBusy(true); try { await api("/wallet/topup", { body: { amount_cents: toCents(v) } }); onDone(); } finally { setBusy(false); } }}>Add test funds</button></div>;
}

function WatchBtn({ d, reload }: { d: Detail; reload: () => void }) {
  const [alert, setAlert] = useState("");
  const on = d.me?.watching;
  return <div className="row">
    <button className="btn" onClick={async () => { await api(`/listings/${d.id}/watch`, { method: on ? "DELETE" : "POST", body: on ? undefined : { alert_price_cents: alert ? toCents(alert) : null } }); reload(); }}>{on ? "★ Watching" : "☆ Watch"}</button>
    {!on && <input style={{ width: 210 }} placeholder="Alert at price (optional)" value={alert} onChange={e => setAlert(e.target.value)} aria-label="Price alert" />}
    {on && d.me?.alert_price_cents && <span className="small muted">Alert at {money(d.me.alert_price_cents)}</span>}
  </div>;
}

function Passport({ d }: { d: Detail }) {
  if (!d.passport) return null;
  return <div className="card passport" style={{ marginTop: 16 }}>
    <div className="row between"><h2>Authenticity Passport</h2><span className="chip good">{d.passport.verified_count}/{d.passport.total} verified</span></div>
    {d.passport.checks.map(c => <div className="it" key={c.key}><span className={"ic " + c.status}>{c.status === "verified" ? "✓" : c.status === "failed" ? "!" : c.status === "pending" ? "…" : "–"}</span>
      <div><b>{c.label}</b><div className="small muted">{c.detail}</div></div></div>)}
  </div>;
}

function Atlas({ d }: { d: Detail }) {
  const a = d.atlas; if (!a.count) return null;
  const prices = a.samples.map(s => s.price_cents); const hi = Math.max(...prices, shownPrice(d));
  const cur = shownPrice(d);
  return <div className="card stack" style={{ marginTop: 16 }}>
    <div className="row between"><h2>Price Atlas</h2><span className="small muted">{a.count} comparable sales</span></div>
    <div className="row"><div><div className="small muted">Typical</div><b>{money0(a.median_cents)}</b></div><div><div className="small muted">Range</div><b>{money0(a.low_cents)} – {money0(a.high_cents)}</b></div>
      {a.median_cents && cur > 0 && <div><div className="small muted">This lot now</div><b style={{ color: cur <= a.median_cents ? "var(--good)" : "var(--warn)" }}>{Math.round((cur / a.median_cents) * 100)}% of typical</b></div>}</div>
    <div className="spark" aria-hidden>{prices.map((p, i) => <i key={i} style={{ height: `${(p / hi) * 100}%` }} />)}<i className="me" style={{ height: `${(cur / hi) * 100}%` }} /></div>
    <div className="small muted">Past sales, oldest to newest. The red bar is this lot's current price.{!a.samples.length ? "" : ""}</div>
  </div>;
}
const shownPrice = (d: Detail) => (d.format === "dutch" && d.status === "live" ? dutchNow(d) : d.price_cents);

function Ledger({ d }: { d: Detail }) {
  const [open, setOpen] = useState(false);
  const v = useAsync(() => api<any>(`/listings/${d.id}/verify`), [d.id, d.bid_count, d.status]);
  return <div className="card stack">
    <div className="row between"><h2>Proof Ledger</h2>
      {v.data && <span className={"chip " + (v.data.ok ? "good" : "bad")}>{v.data.ok ? `Chain intact · ${v.data.count} entries` : "Chain broken"}</span>}</div>
    <p className="small muted">Every bid is linked to the one before it by a SHA-256 hash. Edits or deletions break the chain, and anyone can check it.</p>
    <Link className="btn" to={`/verify/${d.id}`}>Open the public verifier →</Link>
    <button className="btn ghost" onClick={() => setOpen(!open)}>{open ? "Hide" : "Show"} latest entries</button>
    {open && <table><tbody>{d.ledger.map(e => <tr key={e.seq}><td className="mono">#{e.seq}</td><td>{e.alias}<div className="small muted">{e.kind}</div></td>
      <td className="right">{e.amount_cents != null ? money(e.amount_cents) : e.commitment ? "sealed" : ""}<div className="hash">{e.hash.slice(0, 12)}…</div></td></tr>)}</tbody></table>}
  </div>;
}

function Squads({ d, reload }: { d: Detail; reload: () => void }) {
  const { me, toast, bumpWallet } = useAuth();
  const [name, setName] = useState(""); const [pledge, setPledge] = useState(""); const [err, setErr] = useState<string | null>(null);
  if (d.format !== "english" || (d.status !== "live" && !d.squads.length) || d.me?.is_seller) return null;
  const call = async (fn: () => Promise<any>) => { setErr(null); try { await fn(); bumpWallet(); reload(); } catch (e: any) { setErr(e.message); } };
  return <div className="card stack">
    <h2>Squad Bids</h2><p className="small muted">Pool money with friends for a lot you couldn't buy alone. Ownership follows what each person put in.</p>
    {d.squads.map(s => <SquadRow key={s.id} s={s} d={d} call={call} />)}
    {me && d.status === "live" && <>
      <div className="row"><input placeholder="Squad name" value={name} onChange={e => setName(e.target.value)} aria-label="Squad name" />
        <input placeholder="Your pledge ($)" style={{ width: 130 }} value={pledge} onChange={e => setPledge(e.target.value)} aria-label="Your pledge" />
        <button className="btn" disabled={!name || !pledge} onClick={() => call(async () => { await api(`/listings/${d.id}/squads`, { body: { name, pledge_cents: toCents(pledge) } }); toast("Squad created"); })}>Start a squad</button></div>
    </>}
    <Err msg={err} />
  </div>;
}

function SquadRow({ s, d, call }: { s: Squad; d: Detail; call: (f: () => Promise<any>) => void }) {
  const { me } = useAuth(); const [p, setP] = useState(""); const [b, setB] = useState("");
  const mine = s.members.find(m => m.user_id === me?.id);
  return <div style={{ borderTop: "1px solid var(--line)", paddingTop: 12 }} className="stack">
    <div className="row between"><b>{s.name}</b><StatusChip s={s.status} /></div>
    <div className="small muted">Pledged {money0(s.pledged_cents)}</div>
    {s.members.map(m => <div key={m.user_id} className="small"><div className="row between"><span>{m.name}</span><span>{money0(m.pledge_cents)} · {(m.share_bps / 100).toFixed(0)}%</span></div><div className="bar"><i style={{ width: `${m.share_bps / 100}%` }} /></div></div>)}
    {s.status === "forming" && me && !mine && <div className="row"><input style={{ width: 120 }} placeholder="Pledge ($)" value={p} onChange={e => setP(e.target.value)} />
      <button className="btn" disabled={!p} onClick={() => call(() => api(`/squads/${s.id}/join`, { body: { pledge_cents: toCents(p) } }))}>Join</button></div>}
    {s.status === "forming" && s.owner_id === me?.id && <div className="row"><input style={{ width: 120 }} placeholder={`Bid ≥ ${dollars(d.min_next_cents)}`} value={b} onChange={e => setB(e.target.value)} />
      <button className="btn primary" disabled={!b} onClick={() => call(() => api(`/squads/${s.id}/bid`, { body: { amount_cents: toCents(b) } }))}>Bid as squad</button></div>}
    {s.ownership && <div className="ok small">Won. Ownership: {s.ownership.map(o => `${(o.share_bps / 100).toFixed(0)}%`).join(" / ")}</div>}
  </div>;
}
