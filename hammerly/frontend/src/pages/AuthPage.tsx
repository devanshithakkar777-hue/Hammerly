import { FormEvent, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth";
import { Err } from "../components";

export default function AuthPage() {
  const { login, register } = useAuth(); const nav = useNavigate();
  const [mode, setMode] = useState<"in" | "up">("in");
  const [f, setF] = useState({ email: "", password: "", name: "" });
  const [err, setErr] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault(); setBusy(true); setErr(null);
    try { mode === "in" ? await login(f.email, f.password) : await register(f.email, f.password, f.name); nav("/"); }
    catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <form className="card stack" style={{ maxWidth: 420, margin: "40px auto" }} onSubmit={submit}>
      <h2>{mode === "in" ? "Welcome back" : "Create your account"}</h2>
      {mode === "up" && <div><label>Display name <small>(this is what other bidders see)</small></label><input value={f.name} onChange={e => setF({ ...f, name: e.target.value })} required minLength={2} /></div>}
      <div><label>Email</label><input type="email" value={f.email} onChange={e => setF({ ...f, email: e.target.value })} required /></div>
      <div><label>Password</label><input type="password" value={f.password} onChange={e => setF({ ...f, password: e.target.value })} required minLength={8} /></div>
      <Err msg={err} />
      <button className="btn primary big block" disabled={busy}>{mode === "in" ? "Sign in" : "Sign up"}</button>
      <button type="button" className="btn ghost block" onClick={() => setMode(mode === "in" ? "up" : "in")}>{mode === "in" ? "New here? Create an account" : "Have an account? Sign in"}</button>
    </form>
  );
}
