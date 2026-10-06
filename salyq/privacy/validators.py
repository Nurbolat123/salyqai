"""Проверка контрольных сумм идентификаторов РК и банковских реквизитов."""

from datetime import date

_W1 = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
_W2 = (3, 4, 5, 6, 7, 8, 9, 10, 11, 1, 2)


def kz_id_checksum_ok(value: str) -> bool:
    """Контрольный разряд ИИН/БИН (один алгоритм для обоих)."""
    if len(value) != 12 or not value.isdigit():
        return False
    d = [int(c) for c in value]
    control = sum(a * b for a, b in zip(d[:11], _W1, strict=True)) % 11
    if control == 10:
        control = sum(a * b for a, b in zip(d[:11], _W2, strict=True)) % 11
        if control == 10:
            return False
    return control == d[11]


def kz_id_kind(value: str) -> str:
    """'IIN' | 'BIN' | 'ID' — по структуре 12-значного номера.

    В БИН 5-я цифра — тип организации (4–6); в ИИН это первая цифра дня
    рождения (0–3), поэтому пересечения нет.
    """
    if not kz_id_checksum_ok(value):
        return "ID"
    if value[4] in "456":
        return "BIN"
    yy, mm, dd, century = int(value[0:2]), int(value[2:4]), int(value[4:6]), value[6]
    if century in "0123456":
        base = {"1": 1800, "2": 1800, "3": 1900, "4": 1900, "5": 2000, "6": 2000}.get(century, 1900)
        try:
            date(base + yy, mm, dd)
            return "IIN"
        except ValueError:
            pass
    return "ID"


def iban_ok(value: str) -> bool:
    s = value.replace(" ", "").upper()
    if len(s) < 15 or not s[:2].isalpha() or not s[2:4].isdigit():
        return False
    rearranged = s[4:] + s[:4]
    digits = "".join(str(int(c, 36)) for c in rearranged)
    return int(digits) % 97 == 1


def luhn_ok(value: str) -> bool:
    digits = [int(c) for c in value if c.isdigit()]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0
