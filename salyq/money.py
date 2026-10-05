"""Денежные суммы. Внутри системы всё хранится в тиынах (int), 1 тенге = 100 тиын.

float запрещён на входе: только int (тиыны), Decimal или строка.
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

TIYN_PER_TENGE = 100


class MoneyError(ValueError):
    pass


def to_tiyn(amount: str | Decimal | int) -> int:
    """Тенге (строка/Decimal/int) → тиыны. Допускает пробелы-разделители и запятую."""
    if isinstance(amount, bool) or isinstance(amount, float):
        raise MoneyError("float не допускается для денежных сумм, используйте str или Decimal")
    if isinstance(amount, int):
        return amount * TIYN_PER_TENGE
    if isinstance(amount, str):
        cleaned = (
            amount.replace(" ", "")
            .replace(" ", "")
            .replace(" ", "")
            .replace("₸", "")
            .replace("KZT", "")
            .replace(",", ".")
            .strip()
        )
        try:
            amount = Decimal(cleaned)
        except InvalidOperation as exc:
            raise MoneyError(f"не удалось разобрать сумму: {amount!r}") from exc
    if not amount.is_finite():
        raise MoneyError(f"некорректная сумма: {amount!r}")
    tiyn = amount * TIYN_PER_TENGE
    if tiyn != tiyn.to_integral_value():
        raise MoneyError(f"сумма точнее тиына: {amount}")
    return int(tiyn)


def mul_rate(tiyn: int, rate: Decimal) -> int:
    """tiyn × ставка, округление до тиына по правилу half-up."""
    return int((Decimal(tiyn) * rate).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def round_to_tenge(tiyn: int) -> int:
    """Округление до целых тенге (половина — вверх), результат в тиынах."""
    return int(
        (Decimal(tiyn) / TIYN_PER_TENGE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        * TIYN_PER_TENGE
    )


def format_tiyn(tiyn: int) -> str:
    sign = "-" if tiyn < 0 else ""
    tenge, rest = divmod(abs(tiyn), TIYN_PER_TENGE)
    return f"{sign}{tenge:,}".replace(",", " ") + f",{rest:02d} ₸"
