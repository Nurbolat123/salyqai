"""Шлюз обезличивания: всё, что уходит во внешнюю LLM, проходит через Anonymizer.

Удаляются: ИИН, БИН (и любые 12-значные номера), IBAN, номера карт, телефоны,
e-mail, ФИО. Значения заменяются на стабильные токены вида [PERSON_1]; таблица
соответствия хранится только на нашей стороне (vault) и позволяет вернуть
исходные значения в ответ модели (deanonymize).

Политика — «лучше лишнее замаскировать»: 12-значные номера маскируются даже
при неверной контрольной сумме, карты — без проверки Луна.
"""

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from salyq.privacy.validators import kz_id_kind

UP = "А-ЯЁӘҒҚҢӨҰҮҺІ"
LO = "а-яёәғқңөұүһі"

_PATRONYMIC_SUFFIXES = ("ович", "евич", "ьич", "овна", "евна", "ична", "инична")
_KZ_PATRONYMIC_WORDS = ("улы", "ұлы", "уулу", "кызы", "қызы")
_CONTEXT_WORDS = (
    "ИП", "И.П.", "Индивидуальный предприниматель", "ФИО", "Ф.И.О.",
    "получатель", "отправитель", "плательщик", "бенефициар", "клиент", "владелец",
)


def _alt(words: Iterable[str]) -> str:
    return "|".join(re.escape(w) for w in words)


def _build_person_patterns() -> list[re.Pattern[str]]:
    patterns = []
    for upper in (False, True):
        if upper:
            word = rf"[{UP}]{{2,}}(?:-[{UP}]{{2,}})?"
            sfx = _alt(s.upper() for s in _PATRONYMIC_SUFFIXES)
            kz = _alt(s.upper() for s in _KZ_PATRONYMIC_WORDS)
            patr = rf"[{UP}]+(?:{sfx}|{kz})"
        else:
            word = rf"[{UP}][{LO}]+(?:-[{UP}][{LO}]+)?"
            sfx = _alt(_PATRONYMIC_SUFFIXES)
            kz = _alt(_KZ_PATRONYMIC_WORDS)
            patr = rf"[{UP}][{LO}]*(?:{sfx}|{kz})"
        b, e = rf"(?<![{UP}{LO}\w])", rf"(?![{UP}{LO}\w])"
        # Фамилия Имя Отчество / Имя Отчество / Фамилия Имя Ерланұлы
        patterns.append(re.compile(rf"{b}(?:{word}\s+){{1,2}}{patr}{e}"))
        # Казахское отчество отдельным словом: «Нурлан Ерлан улы»
        patterns.append(re.compile(rf"{b}(?:{word}\s+){{1,2}}{word}\s+(?:{kz}){e}", re.IGNORECASE if not upper else 0))
        # Фамилия И.О. / Фамилия И.
        patterns.append(re.compile(rf"{b}{word}\s+[{UP}]\.\s?(?:[{UP}]\.)?"))
        # И.О. Фамилия
        patterns.append(re.compile(rf"{b}[{UP}]\.\s?(?:[{UP}]\.\s?)?{word}{e}"))
    return patterns


_PERSON_PATTERNS = _build_person_patterns()
_ANY_WORD = rf"(?:[{UP}][{LO}]+|[{UP}]{{2,}})(?:-(?:[{UP}][{LO}]+|[{UP}]{{2,}}))?"
_CONTEXT_PERSON = re.compile(
    rf"(?:(?<![\w{UP}{LO}])(?:{_alt(_CONTEXT_WORDS)}))[\s:]+"
    rf"(?P<name>{_ANY_WORD}(?:\s+{_ANY_WORD}){{0,2}}(?:\s+[{UP}]\.\s?(?:[{UP}]\.)?)?)",
    re.IGNORECASE,
)

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?(?![A-Za-z0-9])", re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)\d{4}(?:[\s-]?[\d*]{4}){2}[\s-]?\d{4}(?!\d)")  # и маскированные 4400-43**-****-6789
_KZ_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_PHONE = re.compile(
    r"(?<![\d+])(?:\+7|8|7)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"
)

# Порядок важен: при пересечении побеждает более ранний тип и более длинное совпадение.
_PRIORITY = {"EMAIL": 0, "IBAN": 1, "CARD": 2, "IIN": 3, "BIN": 3, "ID": 3, "PHONE": 4, "PERSON": 5}


@dataclass(frozen=True)
class Entity:
    kind: str
    start: int
    end: int
    value: str


@dataclass
class AnonymizedText:
    text: str
    entities: list[tuple[str, str]]  # (kind, token) — без исходных значений


def _normalize(kind: str, value: str) -> str:
    if kind in {"IBAN", "CARD", "PHONE", "IIN", "BIN", "ID"}:
        digits = re.sub(r"[\s()\-]", "", value).upper()
        if kind == "PHONE":
            digits = "7" + re.sub(r"\D", "", digits)[-10:]
        return digits
    return " ".join(value.split()).lower()


def detect(text: str, known_names: Iterable[str] = ()) -> list[Entity]:
    found: list[Entity] = []

    def add(kind: str, m: re.Match[str], group: int | str = 0) -> None:
        found.append(Entity(kind, m.start(group), m.end(group), m.group(group)))

    for m in _EMAIL.finditer(text):
        add("EMAIL", m)
    for m in _IBAN.finditer(text):
        # Требуем хотя бы одну цифру после кода страны и длину ≥ 15, чтобы не ловить слова
        if len(re.sub(r"\s", "", m.group())) >= 15:
            add("IBAN", m)
    for m in _CARD.finditer(text):
        add("CARD", m)
    for m in _KZ_ID.finditer(text):
        add(kz_id_kind(m.group()), m)
    for m in _PHONE.finditer(text):
        add("PHONE", m)
    for name in known_names:
        name = name.strip()
        if len(name) < 3:
            continue
        pat = r"\s+".join(re.escape(p) for p in name.split())
        for m in re.finditer(rf"(?<![\w{UP}{LO}]){pat}(?![\w{UP}{LO}])", text, re.IGNORECASE):
            add("PERSON", m)
    for p in _PERSON_PATTERNS:
        for m in p.finditer(text):
            add("PERSON", m)
    for m in _CONTEXT_PERSON.finditer(text):
        add("PERSON", m, "name")

    # Разрешение пересечений: по приоритету типа, затем по длине.
    found.sort(key=lambda e: (_PRIORITY[e.kind], -(e.end - e.start), e.start))
    chosen: list[Entity] = []
    for e in found:
        if all(e.end <= c.start or e.start >= c.end for c in chosen):
            chosen.append(e)
    return sorted(chosen, key=lambda e: e.start)


@dataclass
class Anonymizer:
    """Обезличиватель с хранилищем соответствий на одну сессию/документ."""

    known_names: list[str] = field(default_factory=list)
    _forward: dict[tuple[str, str], str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def _token(self, kind: str, value: str) -> str:
        label = "PERSON" if kind == "PERSON" else kind
        key = (label, _normalize(kind, value))
        if key not in self._forward:
            self._counters[label] = self._counters.get(label, 0) + 1
            token = f"[{label}_{self._counters[label]}]"
            self._forward[key] = token
            self._reverse[token] = value
        return self._forward[key]

    def anonymize(self, text: str) -> AnonymizedText:
        entities = detect(text, self.known_names)
        out, pos, report = [], 0, []
        for e in entities:
            token = self._token(e.kind, e.value)
            out.append(text[pos:e.start])
            out.append(token)
            report.append((e.kind, token))
            pos = e.end
        out.append(text[pos:])
        return AnonymizedText("".join(out), report)

    def anonymize_obj(self, obj: Any) -> Any:
        """Рекурсивно обезличивает строки в dict/list (например, транзакции)."""
        if isinstance(obj, str):
            return self.anonymize(obj).text
        if isinstance(obj, dict):
            return {k: self.anonymize_obj(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.anonymize_obj(v) for v in obj]
        return obj

    def deanonymize(self, text: str) -> str:
        return re.sub(r"\[[A-Z]+_\d+\]", lambda m: self._reverse.get(m.group(), m.group()), text)

    @property
    def mapping(self) -> dict[str, str]:
        """token → исходное значение. Никогда не отправлять во внешние сервисы."""
        return dict(self._reverse)


class PIILeakError(RuntimeError):
    pass


def assert_clean(text: str, known_names: Iterable[str] = ()) -> None:
    """Финальная проверка перед отправкой наружу."""
    leaks = detect(text, known_names)
    if leaks:
        raise PIILeakError(f"в тексте остались персональные данные: {sorted({e.kind for e in leaks})}")
