"""Налоговая сводка ИП за полугодие из подтверждённых операций (ТЗ 4.4, 4.6, API /tax/summary).

Облагаемый доход — только подтверждённые пользователем поступления категории
«доход от реализации», в тенге по курсу НБ РК. Неподтверждённые поступления
показываются отдельно как «возможный доход»: в налог они не входят, пока
пользователь их не разметит.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth.clock import utcnow
from salyq.categorize.categories import TAXABLE_INCOME
from salyq.categorize.service import user_transactions
from salyq.models import TaxCalculation, Transaction, User
from salyq.money import mul_rate
from salyq.tax.config_store import active_config
from salyq.tax.engine import IncomeItem, RegionIncome, calculate_self_social, calculate_simplified
from salyq.tax.region_rates import find_region_rate

NO_REGION = "не указан"
_PERIOD_RE = re.compile(r"^(\d{4})H([12])$")


class PeriodError(ValueError):
    pass


@dataclass(frozen=True)
class Period:
    year: int
    half: int
    start: date
    end: date

    @property
    def code(self) -> str:
        return f"{self.year}H{self.half}"


def parse_period(code: str) -> Period:
    m = _PERIOD_RE.match(code)
    if not m:
        raise PeriodError("период в формате 2026H1 или 2026H2")
    year, half = int(m.group(1)), int(m.group(2))
    start, end = (date(year, 1, 1), date(year, 6, 30)) if half == 1 else (date(year, 7, 1), date(year, 12, 31))
    return Period(year, half, start, end)


def current_period(today: date) -> Period:
    return parse_period(f"{today.year}H{1 if today.month <= 6 else 2}")


def _income_txs(session: Session, user: User, start: date, end: date) -> list[Transaction]:
    return list(session.scalars(
        user_transactions(user)
        .where(Transaction.direction == "in", Transaction.op_date >= start, Transaction.op_date <= end)
        .order_by(Transaction.op_date, Transaction.id)
    ))


def _is_taxable(tx: Transaction) -> bool:
    return tx.confirmed_by_user and tx.category in TAXABLE_INCOME and tx.amount_kzt_tiyn is not None


def _next_monthly_deadline(today: date, day: int, months_after: int) -> date:
    """Ближайший срок соцплатежей: day-е число, months_after месяцев после отчётного месяца."""
    candidate = date(today.year, today.month, day)
    if candidate < today:
        idx = today.year * 12 + today.month  # следующий месяц
        candidate = date(idx // 12, idx % 12 + 1, day)
    return candidate


def tax_summary(session: Session, user: User, period: Period, today: date | None = None) -> dict[str, Any]:
    today = today or utcnow().date()
    loaded = active_config(session, period.year)
    cfg = loaded.config

    txs = _income_txs(session, user, period.start, period.end)
    taxable = [t for t in txs if _is_taxable(t)]
    pending = [t for t in txs if not t.confirmed_by_user and t.category in (None, *TAXABLE_INCOME)]

    ytd_before = 0
    if period.half == 2:
        ytd_before = sum(t.amount_kzt_tiyn for t in _income_txs(session, user, date(period.year, 1, 1),
                                                                date(period.year, 6, 30)) if _is_taxable(t))

    by_region: dict[str, list[IncomeItem]] = {}
    for t in taxable:
        by_region.setdefault(t.region_code or user.region_code or NO_REGION, []).append(
            IncomeItem(str(t.id), t.amount_kzt_tiyn))
    if not by_region:
        by_region[user.region_code or NO_REGION] = []

    regions, rate_inputs = [], []
    for code, items in by_region.items():
        found = find_region_rate(session, code, period.end, user.activity_code or "") if code != NO_REGION else None
        rate_inputs.append([code, str(found.rate) if found else None])
        regions.append(RegionIncome.from_items(
            code, items, rate=found.rate if found else None, rate_source=found.source_url if found else None))

    monthly_income = user.declared_income_tiyn if user.declared_income_tiyn is not None else cfg.mzp_tiyn
    social = calculate_self_social(year=period.year, declared_income_tiyn=monthly_income, config=loaded)
    so_half = social.payments_tiyn.get("so", 0) * 6

    result = calculate_simplified(year=period.year, half=period.half, regions=regions,
                                  ytd_income_before_tiyn=ytd_before, so_accrued_tiyn=so_half, config=loaded)
    warnings = [w.__dict__ for w in result.warnings]
    if user.region_code is None:
        warnings.insert(0, {"code": "PROFILE_REGION_MISSING",
                            "message": "Укажите регион в профиле — от него зависит ставка налога."})
    if pending:
        warnings.append({"code": "UNCONFIRMED_INCOME",
                         "message": f"{len(pending)} поступлений ещё не размечены — они не вошли в доход."})

    # Копилка (ТЗ 4.6): сколько откладывать с каждого дохода
    effective_rate = (Decimal(result.tax_computed_tiyn) / result.income_tiyn) if result.income_tiyn else (
        regions[0].rate or cfg.simplified.rate)
    pending_sum = sum(t.amount_kzt_tiyn or 0 for t in pending)

    calc = _save_calculation(session, user, period, result, loaded, ytd_before, so_half, rate_inputs)

    dl = cfg.deadlines.social_payments
    return {
        "period": period.code,
        "calculation_id": calc.id,
        "income_tiyn": result.income_tiyn,
        "regions": [{"region_code": r.region_code, "income_tiyn": r.income_tiyn, "rate": str(r.rate),
                     "tax_tiyn": r.tax_tiyn} for r in result.regions],
        "tax": {"components_tiyn": result.components_tiyn, "total_payable_tiyn": result.total_payable_tiyn},
        "social_monthly": {"declared_income_tiyn": monthly_income, "payments_tiyn": social.payments_tiyn,
                           "total_tiyn": social.total_tiyn},
        "social_half_year_tiyn": social.total_tiyn * 6,
        "limit": {"limit_tiyn": result.limit_tiyn, "used": str(result.limit_used)},
        "pending": {"count": len(pending), "amount_tiyn": pending_sum,
                    "possible_tax_tiyn": mul_rate(pending_sum, effective_rate)},
        "piggybank": {
            "rate": str(effective_rate.quantize(Decimal("0.0001"))),
            "per_100000_tenge_tiyn": mul_rate(100_000_00, effective_rate),
            "reserve_tiyn": result.total_payable_tiyn + mul_rate(pending_sum, effective_rate),
        },
        "deadlines": {
            **{k: v.isoformat() for k, v in result.deadlines.items()},
            "social_payments_next": _next_monthly_deadline(today, dl.day, dl.months_after).isoformat(),
        },
        "config": {"year": cfg.year, "version": cfg.version, "approved_by": cfg.approved_by},
        "warnings": warnings,
    }


def _save_calculation(session, user, period, result, loaded, ytd_before, so_half, rate_inputs) -> TaxCalculation:
    trace = result.trace.to_dict()
    inputs = json.dumps({
        "period": period.code, "config": loaded.sha256, "ops": trace["operations"],
        "ytd": ytd_before, "so": so_half, "rates": rate_inputs,
    }, sort_keys=True, ensure_ascii=False)
    inputs_hash = hashlib.sha256(inputs.encode()).hexdigest()
    last = session.scalar(
        select(TaxCalculation)
        .where(TaxCalculation.user_id == user.id, TaxCalculation.period == period.code)
        .order_by(TaxCalculation.id.desc())
    )
    if last is not None and last.inputs_hash == inputs_hash:
        return last
    calc = TaxCalculation(
        user_id=user.id, period=period.code, income_tiyn=result.income_tiyn, tax_tiyn=result.total_payable_tiyn,
        config_version=loaded.config.version, config_sha256=loaded.sha256, inputs_hash=inputs_hash,
        trace_json={**trace, "warnings": [w.__dict__ for w in result.warnings]}, created_at=utcnow(),
    )
    session.add(calc)
    session.commit()
    return calc


def explain(session: Session, user: User, calc: TaxCalculation) -> dict[str, Any]:
    """Экран «Как посчитано»: ключевые факторы, шаги и операции (ст. 19-1 Закона № 94-V, ст. 43 ЦК)."""
    trace = calc.trace_json
    ids = [int(op["ref"]) for op in trace.get("operations", [])]
    txs = {t.id: t for t in session.scalars(user_transactions(user).where(Transaction.id.in_(ids)))} if ids else {}
    steps = {s["code"]: s for s in trace["steps"]}
    rate_steps = [s for s in trace["steps"] if s["code"].endswith(".rate")]
    factors = [
        f"Доход за {calc.period}: {steps['income']['result_display']} — сумма {len(ids)} подтверждённых вами поступлений.",
        *[f"{s['description']}: {s['result_display']}" for s in rate_steps],
        f"Итого налог к уплате: {steps['total_payable']['result_display']}.",
        f"Использовано лимита упрощённого режима: {Decimal(steps['income_limit_used']['result']) * 100:.2f}%.",
        f"Параметры {trace['config_year']} года, версия {trace['config_version']}"
        + (f", утверждены: {trace['config_approved_by']}." if trace["config_approved_by"] else " (не утверждены экспертом)."),
    ]
    return {
        "calculation_id": calc.id, "period": calc.period, "created_at": calc.created_at,
        "key_factors": factors,
        "steps": trace["steps"],
        "operations": [
            {"id": op["ref"], "region": op["region"], "amount_tiyn": op["amount_tiyn"],
             "date": txs[int(op["ref"])].op_date if int(op["ref"]) in txs else None,
             "counterparty": txs[int(op["ref"])].counterparty if int(op["ref"]) in txs else None,
             "purpose": txs[int(op["ref"])].purpose if int(op["ref"]) in txs else None}
            for op in trace.get("operations", [])
        ],
        "warnings": trace.get("warnings", []),
        "config": {"year": trace["config_year"], "version": trace["config_version"],
                   "sha256": trace["config_sha256"], "approved_by": trace["config_approved_by"]},
        "rights": "Если вы не согласны с расчётом, нажмите «Возразить» — эксперт рассмотрит возражение "
                  "в течение 3 рабочих дней.",
    }
