import json
from decimal import Decimal
from pathlib import Path

import pytest

from salyq.money import to_tiyn
from salyq.tax import ConfigNotFound, TaxInputError, available_years, calculate_self_social, calculate_simplified, load_year
from salyq.tax.config import RATES_DIR


def codes(result):
    return [w.code for w in result.warnings]


def step(result, code):
    return next(s for s in result.trace.steps if s.code == code)


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
        assert isinstance(cfg.social_self.opvr.rate, Decimal)

    def _copy(self, tmp_path: Path, year: int, replace: tuple[str, str], name: str | None = None) -> Path:
        text = (RATES_DIR / f"{year}.yaml").read_text(encoding="utf-8").replace(*replace)
        (tmp_path / f"{name or year}.yaml").write_text(text, encoding="utf-8")
        return tmp_path

    def test_shares_must_sum_to_one(self, tmp_path):
        d = self._copy(tmp_path, 2025, ('share: "0.5", reduced', 'share: "0.6", reduced'))
        with pytest.raises(ValueError, match="сумма долей"):
            load_year.__wrapped__(2025, d)

    def test_year_must_match_filename(self, tmp_path):
        d = self._copy(tmp_path, 2025, ("", ""), name="2024")
        with pytest.raises(ValueError, match="не совпадает"):
            load_year.__wrapped__(2024, d)

    def test_unquoted_float_is_read_exactly(self, tmp_path):
        d = self._copy(tmp_path, 2026, ('rate: "0.04"', "rate: 0.04"))
        assert load_year.__wrapped__(2026, d).config.simplified.rate == Decimal("0.04")


class TestSimplified2026:
    def test_basic(self):
        r = calculate_simplified(year=2026, half=1, income_tiyn=to_tiyn("10000000"))
        assert r.rate == Decimal("0.04")
        assert r.components_tiyn == {"ipn": to_tiyn("400000")}
        assert r.total_payable_tiyn == to_tiyn("400000")
        assert codes(r) == ["CONFIG_NOT_VERIFIED"]

    def test_rounding_to_tenge(self):
        r = calculate_simplified(year=2026, half=1, income_tiyn=to_tiyn("1234567.89"))
        # 1 234 567,89 × 4% = 49 382,7156 → 49 382,72 (тиын) → 49 383 (тенге)
        assert r.tax_computed_tiyn == 4938272
        assert r.total_payable_tiyn == to_tiyn("49383")

    def test_zero_income(self):
        r = calculate_simplified(year=2026, half=2, income_tiyn=0)
        assert r.total_payable_tiyn == 0

    def test_maslikhat_rate(self):
        r = calculate_simplified(year=2026, half=1, income_tiyn=to_tiyn("1000000"), maslikhat_rate=Decimal("0.02"))
        assert r.total_payable_tiyn == to_tiyn("20000")
        assert "маслихата" in step(r, "rate").description

    @pytest.mark.parametrize("rate", ["0.019", "0.061", "0"])
    def test_maslikhat_rate_out_of_range(self, rate):
        with pytest.raises(TaxInputError, match="вне диапазона"):
            calculate_simplified(year=2026, half=1, income_tiyn=1, maslikhat_rate=Decimal(rate))

    def test_annual_limit_uses_ytd(self):
        limit = 600_000 * 432_500
        ok = calculate_simplified(year=2026, half=2, income_tiyn=limit // 2, ytd_income_before_tiyn=limit // 2)
        assert "INCOME_LIMIT_EXCEEDED" not in codes(ok)
        over = calculate_simplified(year=2026, half=2, income_tiyn=limit // 2 + 1, ytd_income_before_tiyn=limit // 2)
        assert "INCOME_LIMIT_EXCEEDED" in codes(over)

    def test_vat_threshold(self):
        threshold = 10_000 * 432_500
        assert "VAT_THRESHOLD_EXCEEDED" not in codes(calculate_simplified(year=2026, half=1, income_tiyn=threshold))
        assert "VAT_THRESHOLD_EXCEEDED" in codes(calculate_simplified(year=2026, half=1, income_tiyn=threshold + 1))


class TestSimplified2025:
    def test_split_ipn_sn_and_so_reduction(self):
        r = calculate_simplified(
            year=2025, half=1, income_tiyn=to_tiyn("10000000"), so_accrued_tiyn=to_tiyn("50000")
        )
        assert r.components_tiyn == {"ipn": to_tiyn("150000"), "sn": to_tiyn("100000")}
        assert r.total_payable_tiyn == to_tiyn("250000")

    def test_sn_not_negative(self):
        r = calculate_simplified(year=2025, half=1, income_tiyn=to_tiyn("100000"), so_accrued_tiyn=to_tiyn("50000"))
        assert r.components_tiyn["sn"] == 0
        assert r.components_tiyn["ipn"] == to_tiyn("1500")

    def test_half_year_limit_ignores_ytd(self):
        limit = 24_038 * 393_200
        r = calculate_simplified(year=2025, half=2, income_tiyn=limit, ytd_income_before_tiyn=limit)
        assert "INCOME_LIMIT_EXCEEDED" not in codes(r)
        r = calculate_simplified(year=2025, half=1, income_tiyn=limit + 1)
        assert "INCOME_LIMIT_EXCEEDED" in codes(r)


class TestInputValidation:
    @pytest.mark.parametrize(
        "kwargs",
        [
            {"income_tiyn": -1},
            {"income_tiyn": 1.5},
            {"income_tiyn": True},
            {"income_tiyn": 1, "so_accrued_tiyn": -1},
            {"income_tiyn": 1, "half": 3},
            {"income_tiyn": 1, "half": 1, "ytd_income_before_tiyn": 5},
        ],
    )
    def test_rejects(self, kwargs):
        params = {"year": 2026, "half": 2} | kwargs
        with pytest.raises(TaxInputError):
            calculate_simplified(**params)

    def test_config_year_mismatch(self):
        with pytest.raises(TaxInputError):
            calculate_simplified(year=2026, half=1, income_tiyn=1, config=load_year(2025))


class TestTrace:
    def test_trace_identifies_config_and_is_json(self):
        r = calculate_simplified(year=2026, half=1, income_tiyn=to_tiyn("500000"))
        t = r.trace
        assert (t.config_year, t.config_version) == (2026, "2026.1")
        assert t.config_sha256 == load_year(2026).sha256
        assert t.config_verified is False
        dumped = json.loads(json.dumps(t.to_dict(), ensure_ascii=False))
        assert [s["code"] for s in dumped["steps"]][:4] == ["income", "rate", "tax_computed", "ipn_computed"]

    def test_trace_steps_reproduce_result(self):
        r = calculate_simplified(year=2025, half=1, income_tiyn=to_tiyn("3000000"), so_accrued_tiyn=to_tiyn("10000"))
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
