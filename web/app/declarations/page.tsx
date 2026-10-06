"use client";

import { useEffect, useState } from "react";
import Shell, { useMe } from "@/components/Shell";
import { API_URL, api, errorText, getToken } from "@/lib/api";
import { currentPeriod, periodLabel, tenge } from "@/lib/format";
import { DEV_LOGIN, devIin, devSign, signWithNcaLayer } from "@/lib/ncalayer";

type Check = { code: string; ok: boolean; severity: string; message: string };
type Decl = {
  id: number; period: string; status: string; checks: Check[]; digest_to_sign: string | null; expert_reviewed: boolean;
  document: { income: { total_tiyn: number }; taxes: { ipn_tiyn: number; sn_tiyn: number; total_payable_tiyn: number };
    employees_avg: number; social_self: Record<string, number | string>[]; format_note: string };
};
const STATUS: Record<string, string> = { draft: "черновик", checked: "проверена", signed: "подписана", exported: "выгружена", superseded: "заменена" };

function lastFinished(): string {
  const p = currentPeriod();
  const [y, h] = p.split("H").map(Number);
  return h === 2 ? `${y}H1` : `${y - 1}H2`;
}

function DeclarationsView() {
  const me = useMe();
  const [period, setPeriod] = useState(lastFinished());
  const [decl, setDecl] = useState<Decl | null>(null);
  const [list, setList] = useState<{ id: number; period: string; status: string }[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const loadList = () => api<typeof list>("/declarations/910").then(setList);
  useEffect(() => { loadList(); }, []);

  async function run(fn: () => Promise<Decl>) {
    setBusy(true);
    setError(null);
    try {
      setDecl(await fn());
      await loadList();
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  const build = () => run(() => api<Decl>("/declarations/910", { body: { period } }));
  const open = (id: number) => run(() => api<Decl>(`/declarations/910/${id}`));
  const sign = (dev: boolean) => run(async () => {
    const digest = decl!.digest_to_sign!;
    const signed_data = dev ? devSign(digest, devIin(), me?.full_name ?? "") : await signWithNcaLayer(digest, "SIGNATURE");
    return api<Decl>(`/declarations/910/${decl!.id}/sign`, { body: { signed_data } });
  });

  async function exportXml() {
    const res = await fetch(`${API_URL}/declarations/910/${decl!.id}/export`, { headers: { Authorization: `Bearer ${getToken()}` } });
    if (!res.ok) { setError((await res.json()).detail); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: `910_${decl!.period}.xml` });
    a.click();
    URL.revokeObjectURL(url);
    open(decl!.id);
  }

  return (
    <>
      <h1>Форма 910.00</h1>
      <div className="card row">
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          {[lastFinished(), currentPeriod()].map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
        </select>
        <button className="primary" disabled={busy} onClick={build}>Собрать черновик</button>
        {list.length > 0 && (
          <span className="small muted">Ранее: {list.slice(0, 5).map((d) => (
            <a key={d.id} href="#" onClick={(e) => { e.preventDefault(); open(d.id); }} style={{ marginRight: 8 }}>
              {periodLabel(d.period)} ({STATUS[d.status]})</a>))}
          </span>
        )}
      </div>
      {error && <div className="notice error">{error}</div>}
      {decl && (
        <>
          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>{periodLabel(decl.period)}</strong><span className="badge">{STATUS[decl.status]}</span>
            </div>
            <table style={{ marginTop: 8 }}><tbody>
              <tr><td>Доход за налоговый период</td><td className="num">{tenge(decl.document.income.total_tiyn)}</td></tr>
              <tr><td>Среднесписочная численность работников</td><td className="num">{decl.document.employees_avg}</td></tr>
              <tr><td>ИПН</td><td className="num">{tenge(decl.document.taxes.ipn_tiyn)}</td></tr>
              {decl.document.taxes.sn_tiyn > 0 && <tr><td>Социальный налог</td><td className="num">{tenge(decl.document.taxes.sn_tiyn)}</td></tr>}
              <tr><td><b>Итого к уплате</b></td><td className="num"><b>{tenge(decl.document.taxes.total_payable_tiyn)}</b></td></tr>
            </tbody></table>
            <p className="small muted">{decl.document.format_note}</p>
          </div>
          <div className="card">
            <h2 style={{ marginTop: 0 }}>Проверки</h2>
            {decl.checks.map((c) => (
              <div key={c.code} className="row small" style={{ marginBottom: 4 }}>
                <span className={`badge ${c.ok ? "ok" : c.severity === "error" ? "bad" : ""}`}>{c.ok ? "✓" : c.severity === "error" ? "✕" : "!"}</span>{c.message}
              </div>
            ))}
          </div>
          {decl.status !== "draft" && decl.status !== "superseded" && <div className="card row">
            {decl.status === "checked" && !decl.expert_reviewed && <span className="muted">Ждёт проверки экспертом перед подписью.</span>}
            {decl.status === "checked" && (
              <>
                <button className="primary" disabled={busy} onClick={() => sign(false)}>Подписать ЭЦП (NCALayer)</button>
                {DEV_LOGIN && <button disabled={busy} onClick={() => sign(true)}>Подписать (dev)</button>}
              </>
            )}
            {(decl.status === "signed" || decl.status === "exported") && (
              <button className="primary" onClick={exportXml}>Скачать файл для Кабинета налогоплательщика</button>
            )}
          </div>}
          {decl.status === "draft" && <div className="notice warn">Исправьте отмеченные проверки и соберите черновик заново.</div>}
        </>
      )}
    </>
  );
}

export default function DeclarationsPage() {
  return <Shell><DeclarationsView /></Shell>;
}
