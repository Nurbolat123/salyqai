from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from salyq.db import get_session
from salyq.settings import get_settings
from salyq.statements import StatementParseError, parse_kaspi_statement
from salyq.statements.repository import save_statement

router = APIRouter(prefix="/statements", tags=["statements"])


@router.post("/kaspi")
async def upload_kaspi(
    file: UploadFile,
    session: Annotated[Session, Depends(get_session)],
    account_iban: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    limit = get_settings().max_upload_bytes
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(413, "файл слишком большой")
    try:
        parsed = parse_kaspi_statement(data, file.filename or "")
        result = save_statement(session, parsed, raw=data, filename=file.filename or "", account_iban=account_iban)
    except StatementParseError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "import_id": result.import_id, "account_iban": result.account_iban, "parsed": result.parsed,
        "inserted": result.inserted, "duplicates": result.duplicates,
        "skipped": [{"row": r, "reason": why} for r, why in result.skipped],
    }
