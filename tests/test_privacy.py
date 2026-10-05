import pytest

from salyq.privacy import Anonymizer, PIILeakError, assert_clean, detect
from salyq.privacy.validators import iban_ok, kz_id_checksum_ok, kz_id_kind, luhn_ok
from tests.helpers import make_kz_iban, make_kz_id

IIN = make_kz_id("85010130012")  # 01.01.1985, мужчина, XX век
BIN = make_kz_id("05014000001")  # ЮЛ-резидент, зарегистрировано 01.2005
IBAN = make_kz_iban()


def kinds(text, **kw):
    return [e.kind for e in detect(text, **kw)]


class TestValidators:
    def test_kz_id(self):
        assert kz_id_checksum_ok(IIN) and kz_id_checksum_ok(BIN)
        assert not kz_id_checksum_ok(IIN[:-1] + str((int(IIN[-1]) + 1) % 10))
        assert kz_id_kind(IIN) == "IIN"
        assert kz_id_kind(BIN) == "BIN"
        assert kz_id_kind("123456789012") in {"ID", "IIN", "BIN"}

    def test_iban_and_luhn(self):
        assert iban_ok(IBAN)
        assert not iban_ok(IBAN[:-1] + ("0" if IBAN[-1] != "0" else "1"))
        assert luhn_ok("4111111111111111")
        assert not luhn_ok("4111111111111112")


class TestDetection:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (f"ИИН {IIN}", "IIN"),
            (f"ИИН:{IIN}.", "IIN"),
            (f"БИН{BIN}", "BIN"),
            ("номер 123456789012", "ID"),  # неверная контрольная сумма — всё равно маскируем
            (f"счёт {IBAN}", "IBAN"),
            ("счёт KZ86 125K ZT50 0410 0100", "IBAN"),
            ("карта 4400 4301 2345 6789", "CARD"),
            ("карта 4400-43** ****-6789", "CARD"),
            ("карта 440043******6789", "CARD"),
            ("тел +7 701 123 45 67", "PHONE"),
            ("тел 8(701)123-45-67", "PHONE"),
            ("тел 87011234567", "PHONE"),
            ("тел 77011234567", "PHONE"),
            ("пишите a.b-c@mail.kz", "EMAIL"),
        ],
    )
    def test_identifiers(self, text, expected):
        assert kinds(text) == [expected]

    @pytest.mark.parametrize(
        "text",
        [
            "Иванов Иван Иванович",
            "ИВАНОВ ИВАН ИВАНОВИЧ",
            "Петрова Мария Сергеевна",
            "Ильич",  # одиночное слово — не ловим (см. ниже)
            "Сериков Айдос Нурланұлы",
            "Нурлан Ерлан улы",
            "Ахметова Айгерім Серікқызы",
            "Ахметова А.Б.",
            "Ахметова А. Б.",
            "А.Б. Ахметова",
            "Ахметов-Сулейменов Ерлан Маратович",
        ],
    )
    def test_person_names(self, text):
        expected = [] if text == "Ильич" else ["PERSON"]
        assert kinds(text) == expected

    @pytest.mark.parametrize(
        ("text", "masked"),
        [
            ("ИП Жумабаев Ерлан", "Жумабаев Ерлан"),
            ("Получатель: КАСЫМОВ ДАНИЯР", "КАСЫМОВ ДАНИЯР"),
            ("отправитель Смагулова", "Смагулова"),
        ],
    )
    def test_names_by_context(self, text, masked):
        (e,) = detect(text)
        assert (e.kind, e.value) == ("PERSON", masked)

    def test_known_names(self):
        assert kinds("оплата от анна смит", known_names=["Анна Смит"]) == ["PERSON"]

    @pytest.mark.parametrize(
        "text",
        [
            "Сумма 1 250 000,00 ₸ за 01.02.2026",
            "Доход за полугодие 123456789 тенге",
            "ТОО «Ромашка», Kaspi Pay, КНП 710",
            "Оплата по договору № 15/2026 от 10.01.2026",
            "Налог по форме 910.00 за 1 полугодие",
            "Остаток 1234567890 тиын",
        ],
    )
    def test_no_false_positives(self, text):
        assert detect(text) == []


class TestAnonymizer:
    TEXT = (
        f"ИП Иванов Иван Иванович (ИИН {IIN}) получил 150 000 ₸ от ТОО (БИН {BIN}) на счёт {IBAN}. "
        f"Иванов Иван Иванович, тел. +7 701 123 45 67 / 87011234567."
    )

    def test_roundtrip(self):
        a = Anonymizer()
        out = a.anonymize(self.TEXT)
        for secret in (IIN, BIN, IBAN, "Иванов", "701"):
            assert secret not in out.text
        assert "150 000 ₸" in out.text
        assert a.deanonymize(out.text) == self.TEXT.replace("87011234567", "+7 701 123 45 67")

    def test_tokens_are_stable(self):
        a = Anonymizer()
        out = a.anonymize(self.TEXT).text
        assert out.count("[PERSON_1]") == 2
        assert out.count("[PHONE_1]") == 2  # один номер в разных форматах — один токен
        assert a.anonymize("Иванов Иван Иванович").text == "[PERSON_1]"  # между вызовами тоже

    def test_entities_report_has_no_values(self):
        out = Anonymizer().anonymize(self.TEXT)
        flat = repr(out.entities)
        assert IIN not in flat and "Иванов" not in flat

    def test_anonymize_obj(self):
        a = Anonymizer()
        tx = {"amount_tiyn": 100, "counterparty": "СЕРИКОВ АЙДОС НУРЛАНОВИЧ", "ids": [IIN], "purpose": None}
        out = a.anonymize_obj(tx)
        assert out == {"amount_tiyn": 100, "counterparty": "[PERSON_1]", "ids": ["[IIN_1]"], "purpose": None}

    def test_output_is_clean(self):
        a = Anonymizer()
        assert_clean(a.anonymize(self.TEXT).text)
        with pytest.raises(PIILeakError):
            assert_clean(self.TEXT)
