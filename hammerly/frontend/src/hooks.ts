import { useEffect, useRef, useState } from "react";
import { serverNow, wsUrl } from "./api";

/** Seconds left until `endsIso`, ticking against the server's clock. */
export function useCountdown(endsIso: string | null | undefined) {
  const [, set] = useState(0);
  useEffect(() => { const t = setInterval(() => set(x => x + 1), 250); return () => clearInterval(t); }, []);
  if (!endsIso) return null;
  return Math.max(0, (new Date(endsIso).getTime() - serverNow()) / 1000);
}

/** Subscribe to a channel; reconnects with backoff. Returns a `send` function. */
export function useChannel(path: string | null, onEvent: (e: any) => void) {
  const handler = useRef(onEvent); handler.current = onEvent;
  const sock = useRef<WebSocket | null>(null);
  useEffect(() => {
    if (!path) return;
    let closed = false, retry = 500, timer: any, ping: any;
    const open = () => {
      const ws = new WebSocket(wsUrl(path)); sock.current = ws;
      ws.onopen = () => { retry = 500; ping = setInterval(() => ws.readyState === 1 && ws.send('{"type":"ping"}'), 20000); };
      ws.onmessage = e => handler.current(JSON.parse(e.data));
      ws.onclose = () => { clearInterval(ping); if (!closed) { timer = setTimeout(open, retry); retry = Math.min(retry * 2, 8000); } };
    };
    open();
    return () => { closed = true; clearTimeout(timer); clearInterval(ping); sock.current?.close(); };
  }, [path]);
  return (msg: object) => { if (sock.current?.readyState === 1) sock.current.send(JSON.stringify(msg)); };
}

export function useAsync<T>(fn: () => Promise<T>, deps: any[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const reload = () => {
    setLoading(true);
    fn().then(d => { setData(d); setError(null); }).catch(e => setError(e.message)).finally(() => setLoading(false));
  };
  useEffect(reload, deps); // eslint-disable-line
  return { data, error, loading, reload, setData };
}
