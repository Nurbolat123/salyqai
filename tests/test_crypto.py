import pytest

from salyq.crypto import blind_index, decrypt, encrypt, mask_iban


def test_roundtrip_and_randomized():
    a, b = encrypt("850101300128"), encrypt("850101300128")
    assert a != b and a.startswith("v1:")
    assert decrypt(a) == decrypt(b) == "850101300128"


def test_tampering_detected():
    token = encrypt("секрет")
    broken = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
    with pytest.raises(Exception):
        decrypt(broken)


def test_blind_index():
    assert blind_index("x", "iban") == blind_index("x", "iban")
    assert blind_index("x", "iban") != blind_index("x", "tx")  # разные домены
    assert len(blind_index("x", "iban")) == 64


def test_mask_iban():
    assert mask_iban("KZ18722S000012345678") == "KZ18…5678"
