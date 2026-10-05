"""Поиск ставки маслихата для региона и вида деятельности на дату."""

from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from salyq.models import RegionRate
from salyq.tax.engine import RegionIncome


def find_region_rate(
    session: Session, region_code: str, on_date: date, activity_code: str = ""
) -> RegionRate | None:
    """Ставка, действующая на дату. Ставка для конкретного ОКЭД важнее общей по региону."""
    rows = session.scalars(
        select(RegionRate)
        .where(
            RegionRate.region_code == region_code,
            RegionRate.valid_from <= on_date,
            or_(RegionRate.activity_code == activity_code, RegionRate.activity_code == ""),
        )
        .order_by(RegionRate.valid_from.desc())
    ).all()
    specific = [r for r in rows if activity_code and r.activity_code == activity_code]
    return (specific or rows or [None])[0]


def region_income(
    session: Session, region_code: str, income_tiyn: int, on_date: date, activity_code: str = "", **kw
) -> RegionIncome:
    """RegionIncome со ставкой из справочника; без ставки движок применит базовую с предупреждением."""
    found = find_region_rate(session, region_code, on_date, activity_code)
    return RegionIncome(
        region_code, income_tiyn,
        rate=found.rate if found else None,
        rate_source=found.source_url if found else None,
        **kw,
    )
