"""Декларация 910.00: сборка черновика, проверки, подпись ЭЦП, экспорт (ТЗ 4.5).

Персональные данные (ИИН, ФИО) в payload не хранятся — подставляются из профиля
при показе, подписи и экспорте.

ВНИМАНИЕ: структура строк формы и формат файла экспорта — рабочий вариант. Перед
пилотом их нужно сверить с утверждённой формой 910.00 на 2026 год и XSD ИС СОНО
(импорт в Кабинет налогоплательщика).
"""

import hashlib
import json
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit
from salyq.auth.clock import utcnow
from salyq.auth.ecp import EcpVerificationError, EcpVerifier
from salyq.categorize.service import user_transactions
from salyq.models import BankAccount, Declaration910, Statement, Transaction, User
from salyq.settings import Settings
from salyq.tax.summary import Period, parse_period, tax_summary

FORMAT_NOTE = "Рабочий формат Salyq; сверить с формой 910.00 и XSD ИС СОНО перед пилотом"


class DeclarationError(ValueError):
    pass


def _tenge(tiyn: int) -> int:
    """Суммы в форме — в целых тенге (уже округлены движком)."""
    return tiyn // 100


def _months(period: Period) -> list[str]:
    first = 1 if period.half == 1 else 7
    return [f"{period.year}-{m:02d}" for m in range(first, first + 6)]


def _payload(user: User, period: Period, s: dict[str, Any]) -> dict[str, Any]:
    comps = s["tax"]["components_tiyn"]
    return {
        "form": "910.00",
        "format_note": FORMAT_NOTE,
        "period": {"year": period.year, "half": period.half},
        "declaration_type": "очередная",
        "employees_avg": user.employees_count,
        "income": {"total_tiyn": s["income_tiyn"], "by_region": s["regions"]},
        "taxes": {
            "ipn_tiyn": comps.get("ipn", 0),
            "sn_tiyn": comps.get("sn", 0),
            "total_payable_tiyn": s["tax"]["total_payable_tiyn"],
        },
        "social_self": [{"month": m, **s["social_monthly"]["payments_tiyn"]} for m in _months(period)],
        "calculation_id": s["calculation_id"],
        "config": s["config"],
    }


def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode()


def document(user: User, decl: Declaration910) -> dict[str, Any]:
    """Полный документ для показа, подписи и экспорта: payload + реквизиты из профиля."""
    return {"header": {"iin": user.iin, "full_name": user.full_name, "region_code": user.region_code},
            **decl.payload_json}


def document_digest(user: User, decl: Declaration910) -> str:
    """То, что пользователь подписывает ЭЦП: sha256 канонического JSON документа."""
    return hashlib.sha256(_canonical(document(user, decl))).hexdigest()


def _coverage_gaps(session: Session, user: User, period: Period) -> list[str]:
    """Есть ли выписки на весь период по каждому счёту (ТЗ 4.5: «совпадение с выписками»)."""
    gaps = []
    accounts = session.scalars(select(BankAccount).where(BankAccount.user_id == user.id)).all()
    if not accounts:
        return ["нет ни одного загруженного счёта"]
    for acc in accounts:
        ranges = sorted(
            (st.period_from, st.period_to)
            for st in session.scalars(select(Statement).where(Statement.account_id == acc.id))
            if st.period_from and st.period_to
        )
        cursor = period.start
        for start, end in ranges:
            if start > cursor:
                break
            if end >= cursor:
                cursor = date.fromordinal(end.toordinal() + 1)
        if cursor <= period.end:
            gaps.append(f"счёт {acc.iban_masked}: нет выписки начиная с {cursor:%d.%m.%Y}")
    return gaps


def run_checks(session: Session, user: User, period: Period, summary: dict[str, Any], today: date) -> list[dict]:
    def check(code: str, ok: bool, message: str, severity: str = "error") -> dict:
        return {"code": code, "ok": ok, "severity": severity, "message": message}

    unconfirmed = session.scalars(user_transactions(user).where(
        Transaction.op_date >= period.start, Transaction.op_date <= period.end,
        Transaction.confirmed_by_user.is_(False),
    )).all()
    gaps = _coverage_gaps(session, user, period)
    codes = {w["code"] for w in summary["warnings"]}
    return [
        check("period_finished", today > period.end,
              "Полугодие закончилось" if today > period.end else "Полугодие ещё не закончилось — данные могут измениться"),
        check("no_unconfirmed_transactions", not unconfirmed,
              "Все операции подтверждены" if not unconfirmed else f"Не подтверждено операций: {len(unconfirmed)}"),
        check("statements_cover_period", not gaps, "Выписки покрывают весь период" if not gaps else "; ".join(gaps)),
        check("limit_not_exceeded", "INCOME_LIMIT_EXCEEDED" not in codes,
              "Лимит упрощённого режима не превышен" if "INCOME_LIMIT_EXCEEDED" not in codes
              else "Превышен лимит — упрощённая декларация неприменима"),
        check("region_in_profile", user.region_code is not None,
              "Регион указан" if user.region_code else "Укажите регион в профиле"),
        check("config_approved", summary["config"]["approved_by"] is not None,
              "Ставки утверждены экспертом" if summary["config"]["approved_by"] else "Ставки года не утверждены экспертом"),
        check("vat_threshold", "VAT_THRESHOLD_EXCEEDED" not in codes,
              "Порог НДС не превышен" if "VAT_THRESHOLD_EXCEEDED" not in codes
              else "Превышен порог НДС — нужна постановка на учёт по НДС", severity="warning"),
    ]


def build_draft(session: Session, user: User, period_code: str, today: date | None = None) -> Declaration910:
    period = parse_period(period_code)
    today = today or utcnow().date()
    final = session.scalar(select(Declaration910).where(
        Declaration910.user_id == user.id, Declaration910.period == period.code,
        Declaration910.status.in_(("signed", "exported")),
    ))
    if final is not None:
        raise DeclarationError("декларация за этот период уже подписана; дополнительная декларация — этап 2")
    for old in session.scalars(select(Declaration910).where(
        Declaration910.user_id == user.id, Declaration910.period == period.code,
        Declaration910.status.in_(("draft", "checked")),
    )):
        old.status = "superseded"

    summary = tax_summary(session, user, period, today=today)
    payload = _payload(user, period, summary)
    checks = run_checks(session, user, period, summary, today)
    ok = all(c["ok"] for c in checks if c["severity"] == "error")
    decl = Declaration910(
        user_id=user.id, period=period.code, calculation_id=summary["calculation_id"], payload_json=payload,
        payload_sha256=hashlib.sha256(_canonical(payload)).hexdigest(), checks_json=checks,
        status="checked" if ok else "draft", created_at=utcnow(),
    )
    session.add(decl)
    session.flush()
    audit.record(session, "declaration.build", actor_type="user", actor_id=user.id, object_type="declaration_910",
                 object_id=decl.id, period=period.code, status=decl.status,
                 failed=[c["code"] for c in checks if not c["ok"]])
    session.commit()
    return decl


def expert_review(session: Session, expert: User, decl: Declaration910) -> Declaration910:
    if decl.status != "checked":
        raise DeclarationError("проверить можно только декларацию в статусе checked")
    decl.expert_reviewed_by, decl.expert_reviewed_at = expert.id, utcnow()
    audit.record(session, "declaration.expert_review", actor_type="expert", actor_id=expert.id,
                 object_type="declaration_910", object_id=decl.id)
    session.commit()
    return decl


def sign(
    session: Session, user: User, decl: Declaration910, *, signed_data: str, verifier: EcpVerifier, settings: Settings,
) -> Declaration910:
    """Подпись пользователем (ТЗ 4.5: 910.00 подписывает человек, ст. 19-1)."""
    if decl.status != "checked":
        raise DeclarationError("подписать можно только проверенную декларацию (checked)")
    if settings.declaration_expert_review and decl.expert_reviewed_at is None:
        raise DeclarationError("декларация ждёт проверки экспертом")
    # Данные не должны измениться с момента проверки
    fresh = tax_summary(session, user, parse_period(decl.period))
    if fresh["calculation_id"] != decl.calculation_id:
        raise DeclarationError("данные изменились после проверки — соберите черновик заново")
    try:
        subject = verifier.verify(signed_data, document_digest(user, decl))
    except EcpVerificationError as exc:
        raise DeclarationError(f"подпись не прошла проверку: {exc}") from exc
    if subject.iin != user.iin:
        raise DeclarationError("подписано ключом другого лица")
    decl.signature, decl.signed_at, decl.status = signed_data, utcnow(), "signed"
    audit.record(session, "declaration.sign", actor_type="user", actor_id=user.id,
                 object_type="declaration_910", object_id=decl.id, period=decl.period)
    session.commit()
    return decl


def export_xml(session: Session, user: User, decl: Declaration910) -> tuple[str, bytes]:
    """Файл для импорта в Кабинет налогоплательщика (ТЗ 4.5). Только подписанная декларация."""
    if decl.status not in ("signed", "exported"):
        raise DeclarationError("экспортировать можно только подписанную декларацию")
    doc = document(user, decl)
    p = doc["period"]
    root = ET.Element("form910", {"version": "910.00", "note": FORMAT_NOTE})
    ET.SubElement(root, "header", {
        "iin": doc["header"]["iin"], "fio": doc["header"]["full_name"], "year": str(p["year"]),
        "halfYear": str(p["half"]), "type": "regular", "region": doc["header"]["region_code"] or "",
    })
    fields = [
        ("income", "Доход за налоговый период", _tenge(doc["income"]["total_tiyn"])),
        ("employees_avg", "Среднесписочная численность работников", doc["employees_avg"]),
        ("ipn", "Индивидуальный подоходный налог", _tenge(doc["taxes"]["ipn_tiyn"])),
        ("sn", "Социальный налог", _tenge(doc["taxes"]["sn_tiyn"])),
        ("tax_total", "Сумма налогов к уплате", _tenge(doc["taxes"]["total_payable_tiyn"])),
    ]
    for code, name, value in fields:
        ET.SubElement(root, "field", {"code": code, "name": name}).text = str(value)
    social = ET.SubElement(root, "socialSelf")
    for row in doc["social_self"]:
        ET.SubElement(social, "month", {"period": row["month"], **{k: str(_tenge(v)) for k, v in row.items() if k != "month"}})
    ET.SubElement(root, "signature").text = decl.signature
    data = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    if decl.status == "signed":
        decl.status, decl.exported_at = "exported", utcnow()
        audit.record(session, "declaration.export", actor_type="user", actor_id=user.id,
                     object_type="declaration_910", object_id=decl.id)
        session.commit()
    return f"910_{decl.period}.xml", data
