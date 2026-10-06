"use client";

import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { date, tenge } from "@/lib/format";

type Tx = {
  id: number; date: string; direction: "in" | "out"; amount_tiyn: number; currency: string; amount_kzt_tiyn: number | null;
  counterparty: string; purpose: string; knp: string; category: string | null; confidence: number | null;
  status: "review" | "suggested" | "confirmed"; review_reason: string | null; account: string | null;
};
type List = { total: number; counts: Record<string, number>; items: Tx[] };

const TABS = [["review", "Проверить"], ["suggested", "Предложено"], ["confirmed", "Подтверждено"]] as const;
const REASONS: Record<string, string> = {
  no_consent: "нет согласия на авторазметку", no_category: "категория не определена",
  no_fx_rate: "нет курса НБ РК на дату", low_confidence: "не уверены в категории",
};

export default function TransactionsPage() {
  const [tab, setTab] = useState<string>("review");
  const [data, setData] = useState<List | null>(null);
  const [cats, setCats] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const load = () => api<List>(`/transactions?status=${tab}`).then(setData).catch((e) => setError(errorText(e)));
  useEffect(() => { api<{ categories: Record<string, string> }>("/transactions/categories").then((r) => setCats(r.categories)); }, []);
  useEffect(() => { setData(null); load(); }, [tab]); // eslint-disable-line react-hooks/exhaustive-deps

  async function confirm(tx: Tx, category: string) {
    setError(null);
    try {
      await api(`/transactions/${tx.id}`, { method: "PATCH", body: { category } });
      await load();
    } catch (e) {
      setError(errorText(e));
    }
  }

  async function acceptAll() {
    await api("/transactions/confirm-suggested", { body: {} });
    await load();
  }

  return (
    <Shell>
      <h1>Операции</h1>
      <div className="tabs">
        {TABS.map(([key, label]) => (
          <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>
            {label} {data ? `(${data.counts[key]})` : ""}
          </button>
        ))}
      </div>
      {error && <div className="notice error">{error}</div>}
      {tab === "suggested" && data && data.items.length > 0 && (
        <div className="card row" style={{ justifyContent: "space-between" }}>
          <span>Проверьте предложенные категории и примите их одним нажатием.</span>
          <button className="primary" onClick={acceptAll}>Принять все</button>
        </div>
      )}
      {!data ? <p className="muted">Загрузка…</p> : data.items.length === 0 ? <p className="muted">Здесь пусто.</p> : (
        <div className="card">
          <table>
            <thead><tr><th>Дата</th><th>Контрагент и назначение</th><th className="num">Сумма</th><th>Категория</th></tr></thead>
            <tbody>
              {data.items.map((tx) => (
                <tr key={tx.id}>
                  <td>{date(tx.date)}</td>
                  <td>
                    <div>{tx.counterparty || "—"}</div>
                    <div className="small muted">{tx.purpose}</div>
                    {tx.review_reason && <div className="small" style={{ color: "var(--warn)" }}>{REASONS[tx.review_reason]}</div>}
                  </td>
                  <td className="num" style={{ color: tx.direction === "in" ? "var(--ok)" : undefined }}>
                    {tx.direction === "in" ? "+" : "−"}{tenge(tx.amount_kzt_tiyn ?? tx.amount_tiyn)}
                    {tx.currency !== "KZT" && <div className="small muted">{(tx.amount_tiyn / 100).toFixed(2)} {tx.currency}</div>}
                  </td>
                  <td>
                    <select value={tx.category ?? ""} onChange={(e) => confirm(tx, e.target.value)}>
                      <option value="" disabled>выберите…</option>
                      {Object.entries(cats).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                    </select>
                    {tx.status !== "confirmed" && tx.category && (
                      <div><button className="small" style={{ marginTop: 6 }} onClick={() => confirm(tx, tx.category!)}>Подтвердить</button></div>
                    )}
                    {tx.confidence !== null && tx.status !== "confirmed" && (
                      <div className="small muted">уверенность {Math.round(tx.confidence * 100)}%</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Shell>
  );
}
