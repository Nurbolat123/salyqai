from salyq.privacy.validators import kz_id_checksum_ok


def make_kz_id(prefix11: str) -> str:
    """Дописывает к 11 цифрам контрольный разряд ИИН/БИН (перебором)."""
    for d in "0123456789":
        if kz_id_checksum_ok(prefix11 + d):
            return prefix11 + d
    raise ValueError(f"для {prefix11} контрольный разряд не существует")


def make_kz_iban(bank: str = "722", account: str = "S000012345678") -> str:
    bban = bank + account
    rearranged = bban + "KZ00"
    digits = "".join(str(int(c, 36)) for c in rearranged)
    check = 98 - int(digits) % 97
    return f"KZ{check:02d}{bban}"
