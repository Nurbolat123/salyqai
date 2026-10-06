from datetime import date
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from salyq.api.deps import CurrentUser, DbSession
from salyq.auth import audit
from salyq.models import User

router = APIRouter(prefix="/me", tags=["profile"])


def profile(user: User) -> dict[str, Any]:
    return {
        "id": user.id, "full_name": user.full_name, "iin_masked": f"{user.iin[:2]}••••••••{user.iin[-2:]}",
        "region_code": user.region_code, "activity_code": user.activity_code,
        "ip_registered_on": user.ip_registered_on, "employees_count": user.employees_count,
        "declared_income_tiyn": user.declared_income_tiyn, "role": user.role,
    }


class ProfileUpdate(BaseModel):
    region_code: str | None = Field(None, pattern=r"^\d{9}$")  # КАТО
    activity_code: str | None = Field(None, pattern=r"^\d{5}$")  # ОКЭД
    ip_registered_on: date | None = None
    employees_count: int | None = Field(None, ge=0)
    declared_income_tiyn: int | None = Field(None, ge=0)  # заявленный доход в месяц для соцплатежей


@router.get("")
def get_me(user: CurrentUser) -> dict[str, Any]:
    return profile(user)


@router.patch("")
def update_me(body: ProfileUpdate, user: CurrentUser, session: DbSession) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    for key, value in changes.items():
        setattr(user, key, value)
    if changes:
        audit.record(session, "profile.update", actor_type="user", actor_id=user.id,
                     object_type="user", object_id=user.id, fields=sorted(changes))
    session.commit()
    return profile(user)
