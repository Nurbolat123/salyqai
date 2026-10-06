"""Адаптер NCANode: ответы сервера подменяются, проверяется логика разбора."""

import base64

import pytest

from salyq.auth.ecp import EcpVerificationError, NcaNodeVerifier
from salyq.settings import Settings
from tests.helpers import make_kz_id

IIN = make_kz_id("85010130012")
NONCE = "nonce-123"


def verifier(responses: dict) -> NcaNodeVerifier:
    v = NcaNodeVerifier(Settings(ecp_verifier="ncanode", ncanode_url="http://ncanode:14579"))
    v.calls = []

    def fake_post(path, body):
        v.calls.append((path, body))
        r = responses[path]
        if isinstance(r, Exception):
            raise r
        return r

    v._post = fake_post
    return v


def ok_verify(subject=None, valid=True, cert_valid=True, signers=1):
    subject = subject or {"iin": IIN, "lastName": "ИВАНОВ", "commonName": "ИВАН", "middleName": "ИВАНОВИЧ"}
    return {"valid": valid, "signers": [{"certificates": [{"valid": cert_valid, "subject": subject}]}] * signers}


def extract(data=NONCE):
    return {"data": base64.b64encode(data.encode()).decode()}


def test_success():
    v = verifier({"/cms/verify": ok_verify(), "/cms/extract": extract()})
    s = v.verify("CMS", NONCE)
    assert (s.iin, s.full_name) == (IIN, "ИВАНОВ ИВАН ИВАНОВИЧ")
    assert v.calls[0] == ("/cms/verify", {"cms": "CMS", "revocationCheck": ["OCSP"]})


def test_iin_with_prefix_and_common_name_only():
    v = verifier({"/cms/verify": ok_verify({"iin": f"IIN{IIN}", "commonName": "ИВАНОВ ИВАН"}), "/cms/extract": extract()})
    assert v.verify("CMS", NONCE).full_name == "ИВАНОВ ИВАН"


@pytest.mark.parametrize(
    ("verify", "extracted", "match"),
    [
        (ok_verify(valid=False), extract(), "недействительна"),
        (ok_verify(cert_valid=False), extract(), "отозван"),
        (ok_verify(signers=2), extract(), "один подписант"),
        (ok_verify(), extract("другие данные"), "не те данные"),
        (ok_verify({"bin": "050140000011", "commonName": "ТОО"}), extract(), "ИИН"),
        (RuntimeError("timeout"), extract(), "недоступен"),
    ],
)
def test_rejects(verify, extracted, match):
    if isinstance(verify, RuntimeError):
        v = NcaNodeVerifier(Settings(ecp_verifier="ncanode", ncanode_url="http://127.0.0.1:9", ncanode_timeout_seconds=0.5))
    else:
        v = verifier({"/cms/verify": verify, "/cms/extract": extracted})
    with pytest.raises(EcpVerificationError, match=match):
        v.verify("CMS", NONCE)
