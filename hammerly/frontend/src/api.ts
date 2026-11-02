export class ApiError extends Error {
  code: string; status: number; extra: Record<string, any>;
  constructor(status: number, body: any) {
    const msg = body?.message || (typeof body?.detail === "string" ? body.detail : body?.detail?.[0]?.msg) || "Something went wrong.";
    super(msg); this.status = status; this.code = body?.code || "error"; this.extra = body || {};
  }
}

const KEY = "hammerly.token";
export const getToken = () => { try { return localStorage.getItem(KEY); } catch { return null; } };
export const setToken = (t: string | null) => { try { t ? localStorage.setItem(KEY, t) : localStorage.removeItem(KEY); } catch {} };

let skewMs = 0; // server time minus client time, so countdowns match the server's clock
export const syncClock = (serverTime?: string) => { if (serverTime) skewMs = new Date(serverTime).getTime() - Date.now(); };
export const serverNow = () => Date.now() + skewMs;

export async function api<T = any>(path: string, opts: { method?: string; body?: any; form?: FormData } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  const t = getToken(); if (t) headers.Authorization = `Bearer ${t}`;
  let body: BodyInit | undefined;
  if (opts.form) body = opts.form; else if (opts.body !== undefined) { headers["Content-Type"] = "application/json"; body = JSON.stringify(opts.body); }
  const r = await fetch(`/api${path}`, { method: opts.method || (body ? "POST" : "GET"), headers, body });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data);
  if (data?.server_time) syncClock(data.server_time);
  return data as T;
}

export function wsUrl(path: string) {
  const p = location.protocol === "https:" ? "wss" : "ws";
  return `${p}://${location.host}/api${path}`;
}

export const money = (c?: number | null, dp = 2) =>
  c == null ? "—" : "$" + (c / 100).toLocaleString(undefined, { minimumFractionDigits: dp, maximumFractionDigits: dp });
export const money0 = (c?: number | null) => (c != null && c % 100 === 0 ? money(c, 0) : money(c));

export function clock(sec: number) {
  if (sec <= 0) return "Ended";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  const p = (n: number) => String(n).padStart(2, "0");
  return d ? `${d}d ${h}h` : h ? `${h}:${p(m)}:${p(s)}` : `${p(m)}:${p(s)}`;
}

export type Seller = { id: number; name: string; trust_score: number; ratings_count: number; verified: boolean; pro: boolean };
export type Card = {
  id: number; title: string; category: string; format: "english" | "dutch" | "sealed"; status: string; image: string | null;
  condition_grade: number | null; price_cents: number; start_price_cents: number; min_next_cents: number; bid_count: number;
  shipping_cents: number; ends_at: string | null; starts_at: string | null; seconds_left: number | null; reserve_met: boolean | null;
  dutch: { floor_cents: number; drop_cents_per_min: number } | null; sealed_pricing: string | null; room_id: number | null; seller: Seller;
};
export type LedgerRow = { seq: number; kind: string; alias: string; amount_cents: number | null; commitment: string | null; salt: string | null; hash: string; prev_hash: string; at: string };
export type Detail = Card & {
  description: string; images: string[]; leader_alias: string | null; extensions_used: number; final_price_cents: number | null;
  winner_user_id: number | null; ledger: LedgerRow[]; settlement_hold: boolean | null;
  passport: { checks: { key: string; label: string; status: string; detail: string }[]; verified_count: number; total: number } | null;
  atlas: { count: number; median_cents: number | null; low_cents: number | null; high_cents: number | null; samples: { title: string; price_cents: number; sold_at: string; source: string }[] };
  me?: { leading: boolean; my_max_cents: number | null; sealed_placed: boolean; watching: boolean; alert_price_cents: number | null; is_seller: boolean };
  squads: Squad[];
};
export type Squad = { id: number; name: string; listing_id: number; status: string; owner_id: number; pledged_cents: number;
  members: { user_id: number; name: string; pledge_cents: number; share_bps: number }[]; ownership?: { user_id: number; share_bps: number }[]; order_id?: number };
export type Me = { id: number; name: string; email: string; is_seller: boolean; is_admin: boolean; calm_mode: boolean; daily_cap_cents: number; trust_score: number; ratings_count: number; verified: boolean; pro: boolean };
export type Order = { id: number; listing_id: number; title: string; image: string | null; amount_cents: number; shipping_cents: number; fee_cents: number; status: string;
  tracking: string; bundle_id: number | null; squad_id: number | null; release_at: string | null; created_at: string; buyer: Seller; seller: Seller;
  payout_paused: boolean; role: "buyer" | "seller"; my_share_cents: number | null; rated: boolean };
