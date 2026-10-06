"""Шлюз обезличивания: всё, что уходит во внешнюю LLM, проходит через Anonymizer.

Удаляются (ТЗ, раздел 6): ИИН, БИН (и любые 12-значные номера), IBAN, номера
карт, телефоны, e-mail, ФИО, адреса. Значения заменяются на стабильные токены
вида [PERSON_1]; таблица соответствия хранится только на нашей стороне и
позволяет вернуть исходные значения в ответ модели (deanonymize). Суммы для
внешней модели заменяются диапазонами без возможности восстановления.

Вход для внешней LLM — только prepare_external(): проверяет согласие на
трансграничную передачу и блокирует запрос, если после обезличивания остались ПДн.

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
# Без учёта регистра — только слова контекста; имя обязано начинаться с заглавной,
# иначе «ИП на упрощённой декларации» принималось за ФИО.
_CONTEXT_PERSON = re.compile(
    rf"(?:(?<![\w{UP}{LO}])(?i:{_alt(_CONTEXT_WORDS)}))[\s:]+"
    rf"(?P<name>{_ANY_WORD}(?:\s+{_ANY_WORD}){{0,2}}(?:\s+[{UP}]\.\s?(?:[{UP}]\.)?)?)"
)

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_IBAN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?:\s?[A-Z0-9]{4}){2,7}(?:\s?[A-Z0-9]{1,4})?(?![A-Za-z0-9])", re.IGNORECASE)
_CARD = re.compile(r"(?<!\d)\d{4}(?:[\s-]?[\d*]{4}){2}[\s-]?\d{4}(?!\d)")  # и маскированные 4400-43**-****-6789
_KZ_ID = re.compile(r"(?<!\d)\d{12}(?!\d)")
_PHONE = re.compile(
    r"(?<![\d+])(?:\+7|8|7)[\s-]?\(?\d{3}\)?[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}(?!\d)"
)

_ADDR_MARK = (
    r"(?:ул\.|улица|пр\.|пр-т|просп\.|проспект|мкр\.?|микрорайон|б-р|бульвар|пер\.|переулок|"
    r"шоссе|наб\.|набережная|трасса|көшесі|көш\.|даңғылы|шағынаудан|ш\.а\.)"
)
_ADDR_WORD = rf"[«\"]?[{UP}0-9][\w{LO}{UP}\-/]*[»\"]?"
_ADDRESS = re.compile(
    rf"(?<![\w{UP}{LO}]){_ADDR_MARK}\s*{_ADDR_WORD}(?:\s+{_ADDR_WORD}){{0,3}}"
    rf"(?:,?\s*(?:д\.|дом|үй)?\s*\d+[{LO}a-z]?(?:/\d+)?)?"
    rf"(?:,?\s*(?:кв\.?|квартира|оф\.?|офис|пәтер)\s*\d+)?",
    re.IGNORECASE,
)
# Казахский порядок: «Абай даңғылы, 10», «Сәтбаев көшесі 5»
_ADDRESS_KZ = re.compile(
    rf"(?<![\w{UP}{LO}]){_ADDR_WORD}\s+(?:көшесі|даңғылы|шағынауданы|көш\.)"
    rf"(?:,?\s*(?:үй)?\s*\d+[{LO}a-z]?(?:/\d+)?)?(?:,?\s*(?:пәтер|кв\.?)\s*\d+)?",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    r"(?<![\d.,])(?:(?:KZT|₸)\s*)?(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?"
    r"(?:\s*(?P<mult>тыс\.?|млн\.?|млрд\.?))?\s*(?:₸|тенге|теңге|тг\.?|KZT)",
    re.IGNORECASE,
)
_AMOUNT_PREFIX = re.compile(r"(?:KZT|₸)\s*(\d{1,3}(?:[ \u00a0\u202f]\d{3})+|\d+)(?:[.,]\d{1,2})?(?![\d])")
_MULT = {"тыс": 10**3, "млн": 10**6, "млрд": 10**9}
# Границы диапазонов сумм в тенге для внешней модели
_AMOUNT_BUCKETS = (
    10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000, 50_000_000,
    100_000_000, 500_000_000, 1_000_000_000,
)

# Порядок важен: при пересечении побеждает более ранний тип и более длинное совпадение.
_PRIORITY = {
    "EMAIL": 0, "IBAN": 1, "CARD": 2, "IIN": 3, "BIN": 3, "ID": 3, "PHONE": 4,
    "PERSON": 5, "ADDRESS": 6, "AMOUNT": 7,
}


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


def _human_tenge(n: int) -> str:
    for size, unit in ((10**9, "млрд"), (10**6, "млн"), (10**3, "тыс.")):
        if n >= size:
            return f"{n // size} {unit}"
    return str(n)


def amount_bucket(tenge: int) -> str:
    """Сумма → диапазон, например «[СУММА В ТЕНГЕ: 100 тыс.–500 тыс.]».

    Знак валюты в метку не входит, чтобы сама метка не распознавалась как сумма.
    """
    lower = 0
    for upper in _AMOUNT_BUCKETS:
        if tenge < upper:
            span = f"{_human_tenge(lower)}–{_human_tenge(upper)}" if lower else f"до {_human_tenge(upper)}"
            return f"[СУММА В ТЕНГЕ: {span}]"
        lower = upper
    return f"[СУММА В ТЕНГЕ: от {_human_tenge(lower)}]"


def _amount_tenge(m: re.Match[str]) -> int:
    value = int(re.sub(r"\D", "", m.group(1)))
    mult = (m.groupdict().get("mult") or "").rstrip(".").lower()
    return value * _MULT.get(mult, 1)


def detect(text: str, known_names: Iterable[str] = (), *, include_amounts: bool = False) -> list[Entity]:
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
    for pattern in (_ADDRESS, _ADDRESS_KZ):
        for m in pattern.finditer(text):
            add("ADDRESS", m)
    if include_amounts:
        for pattern in (_AMOUNT, _AMOUNT_PREFIX):
            for m in pattern.finditer(text):
                add("AMOUNT", m)

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

    def anonymize(self, text: str, *, bucket_amounts: bool = False) -> AnonymizedText:
        """bucket_amounts=True — заменить суммы диапазонами (для внешней LLM)."""
        entities = detect(text, self.known_names, include_amounts=bucket_amounts)
        out, pos, report = [], 0, []
        for e in entities:
            if e.kind == "AMOUNT":
                m = _AMOUNT.fullmatch(e.value) or _AMOUNT_PREFIX.fullmatch(e.value)
                out.append(text[pos:e.start])
                out.append(amount_bucket(_amount_tenge(m)))
                report.append(("AMOUNT", "bucket"))
                pos = e.end
                continue
            token = self._token(e.kind, e.value)
            out.append(text[pos:e.start])
            out.append(token)
            report.append((e.kind, token))
            pos = e.end
        out.append(text[pos:])
        return AnonymizedText("".join(out), report)

    def anonymize_obj(self, obj: Any, *, bucket_amounts: bool = False) -> Any:
        """Рекурсивно обезличивает строки в dict/list (например, транзакции)."""
        if isinstance(obj, str):
            return self.anonymize(obj, bucket_amounts=bucket_amounts).text
        if isinstance(obj, dict):
            return {k: self.anonymize_obj(v, bucket_amounts=bucket_amounts) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self.anonymize_obj(v, bucket_amounts=bucket_amounts) for v in obj]
        return obj

    def deanonymize(self, text: str) -> str:
        return re.sub(r"\[[A-Z]+_\d+\]", lambda m: self._reverse.get(m.group(), m.group()), text)

    @property
    def mapping(self) -> dict[str, str]:
        """token → исходное значение. Никогда не отправлять во внешние сервисы."""
        return dict(self._reverse)


class PIILeakError(RuntimeError):
    pass


class ConsentRequired(PermissionError):
    pass


# Остаточные признаки ПДн, которые грубее детекторов: длинные цифровые
# последовательности, IBAN-префикс, «@». Срабатывание блокирует запрос.
_RESIDUAL = (
    ("DIGITS", re.compile(r"\d(?:[\s()\-]?\d){9,}")),
    ("IBAN_PREFIX", re.compile(r"(?<![A-Za-z])KZ\d{2}", re.IGNORECASE)),
    ("AT_SIGN", re.compile(r"@")),
)


def assert_clean(text: str, known_names: Iterable[str] = (), *, amounts: bool = False) -> None:
    """Финальная проверка перед отправкой наружу: при остаточных ПДн — исключение."""
    kinds = {e.kind for e in detect(text, known_names, include_amounts=amounts)}
    kinds |= {name for name, rx in _RESIDUAL if rx.search(text)}
    if kinds:
        raise PIILeakError(f"в тексте остались персональные данные: {sorted(kinds)}")


def prepare_external(text: str, anonymizer: Anonymizer, *, cross_border_consent: bool) -> str:
    """Единственный путь текста к внешней LLM (ТЗ, разделы 2 и 6).

    Без согласия на трансграничную передачу — ConsentRequired (чат должен уйти на
    локальную модель). При остаточных ПДн после обезличивания — PIILeakError.
    """
    if not cross_border_consent:
        raise ConsentRequired("нет согласия на трансграничную передачу — используйте локальную модель")
    safe = anonymizer.anonymize(text, bucket_amounts=True).text
    assert_clean(safe, anonymizer.known_names, amounts=True)
    return safe
