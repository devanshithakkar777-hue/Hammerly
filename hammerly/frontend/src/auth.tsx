import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { api, getToken, Me, setToken, wsUrl } from "./api";

type Toast = { id: number; title: string; body?: string };
type Ctx = {
  me: Me | null; ready: boolean; login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name: string) => Promise<void>; logout: () => void; refresh: () => Promise<void>;
  setMe: (m: Me) => void; toast: (title: string, body?: string) => void; toasts: Toast[]; dismiss: (id: number) => void;
  walletTick: number; bumpWallet: () => void;
};
const C = createContext<Ctx>(null as any);
export const useAuth = () => useContext(C);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [ready, setReady] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const [walletTick, setWalletTick] = useState(0);
  const n = useRef(0);

  const toast = useCallback((title: string, body?: string) => {
    const id = ++n.current; setToasts(t => [...t, { id, title, body }]);
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 6000);
  }, []);
  const dismiss = (id: number) => setToasts(t => t.filter(x => x.id !== id));

  const refresh = useCallback(async () => {
    if (!getToken()) { setMe(null); setReady(true); return; }
    try { setMe(await api<Me>("/me")); } catch { setToken(null); setMe(null); }
    setReady(true);
  }, []);
  useEffect(() => { refresh(); }, [refresh]);

  // Personal notification stream. Calm Mode silences everything except wins and payouts.
  useEffect(() => {
    if (!me) return;
    const ws = new WebSocket(wsUrl(`/ws/me?token=${getToken()}`));
    ws.onmessage = e => {
      const m = JSON.parse(e.data);
      if (m.type !== "notification") return;
      setWalletTick(x => x + 1);
      if (me.calm_mode && !["won", "payout", "shipped"].includes(m.kind)) return;
      toast(m.title, m.body);
    };
    return () => ws.close();
  }, [me?.id, me?.calm_mode, toast]);

  const login = async (email: string, password: string) => {
    const r = await api("/auth/login", { body: { email, password } }); setToken(r.token); setMe(r.user);
  };
  const register = async (email: string, password: string, display_name: string) => {
    const r = await api("/auth/register", { body: { email, password, display_name } }); setToken(r.token); setMe(r.user);
  };
  const logout = () => { setToken(null); setMe(null); };
  return <C.Provider value={{ me, ready, login, register, logout, refresh, setMe, toast, toasts, dismiss, walletTick, bumpWallet: () => setWalletTick(x => x + 1) }}>{children}</C.Provider>;
}
