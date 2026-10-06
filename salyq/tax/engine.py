"""Налоговый движок ИП на СНР на основе упрощённой декларации (ф. 910.00).

Все суммы — в тиынах (int). Ставки и лимиты — только из TaxYearConfig.
Каждый промежуточный результат записывается в Trace.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

from salyq.money import mul_rate, round_to_tenge
from salyq.tax.config import Deadline, LoadedConfig, SocialPayment, load_year
from salyq.tax.trace import Trace


class TaxInputError(ValueError):
    pass


@dataclass(frozen=True)
class TaxWarning:
    code: str
    message: str


@dataclass(frozen=True)
class IncomeItem:
    """Операция, вошедшая в облагаемый доход (для «следа» и экрана «Как посчитано»)."""

    ref: str  # id транзакции
    amount_tiyn: int


@dataclass(frozen=True)
class RegionIncome:
    """Доход, полученный в одном регионе, и ставка маслихата этого региона.

    rate=None — ставка региона не найдена в справочнике, применяется базовая из конфигурации.
    """

    region_code: str
    income_tiyn: int
    rate: Decimal | None = None
    rate_source: str | None = None  # source_url из region_rates
    items: tuple[IncomeItem, ...] = ()

    @classmethod
    def from_items(cls, region_code: str, items: Sequence[IncomeItem], **kw) -> "RegionIncome":
        return cls(region_code, sum(i.amount_tiyn for i in items), items=tuple(items), **kw)


@dataclass
class RegionTax:
    region_code: str
    income_tiyn: int
    rate: Decimal
    tax_tiyn: int


@dataclass
class SimplifiedResult:
    year: int
    half: int
    income_tiyn: int
    regions: list[RegionTax]
    tax_computed_tiyn: int
    components_tiyn: dict[str, int]  # к уплате по каждому налогу, округлено до тенге
    total_payable_tiyn: int
    limit_tiyn: int
    limit_used: Decimal  # доля использованного лимита режима
    deadlines: dict[str, date]
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
    return Trace(c.year, c.version, loaded.sha256, c.approved_by)


def _check_amount(name: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TaxInputError(f"{name} должен быть int (тиыны)")
    if value < 0:
        raise TaxInputError(f"{name} не может быть отрицательным")


def _offset_date(period_end: date, d: Deadline) -> date:
    month_index = period_end.year * 12 + period_end.month - 1 + d.months_after
    return date(month_index // 12, month_index % 12 + 1, d.day)


def period_deadlines(year: int, half: int, config: LoadedConfig | None = None) -> dict[str, date]:
    """Сроки сдачи ф. 910.00 и уплаты налога за полугодие."""
    dl = (config or load_year(year)).config.deadlines
    end = date(year, 6, 30) if half == 1 else date(year, 12, 31)
    return {
        "declaration_910": _offset_date(end, dl.declaration_910),
        "tax_payment": _offset_date(end, dl.tax_payment),
    }


def calculate_simplified(
    *,
    year: int,
    half: Literal[1, 2],
    regions: Sequence[RegionIncome],
    ytd_income_before_tiyn: int = 0,
    so_accrued_tiyn: int = 0,
    config: LoadedConfig | None = None,
) -> SimplifiedResult:
    """Налог по упрощённой декларации за полугодие.

    regions                — доход по регионам со ставками маслихатов (раздельный учёт, ТЗ 4.4).
    ytd_income_before_tiyn — доход с начала года до этого полугодия (для годовых порогов).
    so_accrued_tiyn        — соцотчисления за полугодие (уменьшают СН, если так задано в конфиге).
    """
    if half not in (1, 2):
        raise TaxInputError("half должен быть 1 или 2")
    if not regions:
        raise TaxInputError("нужен хотя бы один регион с доходом")
    codes = [r.region_code for r in regions]
    if len(set(codes)) != len(codes):
        raise TaxInputError("регионы не должны повторяться")
    _check_amount("ytd_income_before_tiyn", ytd_income_before_tiyn)
    _check_amount("so_accrued_tiyn", so_accrued_tiyn)
    for r in regions:
        _check_amount(f"income_tiyn[{r.region_code}]", r.income_tiyn)
        if r.items and sum(i.amount_tiyn for i in r.items) != r.income_tiyn:
            raise TaxInputError(f"{r.region_code}: сумма операций не равна доходу региона")
    if half == 1 and ytd_income_before_tiyn:
        raise TaxInputError("для первого полугодия доход до периода должен быть 0")

    loaded = config or load_year(year)
    cfg = loaded.config
    if cfg.year != year:
        raise TaxInputError(f"конфигурация за {cfg.year}, а расчёт за {year}")
    reg = cfg.simplified
    t = _new_trace(loaded)
    warnings: list[TaxWarning] = []

    region_results: list[RegionTax] = []
    component_totals = {c.code: 0 for c in reg.components}
    for r in regions:
        p = f"region[{r.region_code}]"
        t.operations.extend(
            {"region": r.region_code, "ref": i.ref, "amount_tiyn": i.amount_tiyn} for i in r.items
        )
        t.money(f"{p}.income", f"Доход за полугодие, регион {r.region_code}",
                "Σ подтверждённых операций дохода", r.income_tiyn, operations=len(r.items))
        if r.rate is None:
            rate = t.fact(f"{p}.rate", "Ставка (базовая: ставка маслихата не найдена)",
                          "simplified.rate", reg.rate)
            warnings.append(TaxWarning(
                "REGION_RATE_MISSING",
                f"Для региона {r.region_code} нет ставки маслихата в справочнике — применена базовая {reg.rate * 100:g}%.",
            ))
        else:
            if not reg.rate_min <= r.rate <= reg.rate_max:
                raise TaxInputError(
                    f"{r.region_code}: ставка {r.rate} вне диапазона {reg.rate_min}–{reg.rate_max}"
                )
            rate = t.fact(f"{p}.rate", "Ставка маслихата региона", "region_rates.rate ∈ [rate_min; rate_max]",
                          r.rate, rate_min=reg.rate_min, rate_max=reg.rate_max, source=r.rate_source)
        tax = t.money(f"{p}.tax", f"Налог, регион {r.region_code}", "income × rate, до тиына",
                      mul_rate(r.income_tiyn, rate), income=r.income_tiyn, rate=rate)
        region_results.append(RegionTax(r.region_code, r.income_tiyn, rate, tax))
        for comp in reg.components:
            component_totals[comp.code] += t.money(
                f"{p}.{comp.code}", f"{comp.code.upper()}, регион {r.region_code}",
                "income × rate × share", mul_rate(r.income_tiyn, rate * comp.share),
                income=r.income_tiyn, rate=rate, share=comp.share,
            )

    income = t.money("income", "Доход за полугодие, всего", "Σ по регионам",
                     sum(r.income_tiyn for r in regions))
    tax = t.money("tax_computed", "Исчисленный налог, всего", "Σ по регионам",
                  sum(r.tax_tiyn for r in region_results))

    components: dict[str, int] = {}
    for comp in reg.components:
        amount = component_totals[comp.code]
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

    # Лимит дохода для применения режима и ступени предупреждений (ТЗ 4.4)
    limit = reg.income_limit
    limit_tiyn = t.money(
        "income_limit", f"Лимит дохода ({'полугодие' if limit.period == 'half_year' else 'год'})",
        "limit_mrp × МРП", limit.mrp * cfg.mrp_tiyn, limit_mrp=limit.mrp, mrp=cfg.mrp_tiyn,
    )
    checked = income if limit.period == "half_year" else ytd_income_before_tiyn + income
    used = t.fact(
        "income_limit_used", "Использовано лимита", "checked_income / limit",
        (Decimal(checked) / Decimal(limit_tiyn)).quantize(Decimal("0.0001")),
        checked_income=checked, limit=limit_tiyn,
    )
    if checked > limit_tiyn:
        warnings.append(TaxWarning(
            "INCOME_LIMIT_EXCEEDED",
            "Доход превысил лимит упрощённого режима — требуется переход на другой режим.",
        ))
    else:
        reached = [lv for lv in reg.limit_warning_levels if checked >= mul_rate(limit_tiyn, lv)]
        if reached:
            pct = int(reached[-1] * 100)
            warnings.append(TaxWarning(
                f"INCOME_LIMIT_{pct}", f"Доход достиг {pct}% лимита упрощённого режима.",
            ))

    vat_threshold = cfg.vat_registration_threshold_mrp * cfg.mrp_tiyn
    year_income = ytd_income_before_tiyn + income
    if t.fact(
        "vat_threshold_exceeded", "Превышен порог постановки на учёт по НДС",
        "доход с начала года > threshold_mrp × МРП", year_income > vat_threshold,
        year_income=year_income, threshold=vat_threshold,
    ):
        warnings.append(TaxWarning(
            "VAT_THRESHOLD_EXCEEDED",
            "Годовой оборот превысил порог по НДС — необходимо встать на учёт по НДС.",
        ))

    deadlines = period_deadlines(year, half, loaded)
    for code, d in deadlines.items():
        t.fact(f"deadline.{code}", f"Срок: {code}", "конец полугодия + deadlines", d.isoformat())

    if cfg.approved_by is None:
        warnings.append(TaxWarning(
            "CONFIG_NOT_APPROVED",
            f"Параметры {cfg.year} года ({cfg.version}) не утверждены экспертом.",
        ))

    return SimplifiedResult(
        year=year, half=half, income_tiyn=income, regions=region_results, tax_computed_tiyn=tax,
        components_tiyn=components, total_payable_tiyn=total, limit_tiyn=limit_tiyn,
        limit_used=used, deadlines=deadlines, warnings=warnings, trace=t,
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
