"""«След» расчёта: упорядоченный журнал шагов, по которому человек (или аудитор)
может повторить вычисление вручную."""

from dataclasses import asdict, dataclass, field
from typing import Any

from salyq.money import format_tiyn


@dataclass(frozen=True)
class TraceStep:
    code: str
    description: str
    formula: str
    inputs: dict[str, Any]
    result: int | str | bool
    result_display: str


@dataclass
class Trace:
    config_year: int
    config_version: str
    config_sha256: str
    config_verified: bool
    steps: list[TraceStep] = field(default_factory=list)

    def money(self, code: str, description: str, formula: str, result: int, **inputs: Any) -> int:
        self.steps.append(
            TraceStep(code, description, formula, _plain(inputs), result, format_tiyn(result))
        )
        return result

    def fact(self, code: str, description: str, formula: str, result: Any, **inputs: Any) -> Any:
        self.steps.append(
            TraceStep(code, description, formula, _plain(inputs), _plain_value(result), str(result))
        )
        return result

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _plain_value(v: Any) -> Any:
    # Decimal → str, чтобы след был JSON-сериализуем без потери точности
    return v if isinstance(v, (int, str, bool, type(None))) else str(v)


def _plain(inputs: dict[str, Any]) -> dict[str, Any]:
    return {k: _plain_value(v) for k, v in inputs.items()}
