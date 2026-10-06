import json

import pytest
from sqlalchemy import select

from salyq.auth import consents
from salyq.categorize import service as cat
from salyq.chat import kb, service
from salyq.chat.providers import LLMError, LLMReply, ToolCall
from salyq.models import AuditLog, ChatMessage, ExpertQuestion
from salyq.settings import Settings
from tests.helpers import make_kz_id, make_user
from tests.test_categorize import import_rows

V = dict(consents.CONSENT_VERSIONS)
IIN = make_kz_id("85010130012")


class FakeProvider:
    def __init__(self, kind, replies):
        self.kind, self.replies, self.seen = kind, list(replies), []

    def chat(self, messages, tools):
        self.seen.append(json.loads(json.dumps(messages)))
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def user(db):
    u = make_user(db, iin=IIN, full_name="Иванов Иван Иванович")
    u.region_code = "750000000"
    db.commit()
    for t in ("pd_processing", "automated_processing"):
        consents.grant(db, u, t, V[t])
    txs = import_rows(db, u, [["01.07.2026", "1", "", "1 234 567", "ТОО Ромашка", "Оплата по договору"]])
    cat.confirm(db, u, txs[0], category="income_sales")
    return u


class TestKb:
    def test_search(self):
        assert kb.search("когда сдавать 910")[0].slug == "deadlines"
        assert kb.search("перевод от сестры это доход?")[0].slug == "what-is-income"
        assert kb.search("") == []


class TestRouting:
    def test_choose(self, db, user):
        s = Settings(llm_base_url="http://llm.local/v1", external_llm_base_url="https://ext/v1")
        assert service.choose_provider(db, user, s).kind == "local"  # нет согласия на трансграничную передачу
        consents.grant(db, user, "cross_border", V["cross_border"])
        assert service.choose_provider(db, user, s).kind == "external"
        assert service.choose_provider(db, user, Settings()) is None


class TestAnswer:
    def test_tool_loop_local(self, db, user):
        p = FakeProvider("local", [
            LLMReply(None, [ToolCall("c1", "calculate_tax", {"period": "2026H2"})]),
            LLMReply("Налог за второе полугодие — 49 383 ₸."),
        ])
        a = service.answer(db, user, "Сколько мне платить налога?", p)
        assert a.provider == "local" and a.text.startswith("Налог за второе полугодие")
        assert a.cards[0]["tool"] == "calculate_tax"
        assert a.cards[0]["data"]["tax"]["total_payable_tiyn"] == 4_938_300  # точные цифры — от движка
        tool_msg = p.seen[1][-1]
        assert tool_msg["role"] == "tool" and "1 234 567,00 ₸" in tool_msg["content"]  # локальной — как есть

    def test_external_gets_no_pii(self, db, user):
        consents.grant(db, user, "cross_border", V["cross_border"])
        p = FakeProvider("external", [
            LLMReply(None, [ToolCall("c1", "show_transactions", {})]),
            LLMReply("Иван, у вас всё подтверждено, [PERSON_1]."),
        ])
        q = f"Я Иванов Иван Иванович, ИИН {IIN}, телефон +7 701 123 45 67. Получил 1 234 567 ₸, всё ли размечено?"
        a = service.answer(db, user, q, p)
        sent = json.dumps(p.seen, ensure_ascii=False)
        for secret in (IIN, "Иванов Иван Иванович", "701 123", "1 234 567"):
            assert secret not in sent
        assert "[СУММА В ТЕНГЕ: 1 млн–5 млн]" in sent
        assert a.text.startswith("Иван, у вас всё подтверждено, Иванов Иван Иванович.")  # токен вернули обратно
        assert a.cards[0]["data"]["items"][0]["amount_tiyn"] == 123_456_700  # пользователю — точно

    def test_llm_down_falls_back(self, db, user):
        a = service.answer(db, user, "Сколько налога платить?", FakeProvider("local", [LLMError("down")]))
        assert a.provider == "none"
        assert a.cards[0]["tool"] == "calculate_tax"

    def test_no_provider(self, db, user):
        a = service.answer(db, user, "Перевод от сестры — это доход?", None)
        assert a.provider == "none" and "Что считается доходом" in a.text
        assert a.sources[0]["slug"] == "what-is-income"
        assert service.UNVERIFIED_NOTE in a.text

    def test_unknown_question(self, db, user):
        a = service.answer(db, user, "xyz qwerty", None)
        assert "Передать эксперту" in a.text

    def test_history_saved_encrypted(self, db, user):
        from sqlalchemy import text

        service.answer(db, user, f"мой ИИН {IIN}", None)
        rows = db.scalars(select(ChatMessage).order_by(ChatMessage.id)).all()
        assert [r.role for r in rows] == ["user", "assistant"]
        raw = db.execute(text("SELECT content FROM chat_messages")).scalars().all()
        assert all(r.startswith("v1:") for r in raw)
        entry = db.scalar(select(AuditLog).where(AuditLog.action == "chat.message"))
        assert IIN not in repr(entry.details)


class TestEscalate:
    def test_escalate_with_history(self, db, user):
        service.answer(db, user, "Как учитывать доход из-за рубежа?", None)
        q = service.escalate(db, user, "Нужна консультация")
        assert q.text.startswith("Нужна консультация") and "Из чата:" in q.text
        assert db.scalar(select(ExpertQuestion)).id == q.id

    def test_empty(self, db):
        u = make_user(db, iin=make_kz_id("90020240012"))
        with pytest.raises(ValueError):
            service.escalate(db, u, " ")


class TestApi:
    def test_chat_flow(self, client):
        r = client.post("/api/v1/chat/messages", json={"text": "Какие сроки сдачи 910?"})
        assert r.status_code == 200 and r.json()["provider"] == "none"
        assert len(client.get("/api/v1/chat/messages").json()) == 2
        q = client.post("/api/v1/chat/escalate", json={"text": "Помогите"}).json()
        assert client.get("/api/v1/chat/questions").json()[0]["id"] == q["question_id"]
        assert client.post("/api/v1/chat/messages", json={"text": ""}).status_code == 422
