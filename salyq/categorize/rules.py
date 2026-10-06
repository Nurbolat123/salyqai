"""Детерминированные правила разметки (ТЗ 4.3: «LLM + правила»)."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, field_validator

from salyq.categorize.categories import CATEGORIES, allowed

RULES_FILE = Path(__file__).parent / "rules.yaml"


class Rule(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    category: str
    confidence: float
    direction: Literal["in", "out"] | None = None
    knp: tuple[str, ...] = ()
    any_words: tuple[str, ...] = ()
    none_words: tuple[str, ...] = ()

    @field_validator("category")
    @classmethod
    def _known(cls, v: str) -> str:
        if v not in CATEGORIES:
            raise ValueError(f"неизвестная категория {v}")
        return v

    def matches(self, tx: "TxFacts") -> bool:
        if self.direction and tx.direction != self.direction:
            return False
        if self.knp and tx.knp not in self.knp:
            return False
        if self.any_words and not any(w in tx.text for w in self.any_words):
            return False
        if self.none_words and any(w in tx.text for w in self.none_words):
            return False
        return allowed(self.category, tx.direction)


class RuleSet(BaseModel):
    version: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class TxFacts:
    """То, что видят правила. text — назначение + тип операции + контрагент, в нижнем регистре."""

    direction: str
    knp: str
    text: str
    own_transfer: bool  # контрагент — сам пользователь или его счёт


@dataclass(frozen=True)
class Suggestion:
    category: str | None
    confidence: float
    source: str


@lru_cache
def load_rules(path: Path = RULES_FILE) -> RuleSet:
    return RuleSet.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def apply_rules(tx: TxFacts, ruleset: RuleSet | None = None) -> Suggestion:
    if tx.own_transfer:
        return Suggestion("own_transfer", 0.98, "own_account")
    ruleset = ruleset or load_rules()
    best: Rule | None = None
    for rule in ruleset.rules:
        if rule.matches(tx) and (best is None or rule.confidence > best.confidence):
            best = rule
    if best is None:
        return Suggestion(None, 0.0, "no_rule")
    return Suggestion(best.category, best.confidence, f"rule:{best.code}")
