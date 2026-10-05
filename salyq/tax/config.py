"""Версионируемая налоговая конфигурация по годам.

Каждый год — отдельный YAML в salyq/tax/rates/<год>.yaml. Значения в коде не
хардкодятся: движок получает их только отсюда. В «след» расчёта попадают год,
версия и sha256 файла, чтобы любой расчёт можно было воспроизвести.
"""

import hashlib
from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, model_validator

RATES_DIR = Path(__file__).parent / "rates"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaxComponent(_Frozen):
    """Доля общей ставки, уходящая в конкретный налог (ИПН, СН)."""

    code: Literal["ipn", "sn"]
    share: Decimal
    reduced_by_so: bool = False  # СН уменьшается на начисленные социальные отчисления


class IncomeLimit(_Frozen):
    mrp: int
    period: Literal["half_year", "year"]


class SimplifiedRegime(_Frozen):
    rate: Decimal  # базовая ставка; применяется, если ставка маслихата региона не найдена
    rate_min: Decimal  # нижняя граница корректировки маслихатом
    rate_max: Decimal
    components: tuple[TaxComponent, ...]
    income_limit: IncomeLimit
    # Доли лимита, при достижении которых предупреждаем пользователя (по возрастанию)
    limit_warning_levels: tuple[Decimal, ...] = ()
    max_employees: int | None = None

    @model_validator(mode="after")
    def _shares_sum_to_one(self) -> "SimplifiedRegime":
        total = sum((c.share for c in self.components), Decimal(0))
        if total != Decimal(1):
            raise ValueError(f"сумма долей компонентов должна быть 1, получено {total}")
        if not self.rate_min <= self.rate <= self.rate_max:
            raise ValueError("базовая ставка вне диапазона корректировки")
        levels = self.limit_warning_levels
        if list(levels) != sorted(set(levels)) or any(not 0 < x < 1 for x in levels):
            raise ValueError("limit_warning_levels: уникальные доли в (0; 1) по возрастанию")
        return self


class SocialPayment(_Frozen):
    rate: Decimal
    base_min_mzp: Decimal | None = None
    base_max_mzp: Decimal | None = None
    fixed_base_mzp: Decimal | None = None  # ВОСМС: фиксированная база в МЗП

    @model_validator(mode="after")
    def _base_defined(self) -> "SocialPayment":
        if self.fixed_base_mzp is None and (self.base_min_mzp is None or self.base_max_mzp is None):
            raise ValueError("нужна либо fixed_base_mzp, либо base_min_mzp и base_max_mzp")
        return self


class SelfSocialPayments(_Frozen):
    opv: SocialPayment
    opvr: SocialPayment | None = None
    so: SocialPayment
    vosms: SocialPayment


class Deadline(_Frozen):
    """Срок как смещение от конца периода: day-е число через months_after месяцев."""

    months_after: int
    day: int


class Deadlines(_Frozen):
    declaration_910: Deadline  # сдача ф. 910.00 после полугодия
    tax_payment: Deadline  # уплата налога по ф. 910.00
    social_payments: Deadline  # соцплатежи за себя после месяца


class TaxYearConfig(_Frozen):
    year: int
    version: str
    effective_from: str
    approved_by: str | None = None  # эксперт, утвердивший версию; None — черновик
    mrp_tiyn: int
    mzp_tiyn: int
    simplified: SimplifiedRegime
    social_self: SelfSocialPayments
    vat_registration_threshold_mrp: int
    deadlines: Deadlines
    sources: tuple[str, ...] = ()
    notes: str = ""


class LoadedConfig(_Frozen):
    config: TaxYearConfig
    sha256: str
    path: str


class ConfigNotFound(LookupError):
    pass


def _parse(raw: bytes, path: Path) -> LoadedConfig:
    data = yaml.safe_load(raw)
    # YAML может прочитать 0.04 как float; приводим к строке, чтобы Decimal был точным.
    cfg = TaxYearConfig.model_validate(_floats_to_str(data))
    if cfg.year != int(path.stem):
        raise ValueError(f"{path.name}: year={cfg.year} не совпадает с именем файла")
    return LoadedConfig(config=cfg, sha256=hashlib.sha256(raw).hexdigest(), path=path.name)


def _floats_to_str(obj):
    if isinstance(obj, float):
        return repr(obj)
    if isinstance(obj, dict):
        return {k: _floats_to_str(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_str(v) for v in obj]
    return obj


@lru_cache
def load_year(year: int, rates_dir: Path = RATES_DIR) -> LoadedConfig:
    path = rates_dir / f"{year}.yaml"
    if not path.exists():
        raise ConfigNotFound(f"нет налоговой конфигурации за {year} год")
    return _parse(path.read_bytes(), path)


def available_years(rates_dir: Path = RATES_DIR) -> list[int]:
    return sorted(int(p.stem) for p in rates_dir.glob("*.yaml") if p.stem.isdigit())
