from sqlalchemy.orm import Session

from salyq.crypto import blind_index
from salyq.models import User
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


def make_user(session: Session, iin: str | None = None, full_name: str = "Тестов Тест Тестович") -> User:
    iin = iin or make_kz_id("85010130012")
    user = User(iin=iin, iin_hash=blind_index(iin, "iin"), full_name=full_name, employees_count=0)
    session.add(user)
    session.commit()
    return user
