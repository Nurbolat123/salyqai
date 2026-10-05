from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from salyq.db import get_session
from salyq.settings import get_settings
from salyq.statements import PARSERS, StatementParseError
from salyq.statements.repository import save_statement

router = APIRouter(prefix="/statements", tags=["statements"])


@router.post("")
async def upload_statement(
    file: UploadFile,
    session: Annotated[Session, Depends(get_session)],
    bank: Annotated[str, Form()] = "kaspi",
    account_iban: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    parser = PARSERS.get(bank)
    if parser is None:
        raise HTTPException(422, f"банк {bank!r} пока не поддерживается; доступны: {sorted(PARSERS)}")
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "файл слишком большой")
    try:
        parsed = parser(data, file.filename or "")
        result = save_statement(session, parsed, raw=data, filename=file.filename or "", account_iban=account_iban)
    except StatementParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "statement_id": result.statement_id, "account": result.account_iban_masked,
        "period_from": parsed.period_from, "period_to": parsed.period_to, "parsed": result.parsed,
        "inserted": result.inserted, "duplicates": result.duplicates,
        "skipped": [{"row": r, "reason": why} for r, why in result.skipped],
    }
