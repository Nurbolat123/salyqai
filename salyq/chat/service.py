"""AI-чат (ТЗ 4.7): RAG по базе знаний + инструменты движка; локальная или внешняя модель.

Маршрутизация:
- есть согласие на трансграничную передачу и настроена внешняя модель → внешняя,
  всё, что уходит наружу, проходит prepare_external (обезличивание, суммы
  диапазонами, блокировка при остаточных ПДн);
- иначе → локальная модель в ЦОДе РК;
- модели нет или она недоступна → ответ без LLM: инструменты по ключевым словам и
  статьи базы знаний.
"""

import json
import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from salyq.auth import audit, consents
from salyq.auth.clock import utcnow
from salyq.chat import kb
from salyq.chat.providers import ChatProvider, LLMError, OpenAICompatibleProvider
from salyq.chat.tools import SPECS, ToolResult, run_tool
from salyq.models import ChatMessage, ExpertQuestion, User
from salyq.privacy import Anonymizer, PIILeakError, prepare_external
from salyq.settings import Settings

MAX_TOOL_ROUNDS = 3
HISTORY = 6
SYSTEM = """Ты — Salyq, помощник ИП на упрощённой декларации в Казахстане. Отвечай по-русски или
по-казахски (на языке вопроса), кратко и по делу. Все суммы, ставки и сроки бери ТОЛЬКО из
инструментов и базы знаний, не придумывай цифр. Если вопрос сложный или спорный — предложи
кнопку «Передать эксперту». Ты не даёшь юридических гарантий.

База знаний:
{kb}"""
UNVERIFIED_NOTE = "Ответ основан на материалах, которые ещё не проверены экспертом."


@dataclass
class ChatAnswer:
    text: str
    provider: str  # local | external | none
    cards: list[dict[str, Any]] = field(default_factory=list)
    sources: list[dict[str, Any]] = field(default_factory=list)


def choose_provider(session: Session, user: User, settings: Settings) -> ChatProvider | None:
    if settings.external_llm_base_url and consents.has_consent(session, user, "cross_border"):
        return OpenAICompatibleProvider("external", settings.external_llm_base_url, settings.external_llm_model,
                                        settings.external_llm_api_key)
    if settings.llm_base_url:
        return OpenAICompatibleProvider("local", settings.llm_base_url, settings.llm_model, settings.llm_api_key)
    return None


class _Outbound:
    """Всё, что уходит в модель. Для внешней — через шлюз обезличивания."""

    def __init__(self, provider: ChatProvider, user: User):
        self.external = provider.kind == "external"
        self.anon = Anonymizer(known_names=[user.full_name])

    def __call__(self, text: str) -> str:
        return prepare_external(text, self.anon, cross_border_consent=True) if self.external else text

    def back(self, text: str) -> str:
        return self.anon.deanonymize(text) if self.external else text


_KEYWORD_TOOLS = (
    (re.compile(r"налог|сколько.*(плат|заплат)|910|лимит|соцплат|опв|копилк", re.I), "calculate_tax"),
    (re.compile(r"операц|выписк|провер|размет", re.I), "show_transactions"),
    (re.compile(r"ставк|маслихат|регион", re.I), "compare_rates"),
)


def _fallback(session: Session, user: User, question: str, articles: list[kb.Article]) -> ChatAnswer:
    """Без LLM: инструменты по ключевым словам + статьи базы знаний."""
    results = [run_tool(session, user, name, {}) for rx, name in _KEYWORD_TOOLS if rx.search(question)]
    parts = [r.text for r in results] + [f"{a.title}. {a.body}" for a in articles]
    if not parts:
        parts = ["Не нашёл ответа в базе знаний. Нажмите «Передать эксперту» — он ответит лично."]
    return ChatAnswer("\n\n".join(parts), "none", [{"tool": r.name, "data": r.card} for r in results])


def _messages_for_model(session: Session, user: User, out: _Outbound, question: str,
                        articles: list[kb.Article]) -> list[dict[str, Any]]:
    kb_text = "\n\n".join(f"[{a.title}]\n{a.body}" for a in articles) or "(ничего не найдено)"
    history = session.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                              .order_by(ChatMessage.id.desc()).limit(HISTORY)).all()[::-1]
    msgs = [{"role": "system", "content": out(SYSTEM.format(kb=kb_text))}]
    msgs += [{"role": m.role, "content": out(m.content)} for m in history]
    msgs.append({"role": "user", "content": out(question)})
    return msgs


def answer(session: Session, user: User, question: str, provider: ChatProvider | None) -> ChatAnswer:
    articles = kb.search(question)
    result: ChatAnswer
    if provider is None:
        result = _fallback(session, user, question, articles)
    else:
        out = _Outbound(provider, user)
        try:
            msgs = _messages_for_model(session, user, out, question, articles)
            tool_results: list[ToolResult] = []
            reply = provider.chat(msgs, SPECS)
            for _ in range(MAX_TOOL_ROUNDS):
                if not reply.tool_calls:
                    break
                msgs.append({"role": "assistant", "content": reply.content, "tool_calls": [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": json.dumps(c.arguments)}}
                    for c in reply.tool_calls]})
                for call in reply.tool_calls:
                    r = run_tool(session, user, call.name, call.arguments)
                    tool_results.append(r)
                    msgs.append({"role": "tool", "tool_call_id": call.id, "content": out(r.text)})
                reply = provider.chat(msgs, SPECS)
            text = out.back(reply.content or "")
            result = ChatAnswer(text, provider.kind, [{"tool": r.name, "data": r.card} for r in tool_results])
        except PIILeakError:
            # Запрос заблокирован шлюзом — отвечаем без внешней модели
            result = _fallback(session, user, question, articles)
            result.text = "Запрос содержал персональные данные и не был отправлен во внешнюю модель.\n\n" + result.text
        except LLMError:
            result = _fallback(session, user, question, articles)
    result.sources = [{"slug": a.slug, "title": a.title, "verified": a.verified} for a in articles]
    if any(not a.verified for a in articles):
        result.text += f"\n\n{UNVERIFIED_NOTE}"

    now = utcnow()
    session.add(ChatMessage(user_id=user.id, role="user", content=question, meta={}, created_at=now))
    session.add(ChatMessage(user_id=user.id, role="assistant", content=result.text, created_at=now,
                            meta={"provider": result.provider, "tools": [c["tool"] for c in result.cards],
                                  "sources": [s["slug"] for s in result.sources]}))
    audit.record(session, "chat.message", actor_type="user", actor_id=user.id, provider=result.provider,
                 tools=[c["tool"] for c in result.cards])
    session.commit()
    return result


def escalate(session: Session, user: User, text: str | None = None) -> ExpertQuestion:
    """«Передать эксперту»: вопрос + последние сообщения чата."""
    history = session.scalars(select(ChatMessage).where(ChatMessage.user_id == user.id)
                              .order_by(ChatMessage.id.desc()).limit(HISTORY)).all()[::-1]
    body = (text or "").strip()
    if history:
        body += "\n\nИз чата:\n" + "\n".join(f"{'Клиент' if m.role == 'user' else 'Salyq'}: {m.content}" for m in history)
    if not body.strip():
        raise ValueError("опишите вопрос")
    q = ExpertQuestion(user_id=user.id, text=body.strip(), created_at=utcnow())
    session.add(q)
    session.flush()
    audit.record(session, "question.create", actor_type="user", actor_id=user.id, object_type="question", object_id=q.id)
    session.commit()
    return q
