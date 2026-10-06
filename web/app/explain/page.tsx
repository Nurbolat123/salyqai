"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { date, tenge } from "@/lib/format";

type Explain = {
  calculation_id: number; period: string; key_factors: string[];
  steps: { code: string; description: string; formula: string; result_display: string }[];
  operations: { id: string; amount_tiyn: number; date: string | null; counterparty: string | null; purpose: string | null }[];
  rights: string;
};

function ExplainView() {
  const id = useSearchParams().get("id");
  const [e, setE] = useState<Explain | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sent, setSent] = useState(false);

  useEffect(() => { if (id) api<Explain>(`/tax/calculations/${id}/explain`).then(setE).catch((x) => setError(errorText(x))); }, [id]);

  async function object() {
    try {
      await api("/objections", { body: { subject_ref: `tax_calculation:${id}`, text } });
      setSent(true);
    } catch (x) {
      setError(errorText(x));
    }
  }

  if (error) return <div className="notice error">{error}</div>;
  if (!e) return <p className="muted">Загрузка…</p>;
  return (
    <>
      <h1>Как посчитано</h1>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Ключевые факторы</h2>
        <ul>{e.key_factors.map((f) => <li key={f}>{f}</li>)}</ul>
      </div>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Поступления, вошедшие в доход ({e.operations.length})</h2>
        <table><tbody>
          {e.operations.map((o) => (
            <tr key={o.id}><td>{date(o.date)}</td><td>{o.counterparty}<div className="small muted">{o.purpose}</div></td><td className="num">{tenge(o.amount_tiyn)}</td></tr>
          ))}
        </tbody></table>
      </div>
      <details className="card"><summary>Все шаги расчёта</summary>
        <table><tbody>
          {e.steps.map((s) => <tr key={s.code}><td>{s.description}<div className="small muted">{s.formula}</div></td><td className="num">{s.result_display}</td></tr>)}
        </tbody></table>
      </details>
      <div className="card">
        <h2 style={{ marginTop: 0 }}>Не согласны?</h2>
        <p className="small">{e.rights}</p>
        {sent ? <div className="notice warn">Возражение отправлено. Ответ появится в разделе «Обращения».</div> : (
          <>
            <textarea value={text} onChange={(x) => setText(x.target.value)} placeholder="Что, по-вашему, посчитано неверно?" />
            <button className="primary" disabled={text.trim().length < 3} onClick={object} style={{ marginTop: 8 }}>Возразить</button>
          </>
        )}
      </div>
    </>
  );
}

export default function ExplainPage() {
  return <Shell><Suspense fallback={<p className="muted">Загрузка…</p>}><ExplainView /></Suspense></Shell>;
}
