"""Инструменты чата (ТЗ 4.7): цифры считает движок, модель их только объясняет.

Каждый инструмент возвращает:
- card — точные данные для показа пользователю (рисует приложение, в модель не идёт);
- text — краткое текстовое описание для модели (для внешней модели оно ещё и
  обезличивается, а суммы превращаются в диапазоны).
"""

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth.clock import utcnow
from salyq.categorize.categories import CATEGORIES
from salyq.categorize.service import status_of, user_transactions
from salyq.models import RegionRate, Transaction, User
from salyq.money import format_tiyn
from salyq.tax.config_store import active_config
from salyq.tax.summary import PeriodError, current_period, parse_period, tax_summary

SPECS = [
    {"type": "function", "function": {
        "name": "calculate_tax",
        "description": "Посчитать налог, соцплатежи и лимит ИП за полугодие по подтверждённым операциям",
        "parameters": {"type": "object", "properties": {
            "period": {"type": "string", "description": "Полугодие в формате 2026H1 или 2026H2; пусто — текущее"}}},
    }},
    {"type": "function", "function": {
        "name": "show_transactions",
        "description": "Показать операции ИП: сколько ждут проверки, последние поступления",
        "parameters": {"type": "object", "properties": {
            "status": {"type": "string", "enum": ["review", "suggested", "confirmed"]}}},
    }},
    {"type": "function", "function": {
        "name": "compare_rates",
        "description": "Сравнить ставку налога региона ИП с базовой ставкой и диапазоном маслихата",
        "parameters": {"type": "object", "properties": {}},
    }},
]


@dataclass
class ToolResult:
    name: str
    card: dict[str, Any]
    text: str


def calculate_tax(session: Session, user: User, period: str | None = None) -> ToolResult:
    try:
        p = parse_period(period) if period else current_period(utcnow().date())
    except PeriodError as exc:
        return ToolResult("calculate_tax", {"error": str(exc)}, f"Ошибка: {exc}")
    s = tax_summary(session, user, p)
    text = (
        f"Период {s['period']}. Доход (подтверждённый): {format_tiyn(s['income_tiyn'])}. "
        f"Налог к уплате: {format_tiyn(s['tax']['total_payable_tiyn'])}. "
        f"Соцплатежи за себя в месяц: {format_tiyn(s['social_monthly']['total_tiyn'])}. "
        f"Использовано лимита: {float(s['limit']['used']) * 100:.1f}%. "
        f"Не размечено поступлений: {s['pending']['count']}. "
        f"Срок сдачи 910.00: {s['deadlines']['declaration_910']}, уплаты: {s['deadlines']['tax_payment']}. "
        f"Предупреждения: {', '.join(w['message'] for w in s['warnings']) or 'нет'}."
    )
    return ToolResult("calculate_tax", s, text)


def show_transactions(session: Session, user: User, status: str | None = None) -> ToolResult:
    txs = session.scalars(user_transactions(user).order_by(Transaction.op_date.desc())).all()
    counts = {k: 0 for k in ("review", "suggested", "confirmed")}
    for t in txs:
        counts[status_of(t)] += 1
    chosen = [t for t in txs if status is None or status_of(t) == status][:10]
    items = [{"id": t.id, "date": t.op_date.isoformat(), "direction": t.direction, "amount_tiyn": t.amount_kzt_tiyn,
              "counterparty": t.counterparty, "category": t.category, "status": status_of(t)} for t in chosen]
    text = (f"Операций на проверке: {counts['review']}, с предложенной категорией: {counts['suggested']}, "
            f"подтверждено: {counts['confirmed']}. Последние: " + "; ".join(
                f"{i['date']} {'+' if i['direction'] == 'in' else '−'}{format_tiyn(i['amount_tiyn'] or 0)} "
                f"{i['counterparty']} [{CATEGORIES.get(i['category'] or '', 'без категории')}]" for i in items))
    return ToolResult("show_transactions", {"counts": counts, "items": items}, text)


def compare_rates(session: Session, user: User) -> ToolResult:
    year = utcnow().year
    reg = active_config(session, year).config.simplified
    rows = session.scalars(select(RegionRate).where(RegionRate.region_code == (user.region_code or ""))
                           .order_by(RegionRate.valid_from.desc())).all()
    region_rate = rows[0].rate if rows else None
    card = {"year": year, "base_rate": str(reg.rate), "min": str(reg.rate_min), "max": str(reg.rate_max),
            "region_code": user.region_code, "region_rate": None if region_rate is None else str(region_rate),
            "source_url": rows[0].source_url if rows else None}
    text = (f"Базовая ставка {year}: {reg.rate * 100}%, маслихат может установить от {reg.rate_min * 100}% "
            f"до {reg.rate_max * 100}%. " + (f"Ставка региона ИП: {region_rate * 100}%." if region_rate is not None
                                             else "Ставка региона ИП в справочнике не найдена — применяется базовая."))
    return ToolResult("compare_rates", card, text)


REGISTRY = {"calculate_tax": calculate_tax, "show_transactions": show_transactions, "compare_rates": compare_rates}


def run_tool(session: Session, user: User, name: str, args: dict[str, Any]) -> ToolResult:
    fn = REGISTRY.get(name)
    if fn is None:
        return ToolResult(name, {"error": "unknown tool"}, "Такого инструмента нет")
    allowed = {"calculate_tax": {"period"}, "show_transactions": {"status"}, "compare_rates": set()}[name]
    return fn(session, user, **{k: v for k, v in args.items() if k in allowed and isinstance(v, str)})
