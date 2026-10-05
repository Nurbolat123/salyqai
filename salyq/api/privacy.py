from fastapi import APIRouter
from pydantic import BaseModel

from salyq.privacy import Anonymizer

router = APIRouter(prefix="/privacy", tags=["privacy"])


class AnonymizeRequest(BaseModel):
    text: str
    known_names: list[str] = []


class AnonymizeResponse(BaseModel):
    text: str
    entities: list[dict[str, str]]


@router.post("/anonymize")
def anonymize(req: AnonymizeRequest) -> AnonymizeResponse:
    """Предпросмотр обезличивания. Таблица соответствий наружу не отдаётся."""
    result = Anonymizer(known_names=req.known_names).anonymize(req.text)
    return AnonymizeResponse(
        text=result.text, entities=[{"kind": k, "token": t} for k, t in result.entities]
    )
