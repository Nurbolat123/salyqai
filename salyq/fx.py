"""Курсы НБ РК для пересчёта валютных поступлений (ТЗ 4.3: «курс на дату поступления,
курс хранится с операцией»).

Курсы складываются в таблицу fx_rates фоновой задачей (fetch_and_store) из
официальной ленты Нацбанка; при разметке используется только таблица.
"""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from defusedxml import ElementTree as ET
from defusedxml.common import DefusedXmlException
from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.http import get_text
from salyq.models import FxRate

NBK_URL = "https://nationalbank.kz/rss/get_rates.cfm?fdate={d:%d.%m.%Y}"


class FxError(RuntimeError):
    pass


def parse_nbk_rates(xml_text: str) -> dict[str, Decimal]:
    """XML ленты НБ РК → {валюта: тенге за 1 единицу}. В ленте курс может быть за quant единиц."""
    try:
        root = ET.fromstring(xml_text)
    except (ET.ParseError, DefusedXmlException) as exc:
        raise FxError(f"некорректный XML курсов: {exc}") from exc
    rates: dict[str, Decimal] = {}
    for item in root.iter("item"):
        code = (item.findtext("title") or "").strip().upper()
        value = (item.findtext("description") or "").strip().replace(",", ".")
        quant = (item.findtext("quant") or "1").strip()
        if len(code) == 3 and value:
            rates[code] = (Decimal(value) / Decimal(quant)).quantize(Decimal("0.000001"))
    if not rates:
        raise FxError("в ответе НБ РК нет курсов")
    return rates


def store_rates(session: Session, rate_date: date, rates: dict[str, Decimal], source: str = "nationalbank.kz") -> int:
    existing = set(session.scalars(select(FxRate.currency).where(FxRate.rate_date == rate_date)))
    new = [FxRate(currency=c, rate_date=rate_date, rate=r, source=source) for c, r in rates.items() if c not in existing]
    session.add_all(new)
    session.commit()
    return len(new)


def fetch_and_store(session: Session, rate_date: date, timeout: float = 15.0) -> int:  # pragma: no cover — сеть
    return store_rates(session, rate_date, parse_nbk_rates(get_text(NBK_URL.format(d=rate_date), timeout=timeout)))


def get_rate(session: Session, currency: str, on_date: date) -> Decimal | None:
    if currency == "KZT":
        return Decimal(1)
    return session.scalar(select(FxRate.rate).where(FxRate.currency == currency, FxRate.rate_date == on_date))


def to_kzt_tiyn(amount_minor: int, rate: Decimal) -> int:
    """Сумма в минимальных единицах валюты (центах) × курс → тиыны."""
    return int((Decimal(amount_minor) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))
