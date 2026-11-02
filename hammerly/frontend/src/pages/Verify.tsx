import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, LedgerRow, money } from "../api";
import { Err } from "../components";
import { useAsync } from "../hooks";

async function sha256(t: string) {
  const b = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(t));
  return Array.from(new Uint8Array(b)).map(x => x.toString(16).padStart(2, "0")).join("");
}

export default function Verify() {
  const { id } = useParams();
  const led = useAsync(() => api<{ entries: LedgerRow[] }>(`/listings/${id}/ledger`), [id]);
  const ver = useAsync(() => api<any>(`/listings/${id}/verify`), [id]);
  const [reveals, setReveals] = useState<Record<number, boolean>>({});
  useEffect(() => { // check sealed-bid reveals in the browser: sha256(salt|amount) must equal the commitment
    (async () => { const r: Record<number, boolean> = {};
      for (const e of led.data?.entries ?? []) if (e.kind === "reveal" && e.commitment && e.salt) r[e.seq] = (await sha256(`${e.salt}|${e.amount_cents}`)) === e.commitment;
      setReveals(r); })();
  }, [led.data]);
  const linked = led.data ? led.data.entries.every((e, i, a) => e.prev_hash === (i ? a[i - 1].hash : "genesis")) : null;
  return <div className="stack">
    <Link to={`/lots/${id}`} className="small muted">← Back to lot</Link>
    <h1>Proof Ledger · Lot #{id}</h1>
    <Err msg={led.error || ver.error} />
    {ver.data && <div className={ver.data.ok ? "ok" : "err"}><b>{ver.data.ok ? "Server check passed" : "Server check failed"}</b> — {ver.data.ok ? `${ver.data.count} entries recompute to their stored hashes.` : `Entry #${ver.data.broken_at}: ${ver.data.reason}.`}</div>}
    {linked !== null && <div className={linked ? "ok" : "err"}><b>Browser check {linked ? "passed" : "failed"}</b> — each entry's previous-hash points at the entry before it.</div>}
    {Object.keys(reveals).length > 0 && <div className={Object.values(reveals).every(Boolean) ? "ok" : "err"}><b>Sealed bids</b> — {Object.values(reveals).filter(Boolean).length} of {Object.keys(reveals).length} reveals match the commitments made at bid time.</div>}
    <div className="card" style={{ overflowX: "auto" }}><table><thead><tr><th>#</th><th>Kind</th><th>Bidder</th><th>Amount</th><th>Hash</th></tr></thead>
      <tbody>{led.data?.entries.map(e => <tr key={e.seq}><td className="mono">{e.seq}</td><td>{e.kind}</td><td>{e.alias}</td>
        <td>{e.amount_cents != null ? money(e.amount_cents) : e.commitment ? <span className="muted">sealed</span> : ""}{reveals[e.seq] !== undefined && (reveals[e.seq] ? " ✓" : " ✗")}</td>
        <td><div className="hash">{e.hash}</div><div className="hash">← {e.prev_hash}</div></td></tr>)}</tbody></table></div>
    {ver.data?.anchors?.length > 0 && <div className="card stack"><h2>Merkle anchors</h2><p className="small muted">Hourly roots that commit to this chain's head. A root can't be re-written without changing every later one.</p>
      {ver.data.anchors.map((a: any) => <div key={a.root_id}><div className="row between"><b>Root #{a.root_id}</b><span className={"chip " + (a.valid ? "good" : "bad")}>{a.valid ? "proof valid" : "proof invalid"}</span></div>
        <div className="hash">root {a.root}</div><div className="hash">covers entry #{a.seq} · {a.head}</div></div>)}</div>}
  </div>;
}
