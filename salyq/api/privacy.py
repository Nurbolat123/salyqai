from fastapi import APIRouter
from pydantic import BaseModel

from salyq.api.deps import PdUser
from salyq.privacy import Anonymizer

router = APIRouter(prefix="/privacy", tags=["privacy"])


class AnonymizeRequest(BaseModel):
    text: str
    known_names: list[str] = []
    bucket_amounts: bool = True  # как для внешней LLM


class AnonymizeResponse(BaseModel):
    text: str
    entities: list[dict[str, str]]


@router.post("/anonymize")
def anonymize(req: AnonymizeRequest, user: PdUser) -> AnonymizeResponse:
    """Предпросмотр обезличивания. Таблица соответствий наружу не отдаётся."""
    result = Anonymizer(known_names=req.known_names).anonymize(req.text, bucket_amounts=req.bucket_amounts)
    return AnonymizeResponse(
        text=result.text, entities=[{"kind": k, "token": t} for k, t in result.entities]
    )
