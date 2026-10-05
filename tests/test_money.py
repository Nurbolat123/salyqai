from decimal import Decimal

import pytest

from salyq.money import MoneyError, format_tiyn, mul_rate, round_to_tenge, to_tiyn


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1 250 000,50", 125000050), ("1 000 ₸", 100000), (Decimal("0.01"), 1), (7, 700), ("-15.5", -1550)],
)
def test_to_tiyn(value, expected):
    assert to_tiyn(value) == expected


@pytest.mark.parametrize("bad", [1.5, "abc", "1.005", Decimal("NaN")])
def test_to_tiyn_rejects(bad):
    with pytest.raises(MoneyError):
        to_tiyn(bad)


def test_rounding_half_up():
    assert mul_rate(150, Decimal("0.03")) == 5  # 4.5 → 5
    assert round_to_tenge(150) == 200
    assert round_to_tenge(149) == 100
    assert mul_rate(10**15, Decimal("0.04")) == 4 * 10**13  # без потери точности


def test_format():
    assert format_tiyn(123456789) == "1 234 567,89 ₸"
    assert format_tiyn(-5) == "-0,05 ₸"
