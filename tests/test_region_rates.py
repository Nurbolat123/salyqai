from datetime import date
from decimal import Decimal

from salyq.models import RegionRate
from salyq.tax.region_rates import find_region_rate, region_income

ALMATY = "750000000"


def test_rate_lookup(sqlite_session):
    s = sqlite_session
    s.add_all([
        RegionRate(region_code=ALMATY, activity_code="", rate=Decimal("0.03"), valid_from=date(2026, 1, 1), source_url="u1"),
        RegionRate(region_code=ALMATY, activity_code="", rate=Decimal("0.025"), valid_from=date(2026, 7, 1), source_url="u2"),
        RegionRate(region_code=ALMATY, activity_code="62010", rate=Decimal("0.02"), valid_from=date(2026, 1, 1), source_url="u3"),
    ])
    s.commit()
    assert find_region_rate(s, ALMATY, date(2026, 6, 30)).rate == Decimal("0.03")
    assert find_region_rate(s, ALMATY, date(2026, 12, 31)).rate == Decimal("0.025")
    assert find_region_rate(s, ALMATY, date(2026, 12, 31), "62010").rate == Decimal("0.02")  # ОКЭД важнее
    assert find_region_rate(s, ALMATY, date(2026, 12, 31), "47110").rate == Decimal("0.025")
    assert find_region_rate(s, ALMATY, date(2025, 12, 31)) is None
    assert find_region_rate(s, "710000000", date(2026, 12, 31)) is None


def test_region_income(sqlite_session):
    sqlite_session.add(RegionRate(region_code=ALMATY, rate=Decimal("0.03"), valid_from=date(2026, 1, 1), source_url="u1"))
    sqlite_session.commit()
    r = region_income(sqlite_session, ALMATY, 100, date(2026, 6, 30))
    assert (r.rate, r.rate_source, r.income_tiyn) == (Decimal("0.03"), "u1", 100)
    assert region_income(sqlite_session, "1", 100, date(2026, 6, 30)).rate is None
