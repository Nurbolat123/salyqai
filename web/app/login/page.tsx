"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { api, errorText, setToken } from "@/lib/api";
import { DEV_LOGIN, devSign, rememberDevIin, signWithNcaLayer } from "@/lib/ncalayer";

type LoginResult = { token: string; missing_consents: string[] };

export default function LoginPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [iin, setIin] = useState("");
  const [name, setName] = useState("");

  async function login(sign: (nonce: string) => Promise<string>) {
    setBusy(true);
    setError(null);
    try {
      const { nonce } = await api<{ nonce: string }>("/auth/ecp/challenge", { method: "POST" });
      const signed_data = await sign(nonce);
      const r = await api<LoginResult>("/auth/ecp", { body: { nonce, signed_data } });
      setToken(r.token);
      router.replace(r.missing_consents.length ? "/consents/" : "/");
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="main" style={{ maxWidth: 480, margin: "8vh auto" }}>
      <h1>Salyq AI</h1>
      <p className="muted">ИИ-бухгалтер для ИП на упрощённой декларации. Вход — по ЭЦП.</p>
      {error && <div className="notice error">{error}</div>}
      <div className="card">
        <p>Запустите NCALayer и выберите ключ для аутентификации (AUTH_RSA или AUTH_GOST).</p>
        <button className="primary" disabled={busy} onClick={() => login((n) => signWithNcaLayer(n, "AUTHENTICATION"))}>
          Войти через NCALayer
        </button>
      </div>
      {DEV_LOGIN && (
        <div className="card">
          <div className="notice warn small">Режим разработки: вход без ЭЦП. В рабочей версии недоступен.</div>
          <label className="field">ИИН<input value={iin} onChange={(e) => setIin(e.target.value)} inputMode="numeric" maxLength={12} /></label>
          <label className="field">ФИО<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          <button disabled={busy || iin.length !== 12 || !name} onClick={() => { rememberDevIin(iin); login(async (n) => devSign(n, iin, name)); }}>
            Войти (dev)
          </button>
        </div>
      )}
    </div>
  );
}
