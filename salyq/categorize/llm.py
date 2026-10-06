"""Классификатор на локальной LLM (vLLM, OpenAI-совместимый API) — ТЗ 3, 4.3.

Модель работает в ЦОДе в РК, поэтому данные не покидают страну. Всё равно
отправляем минимум: направление, КНП и обезличенное назначение — ни ИИН, ни
счетов, ни имён модели для категории не нужно.
"""

import json
from typing import Protocol

from salyq.categorize.categories import CATEGORIES, allowed
from salyq.categorize.rules import Suggestion
from salyq.http import post_json
from salyq.privacy import Anonymizer

PROMPT = """Ты помощник бухгалтера ИП на упрощённой декларации в Казахстане.
Определи категорию банковской операции. Категории:
{categories}
Ответь строго JSON: {{"category": "<код>", "confidence": <число от 0 до 1>}}.

Операция: направление={direction}, КНП={knp}, назначение: {text}"""


class Classifier(Protocol):
    def classify(self, *, direction: str, knp: str, text: str) -> Suggestion | None: ...


class OpenAICompatibleClassifier:
    def __init__(self, base_url: str, model: str, timeout: float = 10.0, api_key: str | None = None):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.timeout, self.api_key = model, timeout, api_key

    def _complete(self, prompt: str) -> str:
        body = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
        return post_json(self.url, body, timeout=self.timeout, headers=headers)["choices"][0]["message"]["content"]

    def classify(self, *, direction: str, knp: str, text: str) -> Suggestion | None:
        safe_text = Anonymizer().anonymize(text).text
        prompt = PROMPT.format(
            categories="\n".join(f"- {k}: {v}" for k, v in CATEGORIES.items()),
            direction="поступление" if direction == "in" else "списание", knp=knp or "нет", text=safe_text,
        )
        try:
            answer = json.loads(self._complete(prompt))
            category, confidence = answer["category"], float(answer["confidence"])
        except Exception:  # сеть, таймаут, мусор в ответе — разметка продолжится по правилам
            return None
        if category not in CATEGORIES or not allowed(category, direction):
            return None
        return Suggestion(category, min(max(confidence, 0.0), 1.0), "llm")
