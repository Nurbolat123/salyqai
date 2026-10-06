"""База знаний для RAG (ТЗ 4.7: «по проверенной базе знаний и НПА»).

Статьи — markdown с заголовком YAML (title, verified, tags). Пока статья не
проверена экспертом (verified: false), ответ, опирающийся на неё, помечается.
Поиск — по словам (BM25-подобная оценка); векторный поиск — когда база вырастет.
"""

import math
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

KB_DIR = Path(__file__).parent / "kb"
_WORD = re.compile(r"[\wәғқңөұүһі]+", re.IGNORECASE)


@dataclass(frozen=True)
class Article:
    slug: str
    title: str
    verified: bool
    tags: tuple[str, ...]
    body: str

    @property
    def tokens(self) -> list[str]:
        return _tokens(" ".join([self.title, " ".join(self.tags), self.body]))


def _stem(word: str) -> str:
    # грубая основа слова для русского: первые 5 букв достаточно для короткой базы
    return word[:5]


def _tokens(text: str) -> list[str]:
    return [_stem(w) for w in _WORD.findall(text.lower()) if len(w) > 2]


@lru_cache
def load(kb_dir: Path = KB_DIR) -> tuple[Article, ...]:
    out = []
    for path in sorted(kb_dir.glob("*.md")):
        _, front, body = path.read_text(encoding="utf-8").split("---", 2)
        meta = yaml.safe_load(front)
        out.append(Article(path.stem, meta["title"], bool(meta.get("verified")),
                           tuple(t.strip() for t in str(meta.get("tags", "")).split(",")), body.strip()))
    return tuple(out)


def search(query: str, limit: int = 2, articles: tuple[Article, ...] | None = None) -> list[Article]:
    articles = articles if articles is not None else load()
    q = set(_tokens(query))
    if not q:
        return []
    n = len(articles)
    df = {t: sum(1 for a in articles if t in set(a.tokens)) for t in q}
    scored = []
    for a in articles:
        toks = a.tokens
        score = sum(toks.count(t) / (toks.count(t) + 1.2) * math.log(1 + n / df[t]) for t in q if df[t])
        if score > 0:
            scored.append((score, a))
    return [a for _, a in sorted(scored, key=lambda x: -x[0])[:limit]]
