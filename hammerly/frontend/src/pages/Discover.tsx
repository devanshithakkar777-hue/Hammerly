import { useState } from "react";
import { api, Card } from "../api";
import { Empty, Err, LotCard } from "../components";
import { useAsync } from "../hooks";

const CATS = ["", "cameras", "electronics", "books", "furniture", "watches", "collectibles", "music", "home"];

export default function Discover() {
  const [cat, setCat] = useState(""); const [fmt, setFmt] = useState(""); const [q, setQ] = useState(""); const [sort, setSort] = useState("ending");
  const { data, error, loading } = useAsync(() => api<{ items: Card[] }>(`/listings?sort=${sort}${cat ? `&category=${cat}` : ""}${fmt ? `&format=${fmt}` : ""}${q ? `&q=${encodeURIComponent(q)}` : ""}`), [cat, fmt, q, sort]);
  return (
    <div className="stack">
      <div><h1>Bid with proof, not hope.</h1><p className="muted">Every bid is hash-chained, funds are escrowed, and timers extend fairly.</p></div>
      <div className="row">
        <input style={{ maxWidth: 260 }} placeholder="Search lots" value={q} onChange={e => setQ(e.target.value)} aria-label="Search" />
        <select style={{ width: "auto" }} value={cat} onChange={e => setCat(e.target.value)} aria-label="Category">{CATS.map(c => <option key={c} value={c}>{c || "All categories"}</option>)}</select>
        <select style={{ width: "auto" }} value={fmt} onChange={e => setFmt(e.target.value)} aria-label="Format">
          <option value="">All formats</option><option value="english">Ascending</option><option value="dutch">Dutch drop</option><option value="sealed">Sealed bid</option></select>
        <select style={{ width: "auto" }} value={sort} onChange={e => setSort(e.target.value)} aria-label="Sort">
          <option value="ending">Ending soonest</option><option value="newest">Newest</option><option value="price_low">Price: low</option><option value="price_high">Price: high</option></select>
      </div>
      <Err msg={error} />
      {loading && !data ? <Empty>Loading…</Empty> : data && data.items.length === 0 ? <Empty>No live lots match. Try another filter.</Empty> :
        <div className="grid">{data?.items.map(l => <LotCard key={l.id} l={l} />)}</div>}
    </div>
  );
}
