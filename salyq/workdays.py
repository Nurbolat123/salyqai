"""Рабочие дни РК — для срока рассмотрения возражений (3 рабочих дня, ст. 19-1).

Календарь праздников (holidays_kz.yaml) пополняется каждый год. Для года, которого
в нём нет, учитываются только выходные: срок получается раньше, то есть строже.
"""

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

ALMATY = timezone(timedelta(hours=5))  # единый часовой пояс РК с 01.03.2024
HOLIDAYS_FILE = Path(__file__).parent / "holidays_kz.yaml"


@lru_cache
def holidays() -> frozenset[date]:
    data = yaml.safe_load(HOLIDAYS_FILE.read_text(encoding="utf-8")) or {}
    return frozenset(d for days in data.values() for d in days)


def is_workday(d: date) -> bool:
    return d.weekday() < 5 and d not in holidays()


def add_workdays(start: date, n: int) -> date:
    d = start
    while n > 0:
        d += timedelta(days=1)
        if is_workday(d):
            n -= 1
    return d


def workday_deadline(created: datetime, n: int) -> datetime:
    """Конец n-го рабочего дня после даты создания (по времени Астаны)."""
    local_day = created.astimezone(ALMATY).date()
    return datetime.combine(add_workdays(local_day, n), time(23, 59, 59), ALMATY)
