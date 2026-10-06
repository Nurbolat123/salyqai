"""Разметка операций, очередь «Проверить» и подтверждения пользователя (ТЗ 4.3)."""

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from salyq.auth import audit, consents
from salyq.auth.clock import utcnow
from salyq.categorize.categories import CATEGORIES, REVIEW_THRESHOLD, allowed
from salyq.categorize.llm import Classifier
from salyq.categorize.rules import Suggestion, TxFacts, apply_rules
from salyq.crypto import blind_index
from salyq.fx import get_rate, to_kzt_tiyn
from salyq.models import BankAccount, CategorizationRule, Transaction, User

Status = Literal["review", "suggested", "confirmed"]


class CategorizeError(ValueError):
    pass


def status_of(tx: Transaction) -> Status:
    if tx.confirmed_by_user:
        return "confirmed"
    if tx.category is None or tx.confidence is None or tx.confidence < Decimal(str(REVIEW_THRESHOLD)):
        return "review"
    if tx.amount_kzt_tiyn is None:  # нет курса НБ РК — сумму в тенге не знаем
        return "review"
    return "suggested"


def review_reason(tx: Transaction) -> str | None:
    if tx.confirmed_by_user:
        return None
    if tx.category is None:
        return "no_category" if tx.category_source != "no_consent" else "no_consent"
    if tx.amount_kzt_tiyn is None:
        return "no_fx_rate"
    if tx.confidence is None or tx.confidence < Decimal(str(REVIEW_THRESHOLD)):
        return "low_confidence"
    return None


def user_transactions(user: User) -> Select:
    return (
        select(Transaction)
        .join(BankAccount, BankAccount.id == Transaction.account_id)
        .where(BankAccount.user_id == user.id)
    )


def get_user_transaction(session: Session, user: User, tx_id: int) -> Transaction | None:
    return session.scalar(user_transactions(user).where(Transaction.id == tx_id))


@dataclass
class _Context:
    own_iban_hashes: set[str]
    personal_rules: dict[tuple[str, str], CategorizationRule]
    auto_allowed: bool


def _context(session: Session, user: User) -> _Context:
    hashes = set(session.scalars(select(BankAccount.iban_hash).where(BankAccount.user_id == user.id)))
    rules = {
        (r.counterparty_hash, r.direction): r
        for r in session.scalars(select(CategorizationRule).where(CategorizationRule.user_id == user.id))
    }
    return _Context(hashes, rules, consents.has_consent(session, user, "automated_processing"))


def _is_own(tx: Transaction, user: User, ctx: _Context) -> bool:
    if tx.counterparty_id and blind_index(tx.counterparty_id, "iin") == user.iin_hash:
        return True
    account = tx.counterparty_account.replace(" ", "").upper()
    return bool(account) and blind_index(account, "iban") in ctx.own_iban_hashes


def _suggest(tx: Transaction, user: User, ctx: _Context, classifier: Classifier | None) -> Suggestion:
    facts = TxFacts(
        direction=tx.direction, knp=tx.knp,
        text=" ".join([tx.purpose, tx.operation, tx.counterparty]).lower(),
        own_transfer=_is_own(tx, user, ctx),
    )
    if facts.own_transfer:
        return apply_rules(facts)
    personal = ctx.personal_rules.get((tx.counterparty_hash, tx.direction))
    if personal is not None:
        return Suggestion(personal.category, 0.99 if personal.confirmations >= 2 else 0.95, "personal_rule")
    best = apply_rules(facts)
    if classifier is not None and best.confidence < REVIEW_THRESHOLD:
        llm = classifier.classify(direction=tx.direction, knp=tx.knp, text=tx.purpose or tx.operation)
        if llm is not None and llm.confidence > best.confidence:
            best = llm
    return best


def _apply_fx(session: Session, tx: Transaction) -> None:
    rate = get_rate(session, tx.currency, tx.op_date)
    tx.fx_rate = rate if tx.currency != "KZT" else None
    tx.amount_kzt_tiyn = to_kzt_tiyn(tx.amount_tiyn, rate) if rate is not None else None


def categorize(
    session: Session,
    user: User,
    transactions: Iterable[Transaction] | None = None,
    classifier: Classifier | None = None,
) -> int:
    """Размечает неподтверждённые операции. Без согласия на автоматизированную обработку
    (ст. 19-1) категорию не предлагаем — операция ждёт ручной разметки."""
    ctx = _context(session, user)
    if transactions is None:
        transactions = session.scalars(user_transactions(user).where(Transaction.confirmed_by_user.is_(False)))
    count = 0
    for tx in transactions:
        if tx.confirmed_by_user:
            continue
        _apply_fx(session, tx)
        if ctx.auto_allowed:
            s = _suggest(tx, user, ctx, classifier)
            tx.category, tx.category_source = s.category, s.source
            tx.confidence = Decimal(str(round(s.confidence, 3))) if s.category else None
        else:
            tx.category, tx.confidence, tx.category_source = None, None, "no_consent"
        count += 1
    session.commit()
    return count


def _learn(session: Session, user: User, tx: Transaction) -> None:
    key = (tx.counterparty_hash, tx.direction)
    rule = session.scalar(select(CategorizationRule).where(
        CategorizationRule.user_id == user.id,
        CategorizationRule.counterparty_hash == key[0],
        CategorizationRule.direction == key[1],
    ))
    now = utcnow()
    if rule is None:
        session.add(CategorizationRule(user_id=user.id, counterparty_hash=key[0], direction=key[1],
                                       category=tx.category, confirmations=1, updated_at=now))
    elif rule.category == tx.category:
        rule.confirmations += 1
        rule.updated_at = now
    else:  # пользователь передумал — правило начинается заново
        rule.category, rule.confirmations, rule.updated_at = tx.category, 1, now


def confirm(
    session: Session,
    user: User,
    tx: Transaction,
    *,
    category: str,
    region_code: str | None = None,
    classifier: Classifier | None = None,
) -> Transaction:
    """Пользователь подтверждает или меняет категорию. Решение пишется в журнал и
    становится персональным правилом для этого контрагента."""
    if category not in CATEGORIES:
        raise CategorizeError(f"неизвестная категория {category}")
    if not allowed(category, tx.direction):
        raise CategorizeError(f"категория {category} невозможна для {'поступления' if tx.direction == 'in' else 'списания'}")
    if tx.amount_kzt_tiyn is None:
        raise CategorizeError("нет курса НБ РК на дату операции — подтвердить сумму в тенге нельзя")
    previous = tx.category
    tx.category, tx.confidence, tx.category_source = category, Decimal(1), "user"
    tx.confirmed_by_user, tx.confirmed_at = True, utcnow()
    if region_code is not None:
        tx.region_code = region_code or None
    _learn(session, user, tx)
    audit.record(session, "transaction.confirm", actor_type="user", actor_id=user.id,
                 object_type="transaction", object_id=tx.id, previous=previous, category=category)
    session.flush()
    # новое правило сразу применяется к остальным неподтверждённым операциям этого контрагента
    same = session.scalars(user_transactions(user).where(
        Transaction.counterparty_hash == tx.counterparty_hash, Transaction.direction == tx.direction,
        Transaction.confirmed_by_user.is_(False),
    )).all()
    categorize(session, user, same, classifier)
    session.commit()
    return tx


def confirm_suggested(session: Session, user: User, ids: list[int] | None = None) -> int:
    """Массово принять предложенные категории (статус suggested). Правил не создаёт."""
    query = user_transactions(user).where(Transaction.confirmed_by_user.is_(False))
    if ids is not None:
        query = query.where(Transaction.id.in_(ids))
    now, confirmed = utcnow(), []
    for tx in session.scalars(query):
        if status_of(tx) == "suggested":
            tx.confirmed_by_user, tx.confirmed_at = True, now
            confirmed.append(tx.id)
    if confirmed:
        audit.record(session, "transaction.confirm_bulk", actor_type="user", actor_id=user.id,
                     object_type="transaction", count=len(confirmed))
    session.commit()
    return len(confirmed)
