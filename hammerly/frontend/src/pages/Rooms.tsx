import { FormEvent, useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, Card, money, money0 } from "../api";
import { useAuth } from "../auth";
import { Empty, Err, FORMAT_LABEL, Timer } from "../components";
import { useAsync, useChannel } from "../hooks";

type Room = { id: number; title: string; status: string; host: { name: string; verified: boolean }; viewers: number; current: Card | null; queue: Card[]; playback_url: string; starts_at: string | null };

export default function Rooms() {
  const { data, error } = useAsync(() => api<{ items: Room[] }>("/rooms"), []);
  const { me } = useAuth();
  return <div className="stack">
    <div className="row between"><div><h1>Live Rooms</h1><p className="muted">Hosts run a queue of lots. Bid, chat and react in real time.</p></div>{me?.is_seller && <HostForm />}</div>
    <Err msg={error} />
    {data && !data.items.length && <Empty>No rooms right now. Sellers can host one from here.</Empty>}
    <div className="grid">{data?.items.map(r => <Link key={r.id} to={`/rooms/${r.id}`} className="card stack">
      <div className="row between"><span className={"chip " + (r.status === "live" ? "live" : "")}>{r.status === "live" ? <><i className="dot" /> Live</> : "Scheduled"}</span><span className="small muted">{r.viewers} watching</span></div>
      <h3>{r.title}</h3><div className="small muted">Hosted by {r.host.name} · {r.queue.length} lots</div></Link>)}</div>
  </div>;
}

function HostForm() {
  const nav = useNavigate();
  const [open, setOpen] = useState(false); const [title, setTitle] = useState(""); const [url, setUrl] = useState(""); const [sel, setSel] = useState<number[]>([]); const [err, setErr] = useState<string | null>(null);
  const mine = useAsync(() => open ? api<{ items: Card[] }>("/listings/mine") : Promise.resolve({ items: [] }), [open]);
  const drafts = mine.data?.items.filter(l => l.status === "draft") ?? [];
  if (!open) return <button className="btn primary" onClick={() => setOpen(true)}>Host a room</button>;
  const go = async (e: FormEvent) => { e.preventDefault(); try { const r = await api("/rooms", { body: { title, listing_ids: sel, playback_url: url } }); nav(`/rooms/${r.id}`); } catch (x: any) { setErr(x.message); } };
  return <form className="card stack" style={{ width: "100%", maxWidth: 480 }} onSubmit={go}>
    <h3>New room</h3><input placeholder="Room title" value={title} onChange={e => setTitle(e.target.value)} required minLength={3} />
    <input placeholder="Video playback URL (HLS/MP4, optional)" value={url} onChange={e => setUrl(e.target.value)} />
    <div className="small muted">Queue draft lots (create them in Sell, save as draft):</div>
    {drafts.length === 0 && <div className="small muted">No drafts yet.</div>}
    {drafts.map(l => <label key={l.id} className="row" style={{ fontWeight: 400 }}><input type="checkbox" style={{ width: "auto" }} checked={sel.includes(l.id)} onChange={e => setSel(e.target.checked ? [...sel, l.id] : sel.filter(x => x !== l.id))} />{l.title}</label>)}
    <Err msg={err} /><button className="btn primary">Create room</button></form>;
}

export function RoomView() {
  const { id } = useParams(); const { me } = useAuth();
  const { data: r, reload, error } = useAsync(() => api<Room & { messages: { alias: string; body: string }[]; host: any }>(`/rooms/${id}`), [id]);
  const [msgs, setMsgs] = useState<{ alias: string; body: string }[]>([]); const [viewers, setViewers] = useState(0); const [floats, setFloats] = useState<{ k: number; e: string; x: number }[]>([]);
  const [text, setText] = useState(""); const end = useRef<HTMLDivElement>(null);
  useEffect(() => { if (r) { setMsgs(r.messages); setViewers(r.viewers); } }, [r?.id]);
  useEffect(() => { end.current?.scrollIntoView({ block: "nearest" }); }, [msgs.length]);
  const send = useChannel(me ? `/ws/rooms/${id}?token=${localStorage.getItem("hammerly.token")}` : `/ws/rooms/${id}`, ev => {
    if (ev.type === "chat") setMsgs(m => [...m.slice(-80), ev]);
    else if (ev.type === "viewers") setViewers(ev.count);
    else if (ev.type === "reaction") { const k = Math.random(); setFloats(f => [...f, { k, e: ev.emoji, x: 10 + Math.random() * 80 }]); setTimeout(() => setFloats(f => f.filter(x => x.k !== k)), 2000); }
    else if (ev.type === "lot_changed" || ev.type === "closed" || ev.type === "bid") reload();
  });
  if (error) return <Err msg={error} />;
  if (!r) return <Empty>Loading…</Empty>;
  const host = me && r.host && r.host.id === me.id;
  const act = (p: string) => api(`/rooms/${id}/${p}`, { method: "POST" }).then(reload).catch(e => alert(e.message));
  const cur = r.current;
  return <div className="split">
    <div className="stack">
      <div className="card" style={{ position: "relative", overflow: "hidden", minHeight: 260 }}>
        {r.playback_url ? <video src={r.playback_url} controls autoPlay muted playsInline style={{ width: "100%", borderRadius: 12 }} /> :
          <div className="empty">No video attached. The host can paste a playback URL when creating the room.</div>}
        {floats.map(f => <span key={f.k} className="float" style={{ left: `${f.x}%` }}>{f.e}</span>)}
      </div>
      <div className="row between"><div><h2>{r.title}</h2><span className="small muted">Hosted by {r.host.name} · {viewers} watching</span></div>
        <span className={"chip " + (r.status === "live" ? "live" : "")}>{r.status}</span></div>
      <div className="row">{["🔥", "👏", "😮", "❤️", "💎"].map(e => <button key={e} className="btn" onClick={() => send({ type: "react", emoji: e })} aria-label={`React ${e}`}>{e}</button>)}</div>
      {host && <div className="card row"><b>Host controls</b>
        {r.status === "scheduled" && <button className="btn primary" onClick={() => act("start")}>Go live</button>}
        {r.status === "live" && <><button className="btn" onClick={() => act("next")}>Next lot</button><button className="btn danger" onClick={() => act("end")}>End room</button></>}</div>}
      <div className="card stack"><h3>Queue</h3>{r.queue.length === 0 && <span className="muted small">Empty.</span>}
        {r.queue.map(l => <div key={l.id} className="row between"><span>{l.title}</span><span className="chip">{l.status}</span></div>)}</div>
    </div>
    <div className="stack">
      {cur ? <div className="card stack"><span className="chip live"><i className="dot" /> On the block</span><h2>{cur.title}</h2>
        <div className="row between"><div className="price">{money(cur.price_cents)}</div><Timer endsAt={cur.ends_at} status={cur.status} /></div>
        <div className="small muted">{FORMAT_LABEL[cur.format]} · {cur.bid_count} bids</div>
        <Link to={`/lots/${cur.id}`} className="btn primary big block">Bid on this lot</Link></div> :
        <div className="card empty">{r.status === "ended" ? "This room has ended." : "Waiting for the host to put the next lot up."}</div>}
      <div className="card stack"><h3>Chat</h3>
        <div className="chat">{msgs.map((m, i) => <div key={i}><b>{m.alias}</b> {m.body}</div>)}<div ref={end} /></div>
        {me ? <form className="row" onSubmit={e => { e.preventDefault(); if (text.trim()) { send({ type: "chat", body: text }); setText(""); } }}>
          <input value={text} onChange={e => setText(e.target.value)} maxLength={300} placeholder="Say something" aria-label="Chat message" /><button className="btn">Send</button></form> :
          <Link to="/auth" className="btn">Sign in to chat</Link>}</div>
    </div>
  </div>;
}
