"use client";

import { useState } from "react";
import Shell, { useMe } from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { parseTenge, tenge } from "@/lib/format";

function ProfileForm() {
  const me = useMe()!;
  const [form, setForm] = useState({
    region_code: me.region_code ?? "", activity_code: me.activity_code ?? "",
    ip_registered_on: me.ip_registered_on ?? "", employees_count: String(me.employees_count),
    declared_income: me.declared_income_tiyn === null ? "" : String(me.declared_income_tiyn / 100),
  });
  const [notify, setNotify] = useState({ email: "", telegram_chat_id: "" });
  const [msg, setMsg] = useState<{ kind: "warn" | "error"; text: string } | null>(null);
  const set = (k: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [k]: e.target.value });

  async function save() {
    const declared = form.declared_income.trim() ? parseTenge(form.declared_income) : null;
    if (form.declared_income.trim() && declared === null) { setMsg({ kind: "error", text: "Неверная сумма дохода" }); return; }
    try {
      await api("/me", { method: "PATCH", body: {
        region_code: form.region_code || null, activity_code: form.activity_code || null,
        ip_registered_on: form.ip_registered_on || null, employees_count: Number(form.employees_count) || 0,
        declared_income_tiyn: declared,
      } });
      setMsg({ kind: "warn", text: "Сохранено" });
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  }

  async function saveNotify() {
    try {
      const body: Record<string, string> = {};
      if (notify.email) body.email = notify.email;
      if (notify.telegram_chat_id) body.telegram_chat_id = notify.telegram_chat_id;
      const r = await api<Record<string, boolean>>("/me/notifications", { method: "PATCH", body });
      setMsg({ kind: "warn", text: `Напоминания: e-mail ${r.email ? "вкл" : "выкл"}, Telegram ${r.telegram ? "вкл" : "выкл"}` });
    } catch (e) {
      setMsg({ kind: "error", text: errorText(e) });
    }
  }

  return (
    <>
      <h1>Профиль</h1>
      {msg && <div className={`notice ${msg.kind}`}>{msg.text}</div>}
      <div className="card">
        <p><b>{me.full_name}</b> <span className="muted">ИИН {me.iin_masked}</span></p>
        <label className="field">Регион (код КАТО, 9 цифр)<input value={form.region_code} onChange={set("region_code")} placeholder="750000000 — Алматы" /></label>
        <label className="field">Вид деятельности (ОКЭД, 5 цифр)<input value={form.activity_code} onChange={set("activity_code")} /></label>
        <label className="field">Дата регистрации ИП<input type="date" value={form.ip_registered_on} onChange={set("ip_registered_on")} /></label>
        <label className="field">Работников<input type="number" min={0} value={form.employees_count} onChange={set("employees_count")} /></label>
        <label className="field">Заявленный доход в месяц для соцплатежей, ₸
          <input value={form.declared_income} onChange={set("declared_income")} placeholder="пусто — минимальный (1 МЗП)" />
          {me.declared_income_tiyn !== null && <span className="small muted">сейчас {tenge(me.declared_income_tiyn)}</span>}
        </label>
        <button className="primary" onClick={save}>Сохранить</button>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Напоминания о сроках</h2>
        <label className="field">E-mail<input value={notify.email} onChange={(e) => setNotify({ ...notify, email: e.target.value })} /></label>
        <label className="field">Telegram chat id<input value={notify.telegram_chat_id} onChange={(e) => setNotify({ ...notify, telegram_chat_id: e.target.value })} /></label>
        <button onClick={saveNotify}>Сохранить каналы</button>
      </div>
      <a href="/consents/">Согласия</a>
    </>
  );
}

export default function ProfilePage() {
  return <Shell><ProfileForm /></Shell>;
}
