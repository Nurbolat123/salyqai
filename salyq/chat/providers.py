"""LLM-провайдеры чата (OpenAI-совместимый API: vLLM в ЦОДе РК или внешний с ZDR)."""

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from salyq.http import post_json


class LLMError(RuntimeError):
    pass


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)


class ChatProvider(Protocol):
    kind: str  # local | external

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply: ...


class OpenAICompatibleProvider:
    def __init__(self, kind: str, base_url: str, model: str, api_key: str | None = None, timeout: float = 60.0):
        self.kind, self.url, self.model, self.api_key, self.timeout = (
            kind, base_url.rstrip("/") + "/chat/completions", model, api_key, timeout)

    def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> LLMReply:  # pragma: no cover — сеть
        body = {"model": self.model, "temperature": 0.2, "messages": messages}
        if tools:
            body["tools"] = tools
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        try:
            msg = post_json(self.url, body, timeout=self.timeout, headers=headers)["choices"][0]["message"]
        except Exception as exc:
            raise LLMError(f"модель недоступна: {exc}") from exc
        calls = [
            ToolCall(c["id"], c["function"]["name"], json.loads(c["function"].get("arguments") or "{}"))
            for c in msg.get("tool_calls") or []
        ]
        return LLMReply(msg.get("content"), calls)
