"use client";

import { useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { date } from "@/lib/format";

type Result = {
  statement_id: number; account: string; period_from: string | null; period_to: string | null;
  parsed: number; inserted: number; duplicates: number; skipped: { row: number; reason: string }[];
};

export default function StatementsPage() {
  const [bank, setBank] = useState("kaspi");
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function upload() {
    if (!file) return;
    setBusy(true);
    setError(null);
    setResult(null);
    const form = new FormData();
    form.append("file", file);
    form.append("bank", bank);
    try {
      setResult(await api<Result>("/statements", { form }));
    } catch (e) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell>
      <h1>Загрузить выписку</h1>
      <div className="card">
        <p className="small muted">Kaspi Business: выписка по счёту в PDF или XLSX. Повторная загрузка того же периода дублей не создаёт.</p>
        <div className="row">
          <select value={bank} onChange={(e) => setBank(e.target.value)}>
            <option value="kaspi">Kaspi Business</option>
            <option value="halyk" disabled>Halyk (скоро)</option>
            <option value="freedom" disabled>Freedom (скоро)</option>
          </select>
          <input type="file" accept=".pdf,.xlsx,.csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <button className="primary" disabled={!file || busy} onClick={upload}>{busy ? "Загружаем…" : "Загрузить"}</button>
        </div>
      </div>
      {error && <div className="notice error">{error}</div>}
      {result && (
        <div className="card">
          <strong>Счёт {result.account}</strong>, период {date(result.period_from)} — {date(result.period_to)}
          <p>Новых операций: <b>{result.inserted}</b>, уже были загружены: {result.duplicates}.</p>
          {result.skipped.length > 0 && (
            <details><summary>Не удалось разобрать строк: {result.skipped.length}</summary>
              <ul className="small">{result.skipped.map((s) => <li key={s.row}>Строка {s.row}: {s.reason}</li>)}</ul>
            </details>
          )}
          <a className="button primary" href="/transactions/">Проверить операции</a>
        </div>
      )}
    </Shell>
  );
}
