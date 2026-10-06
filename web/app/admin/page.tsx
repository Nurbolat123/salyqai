"use client";

import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { date, tenge } from "@/lib/format";

type Queue = {
  objections: { id: number; user_id: number; subject_ref: string; due_at: string; overdue: boolean; seconds_left: number }[];
  questions: { id: number; user_id: number; created_at: string }[];
  declarations: { id: number; user_id: number; period: string }[];
};
type Opened = { kind: "objection" | "question" | "declaration_910"; id: number; data: Record<string, unknown> };

function hoursLeft(s: number) {
  return s <= 0 ? "просрочено" : `${Math.floor(s / 3600)} ч`;
}

function AdminView() {
  const [q, setQ] = useState<Queue | null>(null);
  const [opened, setOpened] = useState<Opened | null>(null);
  const [reply, setReply] = useState("");
  const [yaml, setYaml] = useState("");
  const [msg, setMsg] = useState<string | null>(null);

  const load = () => api<Queue>("/admin/queue").then(setQ).catch((e) => setMsg(errorText(e)));
  useEffect(() => { load(); }, []);

  async function open(kind: Opened["kind"], id: number, userId: number) {
    setMsg(null);
    setReply("");
    try {
      await api("/admin/access", { body: { user_id: userId, reason_ref: `${kind}:${id}` } });
      const path = kind === "objection" ? `/admin/objections/${id}` : kind === "question" ? `/admin/questions/${id}` : `/admin/declarations/${id}`;
      setOpened({ kind, id, data: await api(path) });
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  async function act() {
    if (!opened) return;
    try {
      if (opened.kind === "objection") await api(`/admin/objections/${opened.id}/resolve`, { body: { resolution: reply } });
      if (opened.kind === "question") await api(`/admin/questions/${opened.id}/answer`, { body: { answer: reply } });
      if (opened.kind === "declaration_910") await api(`/admin/declarations/${opened.id}/review`, { method: "POST" });
      setOpened(null);
      setMsg("Готово");
      load();
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  async function submitConfig() {
    try {
      const v = await api<{ id: number; version: string }>("/admin/tax-config", { body: { yaml } });
      if (confirm(`Версия ${v.version} загружена как черновик. Утвердить сейчас?`)) {
        await api(`/admin/tax-config/${v.id}/approve`, { method: "POST" });
      }
      setMsg(`Версия ${v.version} сохранена`);
    } catch (e) {
      setMsg(errorText(e));
    }
  }

  if (!q) return <p className="muted">{msg ?? "Загрузка…"}</p>;
  return (
    <>
      <h1>Кабинет эксперта</h1>
      {msg && <div className="notice warn">{msg}</div>}
      <p className="small muted">Данные клиента открываются только по обращению на 2 часа; каждый просмотр записывается в журнал.</p>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Возражения (ст. 19-1, 3 рабочих дня)</h2>
        {q.objections.length === 0 && <p className="muted">Нет</p>}
        {q.objections.map((o) => (
          <div key={o.id} className="row small" style={{ marginBottom: 6 }}>
            <span className={`badge ${o.overdue ? "bad" : ""}`}>{hoursLeft(o.seconds_left)}</span>
            <span>#{o.id} клиент {o.user_id}, {o.subject_ref}, до {date(o.due_at)}</span>
            <button onClick={() => open("objection", o.id, o.user_id)}>Открыть</button>
          </div>
        ))}
        <h2>Вопросы</h2>
        {q.questions.length === 0 && <p className="muted">Нет</p>}
        {q.questions.map((x) => (
          <div key={x.id} className="row small" style={{ marginBottom: 6 }}>
            <span>#{x.id} клиент {x.user_id} от {date(x.created_at)}</span>
            <button onClick={() => open("question", x.id, x.user_id)}>Открыть</button>
          </div>
        ))}
        <h2>Декларации на проверку</h2>
        {q.declarations.length === 0 && <p className="muted">Нет</p>}
        {q.declarations.map((d) => (
          <div key={d.id} className="row small" style={{ marginBottom: 6 }}>
            <span>#{d.id} клиент {d.user_id}, {d.period}</span>
            <button onClick={() => open("declaration_910", d.id, d.user_id)}>Открыть</button>
          </div>
        ))}
      </div>
      {opened && (
        <div className="card">
          <h2 style={{ marginTop: 0 }}>{opened.kind === "declaration_910" ? "Декларация" : "Обращение"} #{opened.id}</h2>
          {opened.kind === "declaration_910" ? (
            <DeclarationPreview data={opened.data} />
          ) : (
            <p style={{ whiteSpace: "pre-wrap" }}>{String(opened.data.text)}</p>
          )}
          {opened.kind !== "declaration_910" && <textarea value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Ответ клиенту" />}
          <button className="primary" style={{ marginTop: 8 }} disabled={opened.kind !== "declaration_910" && !reply.trim()} onClick={act}>
            {opened.kind === "declaration_910" ? "Проверено, можно подписывать" : "Отправить ответ"}
          </button>
        </div>
      )}
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Конфигурация ставок года (YAML)</h2>
        <p className="small muted">Формат — как в salyq/tax/rates/2026.yaml. Новая версия действует после утверждения.</p>
        <textarea style={{ minHeight: 160, fontFamily: "monospace" }} value={yaml} onChange={(e) => setYaml(e.target.value)} />
        <button disabled={!yaml.trim()} onClick={submitConfig} style={{ marginTop: 8 }}>Загрузить версию</button>
      </div>
    </>
  );
}

function DeclarationPreview({ data }: { data: Record<string, unknown> }) {
  const doc = data.document as { income: { total_tiyn: number }; taxes: { total_payable_tiyn: number } };
  const checks = data.checks as { code: string; ok: boolean; message: string }[];
  return (
    <>
      <p>Доход {tenge(doc.income.total_tiyn)}, налог {tenge(doc.taxes.total_payable_tiyn)}</p>
      <ul className="small">{checks.map((c) => <li key={c.code}>{c.ok ? "✓" : "✕"} {c.message}</li>)}</ul>
    </>
  );
}

export default function AdminPage() {
  return <Shell><AdminView /></Shell>;
}
