"""Единственная точка исходящих HTTP-запросов приложения (LLM, NCANode, Telegram, НБ РК)."""

import json
import urllib.request
from typing import Any
from urllib.parse import urlparse


def _check(url: str) -> None:
    if urlparse(url).scheme not in ("http", "https"):
        raise ValueError(f"недопустимая схема URL: {url}")


def post_json(url: str, body: Any, *, timeout: float, headers: dict[str, str] | None = None) -> Any:
    _check(url)
    req = urllib.request.Request(url, data=json.dumps(body).encode(),  # noqa: S310 — схема проверена
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — схема проверена выше
        return json.load(resp)


def get_text(url: str, *, timeout: float) -> str:
    _check(url)
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 — схема проверена выше
        return resp.read().decode("utf-8")
