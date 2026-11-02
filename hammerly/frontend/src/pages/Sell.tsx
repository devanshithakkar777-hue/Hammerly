import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, money, money0 } from "../api";
import { useAuth } from "../auth";
import { Err } from "../components";

const toCents = (s: string) => Math.round(parseFloat(s || "0") * 100);
const dollars = (c?: number | null) => (c ? (c / 100).toFixed(2) : "");
const CATS = ["cameras", "electronics", "books", "furniture", "watches", "collectibles", "music", "home", "other"];

export default function Sell() {
  const { me, setMe, toast } = useAuth(); const nav = useNavigate();
  const file = useRef<HTMLInputElement>(null);
  const [images, setImages] = useState<string[]>([]);
  const [f, setF] = useState({ title: "", description: "", category: "other", format: "english", start: "", reserve: "", condition: "", provenance: "", floor: "", drop: "", sealed_pricing: "second", duration: "3600" });
  const [rec, setRec] = useState<{ reason: string; format: string } | null>(null);
  const [comps, setComps] = useState<any>(null);
  const [busy, setBusy] = useState(false); const [err, setErr] = useState<string | null>(null);
  const set = (k: string, v: string) => setF(p => ({ ...p, [k]: v }));

  if (!me) return <div className="empty">Please <a href="/auth" style={{ color: "var(--accent)" }}>sign in</a> to sell.</div>;
  if (!me.is_seller) return <div className="card stack" style={{ maxWidth: 480, margin: "30px auto" }}><h2>Become a seller</h2>
    <p className="muted">Sellers are verified before they can list. In this test build, verification is instant.</p>
    <button className="btn primary big" onClick={async () => { setMe(await api("/me/seller", { method: "POST" })); }}>Verify me as a seller</button></div>;

  const studio = async (files: FileList | null) => {
    if (!files?.length) return; setBusy(true); setErr(null);
    try {
      const form = new FormData(); Array.from(files).forEach(x => form.append("files", x)); form.append("hint", f.title);
      const r = await api("/ai/listing-draft", { form });
      setImages(r.images); setComps(r.comps); setRec(r.format);
      setF(p => ({ ...p, title: r.draft.title || p.title, description: r.draft.description || p.description, category: CATS.includes(r.draft.category) ? r.draft.category : p.category,
        condition: r.draft.condition_grade ? String(r.draft.condition_grade) : p.condition, format: r.format.format,
        start: dollars(r.pricing.start_price_cents) || p.start, reserve: dollars(r.pricing.reserve_cents) || p.reserve,
        floor: dollars(r.pricing.dutch_floor_cents) || p.floor, drop: dollars(r.pricing.dutch_drop_cents_per_min) || p.drop }));
      toast("Draft ready", "Check everything before you publish.");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  const save = async (publish: boolean) => {
    setBusy(true); setErr(null);
    try {
      const body: any = { title: f.title, description: f.description, category: f.category, format: f.format, start_price_cents: toCents(f.start),
        reserve_cents: toCents(f.reserve), condition_grade: f.condition ? parseFloat(f.condition) : undefined, provenance: f.provenance, images, sealed_pricing: f.sealed_pricing };
      if (f.format === "dutch") { body.dutch_floor_cents = toCents(f.floor); body.dutch_drop_cents_per_min = toCents(f.drop); body.reserve_cents = 0; }
      const l = await api("/listings", { body });
      if (publish) await api(`/listings/${l.id}/publish`, { body: { duration_seconds: parseInt(f.duration) } });
      nav(publish ? `/lots/${l.id}` : "/dashboard/selling");
    } catch (e: any) { setErr(e.message); } finally { setBusy(false); }
  };

  return <div className="split">
    <div className="card stack">
      <h2>AI Listing Studio</h2><p className="muted small">Add photos and we'll draft the title, description, category, a starting price from comparable sales, and the best auction format.</p>
      <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" multiple hidden onChange={e => studio(e.target.files)} />
      <button className="btn primary big" disabled={busy} onClick={() => file.current?.click()}>{busy ? "Working…" : images.length ? "Replace photos" : "Add photos & draft"}</button>
      {images.length > 0 && <div className="thumbs">{images.map(i => <i key={i} className="on" style={{ backgroundImage: `url(/media/${i})` }} />)}</div>}
      {rec && <div className="ok"><b>Suggested: {rec.format}.</b> {rec.reason}</div>}
      {comps?.count > 0 && <div className="small muted">Based on {comps.count} comparable sales · typical {money0(comps.median_cents)}</div>}
      <Err msg={err} />
    </div>
    <div className="card stack">
      <h2>Listing details</h2>
      <div><label>Title</label><input value={f.title} onChange={e => set("title", e.target.value)} /></div>
      <div><label>Description</label><textarea rows={4} value={f.description} onChange={e => set("description", e.target.value)} /></div>
      <div className="row"><div className="grow"><label>Category</label><select value={f.category} onChange={e => set("category", e.target.value)}>{CATS.map(c => <option key={c}>{c}</option>)}</select></div>
        <div className="grow"><label>Condition (1–10)</label><input value={f.condition} onChange={e => set("condition", e.target.value)} inputMode="decimal" /></div></div>
      <div><label>Format</label><select value={f.format} onChange={e => set("format", e.target.value)}>
        <option value="english">Ascending (English)</option><option value="dutch">Dutch drop</option><option value="sealed">Sealed bid (Vickrey)</option></select></div>
      <div className="row"><div className="grow"><label>{f.format === "dutch" ? "Start price ($)" : "Starting price ($)"}</label><input inputMode="decimal" value={f.start} onChange={e => set("start", e.target.value)} /></div>
        {f.format !== "dutch" && <div className="grow"><label>Reserve ($) <small>hidden</small></label><input inputMode="decimal" value={f.reserve} onChange={e => set("reserve", e.target.value)} /></div>}</div>
      {f.format === "dutch" && <div className="row"><div className="grow"><label>Floor ($)</label><input inputMode="decimal" value={f.floor} onChange={e => set("floor", e.target.value)} /></div>
        <div className="grow"><label>Drop per minute ($)</label><input inputMode="decimal" value={f.drop} onChange={e => set("drop", e.target.value)} /></div></div>}
      {f.format === "sealed" && <div><label>Winner pays</label><select value={f.sealed_pricing} onChange={e => set("sealed_pricing", e.target.value)}><option value="second">Second-highest bid (Vickrey)</option><option value="first">Their own bid</option></select></div>}
      <div><label>Provenance <small>(shown on the Authenticity Passport)</small></label><textarea rows={2} value={f.provenance} onChange={e => set("provenance", e.target.value)} /></div>
      <div><label>Duration</label><select value={f.duration} onChange={e => set("duration", e.target.value)}>
        <option value="90">90 seconds (demo)</option><option value="1800">30 minutes</option><option value="3600">1 hour</option><option value="86400">1 day</option><option value="259200">3 days</option><option value="604800">7 days</option></select></div>
      <div className="small muted">Buyers pay {money(800)} flat shipping. Platform fee {me.pro ? "5%" : "8%"} on the sale price, charged only when it sells.</div>
      <Err msg={err} />
      <div className="row"><button className="btn big grow" disabled={busy || !f.title || !f.start} onClick={() => save(false)}>Save draft</button>
        <button className="btn primary big grow" disabled={busy || !f.title || !f.start} onClick={() => save(true)}>Publish</button></div>
    </div>
  </div>;
}
