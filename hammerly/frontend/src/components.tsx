import { ReactNode, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Card, clock, money0 } from "./api";
import { useCountdown } from "./hooks";

export const FORMAT_LABEL = { english: "Ascending", dutch: "Dutch drop", sealed: "Sealed bid" } as const;

export function Timer({ endsAt, status }: { endsAt: string | null; status?: string }) {
  const s = useCountdown(endsAt);
  if (status && status !== "live") return <span className="timer muted">{status === "sold" ? "Sold" : status === "unsold" ? "Ended" : status}</span>;
  if (s == null) return null;
  return <span className={"timer" + (s < 30 ? " hot" : "")} aria-live="off">{clock(s)}</span>;
}

export function LotCard({ l }: { l: Card }) {
  const unit = l.format === "sealed" ? "Starts at" : l.format === "dutch" ? "Now" : l.bid_count ? "Current bid" : "Starting bid";
  return (
    <Link to={`/lots/${l.id}`} className="card lot">
      <div className="ph" style={{ backgroundImage: l.image ? `url(${l.image})` : undefined }}>
        <span className={"chip" + (l.status === "live" ? " live" : "")}>{l.status === "live" ? <><i className="dot" /> Live</> : l.status}</span>
        <span className="chip t">{FORMAT_LABEL[l.format]}</span>
      </div>
      <div className="body">
        <div className="small muted">{l.category} · {l.seller.name}{l.seller.verified ? " ✓" : ""}</div>
        <h3 style={{ margin: "4px 0 8px" }}>{l.title}</h3>
        <div className="row between"><div><div className="small muted">{unit}</div><div className="price" style={{ fontSize: 22 }}>{money0(l.price_cents)}</div></div>
          <div className="right"><Timer endsAt={l.ends_at} status={l.status} /><div className="small muted">{l.format === "sealed" ? "sealed" : `${l.bid_count} ${l.bid_count === 1 ? "bid" : "bids"}`}</div></div></div>
      </div>
    </Link>
  );
}

export function Sheet({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  useEffect(() => { const k = (e: KeyboardEvent) => e.key === "Escape" && onClose(); addEventListener("keydown", k); return () => removeEventListener("keydown", k); }, [onClose]);
  return <div className="sheet" onClick={onClose} role="dialog" aria-modal="true"><div className="panel" onClick={e => e.stopPropagation()}>{children}</div></div>;
}

/** Press and hold to confirm a bid. Keyboard users can press Enter instead. */
export function HoldButton({ label, onConfirm, disabled, ms = 700, className = "btn primary big block" }:
  { label: string; onConfirm: () => void; disabled?: boolean; ms?: number; className?: string }) {
  const bar = useRef<HTMLElement>(null); const t = useRef<any>(null);
  const [held, setHeld] = useState(false);
  const start = () => {
    if (disabled) return; setHeld(true);
    if (bar.current) { bar.current.style.transition = `transform ${ms}ms linear`; bar.current.style.transform = "scaleX(1)"; }
    t.current = setTimeout(() => { setHeld(false); reset(); onConfirm(); }, ms);
  };
  const reset = () => { clearTimeout(t.current); setHeld(false); if (bar.current) { bar.current.style.transition = "transform .15s"; bar.current.style.transform = "scaleX(0)"; } };
  return (
    <button className={className + " hold"} disabled={disabled} onPointerDown={start} onPointerUp={reset} onPointerLeave={reset} onPointerCancel={reset}
      onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); onConfirm(); } }} onContextMenu={e => e.preventDefault()}>
      <i ref={bar as any} />{held ? "Keep holding…" : label}
    </button>
  );
}

export function Empty({ children }: { children: ReactNode }) { return <div className="empty">{children}</div>; }
export function Err({ msg }: { msg?: string | null }) { return msg ? <div className="err" role="alert">{msg}</div> : null; }
export const Stars = ({ v, n }: { v: number; n: number }) => <span className="small">★ {v.toFixed(1)} <span className="muted">({n})</span></span>;
export function StatusChip({ s }: { s: string }) {
  const cls = ({ released: "good", sold: "good", verified: "good", shipped: "", held: "warn", pending: "warn", disputed: "bad", refunded: "bad", failed: "bad" } as any)[s] ?? "";
  return <span className={"chip " + cls}>{s}</span>;
}
