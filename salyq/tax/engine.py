"""Налоговый движок ИП на СНР на основе упрощённой декларации (ф. 910.00).

Все суммы — в тиынах (int). Ставки и лимиты — только из TaxYearConfig.
Каждый промежуточный результат записывается в Trace.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from salyq.money import mul_rate, round_to_tenge
from salyq.tax.config import LoadedConfig, SocialPayment, load_year
from salyq.tax.trace import Trace


class TaxInputError(ValueError):
    pass


@dataclass(frozen=True)
class TaxWarning:
    code: str
    message: str


@dataclass
class SimplifiedResult:
    year: int
    half: int
    income_tiyn: int
    rate: Decimal
    tax_computed_tiyn: int
    components_tiyn: dict[str, int]  # к уплате по каждому налогу, округлено до тенге
    total_payable_tiyn: int
    warnings: list[TaxWarning] = field(default_factory=list)
    trace: Trace | None = None


@dataclass
class SocialResult:
    year: int
    declared_income_tiyn: int
    payments_tiyn: dict[str, int]
    total_tiyn: int
    trace: Trace | None = None


def _new_trace(loaded: LoadedConfig) -> Trace:
    c = loaded.config
    return Trace(c.year, c.version, loaded.sha256, c.verified)


def calculate_simplified(
    *,
    year: int,
    half: Literal[1, 2],
    income_tiyn: int,
    ytd_income_before_tiyn: int = 0,
    maslikhat_rate: Decimal | None = None,
    so_accrued_tiyn: int = 0,
    config: LoadedConfig | None = None,
) -> SimplifiedResult:
    """Налог по упрощённой декларации за полугодие.

    income_tiyn            — доход за полугодие.
    ytd_income_before_tiyn — доход с начала года до этого полугодия (для годовых лимитов).
    maslikhat_rate         — ставка, установленная маслихатом (если отличается от базовой).
    so_accrued_tiyn        — соцотчисления за полугодие (уменьшают СН, если так задано в конфиге).
    """
    if half not in (1, 2):
        raise TaxInputError("half должен быть 1 или 2")
    for name, value in (
        ("income_tiyn", income_tiyn),
        ("ytd_income_before_tiyn", ytd_income_before_tiyn),
        ("so_accrued_tiyn", so_accrued_tiyn),
    ):
        if not isinstance(value, int) or isinstance(value, bool):
            raise TaxInputError(f"{name} должен быть int (тиыны)")
        if value < 0:
            raise TaxInputError(f"{name} не может быть отрицательным")
    if half == 1 and ytd_income_before_tiyn:
        raise TaxInputError("для первого полугодия доход до периода должен быть 0")

    loaded = config or load_year(year)
    cfg = loaded.config
    if cfg.year != year:
        raise TaxInputError(f"конфигурация за {cfg.year}, а расчёт за {year}")
    reg = cfg.simplified
    t = _new_trace(loaded)
    warnings: list[TaxWarning] = []

    t.money("income", "Доход за полугодие", "сумма доходов периода", income_tiyn)

    if maslikhat_rate is None:
        rate = t.fact("rate", "Ставка налога (базовая)", "simplified.rate", reg.rate)
    else:
        if not reg.rate_min <= maslikhat_rate <= reg.rate_max:
            raise TaxInputError(
                f"ставка маслихата {maslikhat_rate} вне диапазона {reg.rate_min}–{reg.rate_max}"
            )
        rate = t.fact(
            "rate",
            "Ставка налога (решение маслихата)",
            "maslikhat_rate ∈ [rate_min; rate_max]",
            maslikhat_rate,
            rate_min=reg.rate_min,
            rate_max=reg.rate_max,
        )

    tax = t.money(
        "tax_computed", "Исчисленный налог", "income × rate, до тиына", mul_rate(income_tiyn, rate),
        income=income_tiyn, rate=rate,
    )

    components: dict[str, int] = {}
    for comp in reg.components:
        comp_rate = rate * comp.share
        amount = t.money(
            f"{comp.code}_computed", f"{comp.code.upper()} исчисленный",
            "income × rate × share", mul_rate(income_tiyn, comp_rate),
            income=income_tiyn, rate=rate, share=comp.share,
        )
        if comp.reduced_by_so:
            amount = t.money(
                f"{comp.code}_after_so", f"{comp.code.upper()} за вычетом соцотчислений",
                "max(0, computed − so)", max(0, amount - so_accrued_tiyn),
                computed=amount, so=so_accrued_tiyn,
            )
        components[comp.code] = t.money(
            f"{comp.code}_payable", f"{comp.code.upper()} к уплате", "округление до тенге",
            round_to_tenge(amount), before=amount,
        )

    total = t.money(
        "total_payable", "Итого к уплате", "Σ компонентов", sum(components.values()),
        **{k: v for k, v in components.items()},
    )

    # Лимит дохода для применения режима
    limit = reg.income_limit
    limit_tiyn = t.money(
        "income_limit", f"Лимит дохода ({'полугодие' if limit.period == 'half_year' else 'год'})",
        "limit_mrp × МРП", limit.mrp * cfg.mrp_tiyn, limit_mrp=limit.mrp, mrp=cfg.mrp_tiyn,
    )
    checked = income_tiyn if limit.period == "half_year" else ytd_income_before_tiyn + income_tiyn
    exceeded = t.fact(
        "income_limit_exceeded", "Превышен лимит дохода", "checked_income > limit",
        checked > limit_tiyn, checked_income=checked, limit=limit_tiyn,
    )
    if exceeded:
        warnings.append(TaxWarning(
            "INCOME_LIMIT_EXCEEDED",
            "Доход превысил лимит упрощённого режима — требуется переход на другой режим.",
        ))

    vat_threshold = cfg.vat_registration_threshold_mrp * cfg.mrp_tiyn
    year_income = ytd_income_before_tiyn + income_tiyn
    if t.fact(
        "vat_threshold_exceeded", "Превышен порог постановки на учёт по НДС",
        "доход с начала года > threshold_mrp × МРП", year_income > vat_threshold,
        year_income=year_income, threshold=vat_threshold,
    ):
        warnings.append(TaxWarning(
            "VAT_THRESHOLD_EXCEEDED",
            "Годовой оборот превысил порог по НДС — необходимо встать на учёт по НДС.",
        ))

    if not cfg.verified:
        warnings.append(TaxWarning(
            "CONFIG_NOT_VERIFIED",
            f"Параметры {cfg.year} года ({cfg.version}) не подтверждены бухгалтером.",
        ))

    return SimplifiedResult(
        year=year, half=half, income_tiyn=income_tiyn, rate=rate, tax_computed_tiyn=tax,
        components_tiyn=components, total_payable_tiyn=total, warnings=warnings, trace=t,
    )


def _social_base(t: Trace, code: str, p: SocialPayment, declared: int, mzp: int) -> int:
    if p.fixed_base_mzp is not None:
        return t.money(
            f"{code}_base", f"База {code.upper()} (фиксированная)", "fixed_base_mzp × МЗП",
            mul_rate(mzp, p.fixed_base_mzp), fixed_base_mzp=p.fixed_base_mzp, mzp=mzp,
        )
    lo, hi = mul_rate(mzp, p.base_min_mzp), mul_rate(mzp, p.base_max_mzp)
    return t.money(
        f"{code}_base", f"База {code.upper()}", "clamp(declared, min_mzp × МЗП, max_mzp × МЗП)",
        min(max(declared, lo), hi), declared=declared, min=lo, max=hi,
    )


def calculate_self_social(
    *, year: int, declared_income_tiyn: int, config: LoadedConfig | None = None
) -> SocialResult:
    """Ежемесячные платежи ИП за себя (ОПВ, ОПВР, СО, ВОСМС) от заявленного дохода."""
    if not isinstance(declared_income_tiyn, int) or declared_income_tiyn < 0:
        raise TaxInputError("declared_income_tiyn должен быть неотрицательным int")
    loaded = config or load_year(year)
    cfg = loaded.config
    t = _new_trace(loaded)
    mzp = cfg.mzp_tiyn
    t.money("declared_income", "Заявленный доход за месяц", "ввод", declared_income_tiyn)

    payments: dict[str, int] = {}
    for code in ("opv", "opvr", "so", "vosms"):
        p: SocialPayment | None = getattr(cfg.social_self, code)
        if p is None:
            continue
        base = _social_base(t, code, p, declared_income_tiyn, mzp)
        payments[code] = t.money(
            code, f"{code.upper()} к уплате", "round_tenge(base × rate)",
            round_to_tenge(mul_rate(base, p.rate)), base=base, rate=p.rate,
        )
    total = t.money("total", "Итого соцплатежи за месяц", "Σ", sum(payments.values()))
    return SocialResult(year, declared_income_tiyn, payments, total, t)
