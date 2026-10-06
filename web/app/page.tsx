"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Shell from "@/components/Shell";
import { api, errorText } from "@/lib/api";
import { currentPeriod, date, percent, periodLabel, tenge } from "@/lib/format";

type Summary = {
  period: string; calculation_id: number; income_tiyn: number;
  regions: { region_code: string; income_tiyn: number; rate: string; tax_tiyn: number }[];
  tax: { total_payable_tiyn: number };
  social_monthly: { total_tiyn: number; payments_tiyn: Record<string, number> };
  limit: { limit_tiyn: number; used: string };
  pending: { count: number; amount_tiyn: number; possible_tax_tiyn: number };
  piggybank: { rate: string; per_100000_tenge_tiyn: number; reserve_tiyn: number };
  deadlines: { declaration_910: string; tax_payment: string; social_payments_next: string };
  config: { version: string; approved_by: string | null };
  warnings: { code: string; message: string }[];
};

const SOCIAL: Record<string, string> = { opv: "ОПВ", opvr: "ОПВР", so: "СО", vosms: "ВОСМС" };

function previous(p: string): string {
  const [y, h] = p.split("H").map(Number);
  return h === 2 ? `${y}H1` : `${y - 1}H2`;
}

export default function Dashboard() {
  const [period, setPeriod] = useState(currentPeriod());
  const [s, setS] = useState<Summary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setS(null);
    api<Summary>(`/tax/summary?period=${period}`).then(setS).catch((e) => setError(errorText(e)));
  }, [period]);

  const periods = [currentPeriod(), previous(currentPeriod()), previous(previous(currentPeriod()))];
  return (
    <Shell>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <h1>Налог за {periodLabel(period)}</h1>
        <select value={period} onChange={(e) => setPeriod(e.target.value)}>
          {periods.map((p) => <option key={p} value={p}>{periodLabel(p)}</option>)}
        </select>
      </div>
      {error && <div className="notice error">{error}</div>}
      {!s ? <p className="muted">Считаем…</p> : (
        <>
          {s.warnings.map((w) => <div key={w.code} className="notice warn">{w.message}</div>)}
          <div className="grid">
            <div className="card stat"><div className="label">Доход (подтверждённый)</div><div className="value">{tenge(s.income_tiyn)}</div></div>
            <div className="card stat"><div className="label">Налог к уплате</div><div className="value">{tenge(s.tax.total_payable_tiyn)}</div>
              <div className="small muted">до {date(s.deadlines.tax_payment)}</div></div>
            <div className="card stat"><div className="label">Соцплатежи за себя в месяц</div><div className="value">{tenge(s.social_monthly.total_tiyn)}</div>
              <div className="small muted">до {date(s.deadlines.social_payments_next)}</div></div>
            <div className="card stat"><div className="label">Копилка: отложить</div><div className="value">{tenge(s.piggybank.reserve_tiyn)}</div>
              <div className="small muted">{tenge(s.piggybank.per_100000_tenge_tiyn)} с каждых 100 000 ₸</div></div>
          </div>

          <div className="card">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <strong>Лимит упрощённого режима</strong>
              <span className="num">{percent(s.limit.used)} из {tenge(s.limit.limit_tiyn)}</span>
            </div>
            <div className="bar" style={{ marginTop: 8 }}><div style={{ width: `${Math.min(100, Number(s.limit.used) * 100)}%` }} /></div>
          </div>

          {s.pending.count > 0 && (
            <div className="card">
              <strong>Не размечено поступлений: {s.pending.count}</strong> на {tenge(s.pending.amount_tiyn)}.
              Если это доход, налог вырастет примерно на {tenge(s.pending.possible_tax_tiyn)}.{" "}
              <Link href="/transactions/">Разметить</Link>
            </div>
          )}

          <div className="card">
            <h2 style={{ marginTop: 0 }}>Подробнее</h2>
            <table>
              <tbody>
                {s.regions.map((r) => (
                  <tr key={r.region_code}><td>Регион {r.region_code}, ставка {percent(r.rate)}</td><td className="num">{tenge(r.income_tiyn)}</td><td className="num">{tenge(r.tax_tiyn)}</td></tr>
                ))}
                {Object.entries(s.social_monthly.payments_tiyn).map(([k, v]) => (
                  <tr key={k}><td>{SOCIAL[k] ?? k} в месяц</td><td /><td className="num">{tenge(v)}</td></tr>
                ))}
                <tr><td>Сдать форму 910.00</td><td /><td className="num">до {date(s.deadlines.declaration_910)}</td></tr>
              </tbody>
            </table>
            <p className="small muted">
              Параметры года: версия {s.config.version}{s.config.approved_by ? `, утверждены: ${s.config.approved_by}` : " — не утверждены экспертом"}.{" "}
              <Link href={`/explain/?id=${s.calculation_id}`}>Как посчитано</Link>
            </p>
          </div>
        </>
      )}
    </Shell>
  );
}
