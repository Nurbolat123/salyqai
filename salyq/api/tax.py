from datetime import date
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from salyq.db import get_session
from salyq.tax import ConfigNotFound, TaxInputError, available_years, calculate_self_social, calculate_simplified
from salyq.tax.region_rates import region_income

router = APIRouter(prefix="/tax", tags=["tax"])


class RegionIn(BaseModel):
    region_code: str = Field(min_length=1, max_length=16)
    activity_code: str = ""
    income_tiyn: int = Field(ge=0)


class SimplifiedRequest(BaseModel):
    year: int
    half: Literal[1, 2]
    regions: list[RegionIn] = Field(min_length=1)
    ytd_income_before_tiyn: int = Field(0, ge=0)
    so_accrued_tiyn: int = Field(0, ge=0)


class SocialRequest(BaseModel):
    year: int
    declared_income_tiyn: int = Field(ge=0)


@router.get("/years")
def years() -> list[int]:
    return available_years()


@router.post("/simplified")
def simplified(req: SimplifiedRequest, session: Annotated[Session, Depends(get_session)]) -> dict[str, Any]:
    """Налог за полугодие. Ставки регионов берутся из справочника region_rates на конец периода."""
    period_end = date(req.year, 6 if req.half == 1 else 12, 30 if req.half == 1 else 31)
    regions = [region_income(session, r.region_code, r.income_tiyn, period_end, r.activity_code) for r in req.regions]
    try:
        r = calculate_simplified(
            year=req.year, half=req.half, regions=regions,
            ytd_income_before_tiyn=req.ytd_income_before_tiyn, so_accrued_tiyn=req.so_accrued_tiyn,
        )
    except ConfigNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except TaxInputError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {
        "year": r.year, "half": r.half, "income_tiyn": r.income_tiyn,
        "regions": [{"region_code": x.region_code, "income_tiyn": x.income_tiyn, "rate": str(x.rate),
                     "tax_tiyn": x.tax_tiyn} for x in r.regions],
        "tax_computed_tiyn": r.tax_computed_tiyn, "components_tiyn": r.components_tiyn,
        "total_payable_tiyn": r.total_payable_tiyn, "limit_tiyn": r.limit_tiyn, "limit_used": str(r.limit_used),
        "deadlines": {k: v.isoformat() for k, v in r.deadlines.items()},
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
