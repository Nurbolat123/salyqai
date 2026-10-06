from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from salyq.api.deps import DbSession, PdUser, get_classifier
from salyq.categorize import service
from salyq.categorize.categories import CATEGORIES, REVIEW_THRESHOLD
from salyq.models import BankAccount, Transaction

router = APIRouter(prefix="/transactions", tags=["transactions"])


def tx_out(tx: Transaction, account: BankAccount | None = None) -> dict[str, Any]:
    return {
        "id": tx.id, "date": tx.op_date, "direction": tx.direction,
        "amount_tiyn": tx.amount_tiyn, "currency": tx.currency, "fx_rate": None if tx.fx_rate is None else str(tx.fx_rate),
        "amount_kzt_tiyn": tx.amount_kzt_tiyn, "reference": tx.reference,
        "counterparty": tx.counterparty, "knp": tx.knp, "purpose": tx.purpose,
        "category": tx.category, "confidence": None if tx.confidence is None else float(tx.confidence),
        "category_source": tx.category_source, "status": service.status_of(tx),
        "review_reason": service.review_reason(tx), "region_code": tx.region_code,
        "account": account.iban_masked if account else None,
    }


@router.get("/categories")
def categories() -> dict[str, Any]:
    return {"categories": CATEGORIES, "review_threshold": REVIEW_THRESHOLD}


@router.get("")
def list_transactions(
    session: DbSession,
    user: PdUser,
    status: Literal["review", "suggested", "confirmed"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    rows = session.execute(
        service.user_transactions(user).add_columns(BankAccount)
        .order_by(Transaction.op_date.desc(), Transaction.id.desc())
    ).all()
    items = [(tx, acc) for tx, acc in rows if status is None or service.status_of(tx) == status]
    counts = {s: 0 for s in ("review", "suggested", "confirmed")}
    for tx, _ in rows:
        counts[service.status_of(tx)] += 1
    return {
        "total": len(items), "counts": counts,
        "items": [tx_out(tx, acc) for tx, acc in items[offset:offset + limit]],
    }


class TxUpdate(BaseModel):
    category: str
    region_code: str | None = Field(None, pattern=r"^(\d{9})?$")


@router.patch("/{tx_id}")
def update_transaction(
    tx_id: int, body: TxUpdate, session: DbSession, user: PdUser, classifier=Depends(get_classifier)
) -> dict[str, Any]:
    tx = service.get_user_transaction(session, user, tx_id)
    if tx is None:
        raise HTTPException(404, "операция не найдена")
    try:
        service.confirm(session, user, tx, category=body.category, region_code=body.region_code, classifier=classifier)
    except service.CategorizeError as exc:
        raise HTTPException(422, str(exc)) from exc
    return tx_out(tx, session.get(BankAccount, tx.account_id))


class BulkConfirm(BaseModel):
    ids: list[int] | None = None  # None — все предложенные


@router.post("/confirm-suggested")
def confirm_suggested(body: BulkConfirm, session: DbSession, user: PdUser) -> dict[str, int]:
    return {"confirmed": service.confirm_suggested(session, user, body.ids)}


@router.post("/recategorize")
def recategorize(session: DbSession, user: PdUser, classifier=Depends(get_classifier)) -> dict[str, int]:
    """Переразметить неподтверждённые операции (например, после согласия по ст. 19-1)."""
    return {"processed": service.categorize(session, user, classifier=classifier)}
