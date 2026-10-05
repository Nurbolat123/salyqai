import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from salyq.money import to_tiyn
from salyq.tax import (
    ConfigNotFound,
    IncomeItem,
    RegionIncome,
    TaxInputError,
    available_years,
    calculate_self_social,
    calculate_simplified,
    load_year,
    period_deadlines,
)
from salyq.tax.config import RATES_DIR

ALMATY, ASTANA = "750000000", "710000000"  # коды КАТО
LIMIT_2026 = 600_000 * 432_500  # 2 595 000 000 ₸ в тиынах


def codes(result):
    return [w.code for w in result.warnings]


def step(result, code):
    return next(s for s in result.trace.steps if s.code == code)


def one(income_tiyn, rate="0.04", **kw):
    """Один регион со ставкой маслихата."""
    return [RegionIncome(ALMATY, income_tiyn, rate=Decimal(rate) if rate else None, **kw)]


class TestConfig:
    def test_all_year_files_are_valid(self):
        years = available_years()
        assert {2025, 2026} <= set(years)
        for y in years:
            loaded = load_year(y)
            assert loaded.config.year == y
            assert len(loaded.sha256) == 64

    def test_missing_year(self):
        with pytest.raises(ConfigNotFound):
            load_year(1999)

    def test_rates_are_exact_decimals(self):
        cfg = load_year(2026).config
        assert cfg.simplified.rate == Decimal("0.04")
        assert cfg.simplified.limit_warning_levels == (Decimal("0.5"), Decimal("0.8"), Decimal("0.95"))
        assert isinstance(cfg.social_self.opvr.rate, Decimal)

    def test_2026_limit_matches_spec(self):
        cfg = load_year(2026).config
        lim = cfg.simplified.income_limit
        assert (lim.period, lim.mrp * cfg.mrp_tiyn) == ("half_year", to_tiyn("2595000000"))

    def _copy(self, tmp_path: Path, year: int, replace: tuple[str, str], name: str | None = None) -> Path:
        text = (RATES_DIR / f"{year}.yaml").read_text(encoding="utf-8").replace(*replace)
        (tmp_path / f"{name or year}.yaml").write_text(text, encoding="utf-8")
        return tmp_path

    def test_shares_must_sum_to_one(self, tmp_path):
        d = self._copy(tmp_path, 2025, ('share: "0.5", reduced', 'share: "0.6", reduced'))
        with pytest.raises(ValueError, match="сумма долей"):
            load_year.__wrapped__(2025, d)

    def test_warning_levels_validated(self, tmp_path):
        d = self._copy(tmp_path, 2026, ('["0.5", "0.8", "0.95"]', '["0.8", "0.5"]'))
        with pytest.raises(ValueError, match="limit_warning_levels"):
            load_year.__wrapped__(2026, d)

    def test_year_must_match_filename(self, tmp_path):
        d = self._copy(tmp_path, 2025, ("", ""), name="2024")
        with pytest.raises(ValueError, match="не совпадает"):
            load_year.__wrapped__(2024, d)

    def test_unquoted_float_is_read_exactly(self, tmp_path):
        d = self._copy(tmp_path, 2026, ('rate: "0.04"', "rate: 0.04"))
        assert load_year.__wrapped__(2026, d).config.simplified.rate == Decimal("0.04")

    def test_approved_config_has_no_warning(self, tmp_path):
        d = self._copy(tmp_path, 2026, ("approved_by: null", 'approved_by: "Эксперт А."'))
        r = calculate_simplified(year=2026, half=1, regions=one(1), config=load_year.__wrapped__(2026, d))
        assert "CONFIG_NOT_APPROVED" not in codes(r)
        assert r.trace.config_approved_by == "Эксперт А."


class TestSimplified2026:
    def test_basic(self):
        r = calculate_simplified(year=2026, half=1, regions=one(to_tiyn("10000000")))
        assert r.components_tiyn == {"ipn": to_tiyn("400000")}
        assert r.total_payable_tiyn == to_tiyn("400000")
        assert codes(r) == ["CONFIG_NOT_APPROVED"]

    def test_rounding_to_tenge(self):
        r = calculate_simplified(year=2026, half=1, regions=one(to_tiyn("1234567.89")))
        # 1 234 567,89 × 4% = 49 382,7156 → 49 382,72 (тиын) → 49 383 (тенге)
        assert r.tax_computed_tiyn == 4938272
        assert r.total_payable_tiyn == to_tiyn("49383")

    def test_zero_income(self):
        assert calculate_simplified(year=2026, half=2, regions=one(0)).total_payable_tiyn == 0

    def test_region_rate(self):
        r = calculate_simplified(year=2026, half=1, regions=one(to_tiyn("1000000"), "0.02", rate_source="https://example.kz/r"))
        assert r.total_payable_tiyn == to_tiyn("20000")
        assert step(r, f"region[{ALMATY}].rate").inputs["source"] == "https://example.kz/r"

    def test_missing_region_rate_falls_back_to_base(self):
        r = calculate_simplified(year=2026, half=1, regions=one(to_tiyn("1000000"), rate=None))
        assert r.total_payable_tiyn == to_tiyn("40000")
        assert "REGION_RATE_MISSING" in codes(r)

    @pytest.mark.parametrize("rate", ["0.019", "0.061"])
    def test_region_rate_out_of_range(self, rate):
        with pytest.raises(TaxInputError, match="вне диапазона"):
            calculate_simplified(year=2026, half=1, regions=one(1, rate))

    def test_several_regions(self):
        r = calculate_simplified(year=2026, half=1, regions=[
            RegionIncome(ALMATY, to_tiyn("1000000"), rate=Decimal("0.03")),
            RegionIncome(ASTANA, to_tiyn("500000"), rate=Decimal("0.05")),
        ])
        assert [(x.region_code, x.tax_tiyn) for x in r.regions] == [(ALMATY, to_tiyn("30000")), (ASTANA, to_tiyn("25000"))]
        assert r.income_tiyn == to_tiyn("1500000")
        assert r.total_payable_tiyn == to_tiyn("55000")

    def test_duplicate_regions_rejected(self):
        with pytest.raises(TaxInputError, match="повторяться"):
            calculate_simplified(year=2026, half=1, regions=one(1) + one(2))

    @pytest.mark.parametrize(
        ("share", "expected"),
        [("0.49", []), ("0.5", ["INCOME_LIMIT_50"]), ("0.81", ["INCOME_LIMIT_80"]),
         ("0.95", ["INCOME_LIMIT_95"]), ("1", ["INCOME_LIMIT_95"])],
    )
    def test_limit_warning_levels(self, share, expected):
        income = int(LIMIT_2026 * Decimal(share))
        r = calculate_simplified(year=2026, half=1, regions=one(income))
        assert [c for c in codes(r) if c.startswith("INCOME_LIMIT")] == expected
        assert r.limit_used == Decimal(share).quantize(Decimal("0.0001"))

    def test_limit_exceeded(self):
        r = calculate_simplified(year=2026, half=1, regions=one(LIMIT_2026 + 1))
        assert "INCOME_LIMIT_EXCEEDED" in codes(r)
        assert not [c for c in codes(r) if c.startswith("INCOME_LIMIT_") and c[-1].isdigit()]

    def test_half_year_limit_ignores_previous_half(self):
        r = calculate_simplified(year=2026, half=2, regions=one(LIMIT_2026 // 4), ytd_income_before_tiyn=LIMIT_2026)
        assert not [c for c in codes(r) if c.startswith("INCOME_LIMIT")]

    def test_vat_threshold_is_annual(self):
        threshold = 10_000 * 432_500
        r = calculate_simplified(year=2026, half=2, regions=one(threshold // 2), ytd_income_before_tiyn=threshold // 2)
        assert "VAT_THRESHOLD_EXCEEDED" not in codes(r)
        r = calculate_simplified(year=2026, half=2, regions=one(threshold // 2 + 1), ytd_income_before_tiyn=threshold // 2)
        assert "VAT_THRESHOLD_EXCEEDED" in codes(r)

    def test_deadlines(self):
        assert period_deadlines(2026, 1) == {"declaration_910": date(2026, 8, 15), "tax_payment": date(2026, 8, 25)}
        r = calculate_simplified(year=2026, half=2, regions=one(1))
        assert r.deadlines == {"declaration_910": date(2027, 2, 15), "tax_payment": date(2027, 2, 25)}


class TestSimplified2025:
    def test_split_ipn_sn_and_so_reduction(self):
        r = calculate_simplified(year=2025, half=1, regions=one(to_tiyn("10000000"), "0.03"), so_accrued_tiyn=to_tiyn("50000"))
        assert r.components_tiyn == {"ipn": to_tiyn("150000"), "sn": to_tiyn("100000")}
        assert r.total_payable_tiyn == to_tiyn("250000")

    def test_sn_not_negative(self):
        r = calculate_simplified(year=2025, half=1, regions=one(to_tiyn("100000"), "0.03"), so_accrued_tiyn=to_tiyn("50000"))
        assert r.components_tiyn == {"ipn": to_tiyn("1500"), "sn": 0}

    def test_components_summed_over_regions(self):
        r = calculate_simplified(year=2025, half=1, regions=[
            RegionIncome(ALMATY, to_tiyn("1000000"), rate=Decimal("0.02")),
            RegionIncome(ASTANA, to_tiyn("1000000"), rate=Decimal("0.04")),
        ])
        assert r.components_tiyn == {"ipn": to_tiyn("30000"), "sn": to_tiyn("30000")}


class TestInputValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"regions": one(-1)},
            {"regions": one(1.5)},
            {"regions": one(True)},
            {"regions": []},
            {"regions": one(1), "so_accrued_tiyn": -1},
            {"regions": one(1), "half": 3},
            {"regions": one(1), "half": 1, "ytd_income_before_tiyn": 5},
            {"regions": [RegionIncome(ALMATY, 10, rate=Decimal("0.04"), items=(IncomeItem("t1", 9),))]},
        ],
    )
    def test_rejects(self, kwargs):
        with pytest.raises(TaxInputError):
            calculate_simplified(**({"year": 2026, "half": 2} | kwargs))

    def test_config_year_mismatch(self):
        with pytest.raises(TaxInputError):
            calculate_simplified(year=2026, half=1, regions=one(1), config=load_year(2025))


class TestTrace:
    def test_trace_identifies_config_and_is_json(self):
        r = calculate_simplified(year=2026, half=1, regions=one(to_tiyn("500000")))
        t = r.trace
        assert (t.config_year, t.config_version, t.config_approved_by) == (2026, "2026.1", None)
        assert t.config_sha256 == load_year(2026).sha256
        dumped = json.loads(json.dumps(t.to_dict(), ensure_ascii=False))
        assert dumped["steps"][0]["code"] == f"region[{ALMATY}].income"

    def test_trace_lists_operations(self):
        items = [IncomeItem("tx-1", to_tiyn("300000")), IncomeItem("tx-2", to_tiyn("200000"))]
        region = RegionIncome.from_items(ALMATY, items, rate=Decimal("0.04"))
        r = calculate_simplified(year=2026, half=1, regions=[region])
        assert r.income_tiyn == to_tiyn("500000")
        assert r.trace.operations == [
            {"region": ALMATY, "ref": "tx-1", "amount_tiyn": to_tiyn("300000")},
            {"region": ALMATY, "ref": "tx-2", "amount_tiyn": to_tiyn("200000")},
        ]

    def test_trace_steps_reproduce_result(self):
        r = calculate_simplified(year=2025, half=1, regions=one(to_tiyn("3000000"), "0.03"), so_accrued_tiyn=to_tiyn("10000"))
        sn = step(r, "sn_after_so")
        assert sn.inputs == {"computed": to_tiyn("45000"), "so": to_tiyn("10000")}
        assert sn.result == to_tiyn("35000")
        assert step(r, "total_payable").result == r.total_payable_tiyn
        assert step(r, "total_payable").result_display == "80 000,00 ₸"


class TestSelfSocial:
    def test_one_mzp(self):
        r = calculate_self_social(year=2026, declared_income_tiyn=to_tiyn("85000"))
        assert r.payments_tiyn == {
            "opv": to_tiyn("8500"), "opvr": to_tiyn("2975"), "so": to_tiyn("4250"), "vosms": to_tiyn("5950"),
        }
        assert r.total_tiyn == to_tiyn("21675")

    def test_bases_are_clamped(self):
        low = calculate_self_social(year=2026, declared_income_tiyn=0)
        assert low.payments_tiyn["opv"] == to_tiyn("8500")
        high = calculate_self_social(year=2026, declared_income_tiyn=to_tiyn("10000000"))
        assert high.payments_tiyn["so"] == to_tiyn("29750")  # 7 МЗП × 5%
        assert high.payments_tiyn["opv"] == to_tiyn("425000")  # 50 МЗП × 10%
        assert high.payments_tiyn["vosms"] == to_tiyn("5950")  # не зависит от дохода
