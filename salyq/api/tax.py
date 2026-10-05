from decimal import Decimal
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from salyq.tax import ConfigNotFound, TaxInputError, available_years, calculate_self_social, calculate_simplified

router = APIRouter(prefix="/tax", tags=["tax"])


class SimplifiedRequest(BaseModel):
    year: int
    half: Literal[1, 2]
    income_tiyn: int = Field(ge=0)
    ytd_income_before_tiyn: int = Field(0, ge=0)
    maslikhat_rate: Decimal | None = None
    so_accrued_tiyn: int = Field(0, ge=0)


class SocialRequest(BaseModel):
    year: int
    declared_income_tiyn: int = Field(ge=0)


@router.get("/years")
def years() -> list[int]:
    return available_years()


@router.post("/simplified")
def simplified(req: SimplifiedRequest) -> dict[str, Any]:
    try:
        r = calculate_simplified(**req.model_dump())
    except ConfigNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaxInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "year": r.year, "half": r.half, "income_tiyn": r.income_tiyn, "rate": str(r.rate),
        "tax_computed_tiyn": r.tax_computed_tiyn, "components_tiyn": r.components_tiyn,
        "total_payable_tiyn": r.total_payable_tiyn,
        "warnings": [w.__dict__ for w in r.warnings], "trace": r.trace.to_dict(),
    }


@router.post("/self-social")
def self_social(req: SocialRequest) -> dict[str, Any]:
    try:
        r = calculate_self_social(**req.model_dump())
    except ConfigNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaxInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"year": r.year, "payments_tiyn": r.payments_tiyn, "total_tiyn": r.total_tiyn, "trace": r.trace.to_dict()}
