"""Тестовые двойники RAG Service (только для тестов: в сервисе — настоящие Jina и LLM)."""

import re
from collections.abc import Sequence

import numpy as np
import pytest

from corag_db.models import EMBED_DIM
from corag_rag.errors import LLMTimeout, LLMUnavailable

WORD = re.compile(r"\w+")


class WhitespaceTokens:
    """Токен = последовательность непробельных символов."""

    def spans(self, text: str) -> list[tuple[int, int]]:
        return [m.span() for m in re.finditer(r"\S+", text)]


class FakeEmbedder:
    """Детерминированные векторы: слова хэшируются в EMBED_DIM измерений, затем L2-норма.

    У текстов с общими словами косинусное сходство высокое, у текстов без общих слов — около 0.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim
        self.calls: list[tuple[str, int]] = []

    def vector(self, text: str) -> list[float]:
        vec = np.zeros(self.dim, dtype=np.float64)
        for word in WORD.findall(text.lower()):
            # Устойчивый хэш (встроенный hash() зависит от PYTHONHASHSEED)
            vec[sum(map(ord, word)) * 31 % self.dim] += 1.0
        norm = np.linalg.norm(vec)
        if norm == 0:
            vec[0] = 1.0
            norm = 1.0
        return (vec / norm).tolist()

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(("query", len(texts)))
        return [self.vector(t) for t in texts]

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        self.calls.append(("passage", len(texts)))
        return [self.vector(t) for t in texts]


class FakeLLM:
    """Запоминает вызовы; по флагу имитирует недоступность или таймаут LLM."""

    model = "fake-llm"

    def __init__(self) -> None:
        self.calls: list[list[dict]] = []
        self.fail: type[Exception] | None = None
        self.answer = "Ответ по фрагментам [1]."

    async def chat(self, messages: list[dict], timeout_s: float) -> str:
        self.calls.append(messages)
        if self.fail is LLMUnavailable:
            raise LLMUnavailable("LLM-сервер недоступен (тест)")
        if self.fail is LLMTimeout:
            raise LLMTimeout("LLM не ответила (тест)")
        return self.answer


@pytest.fixture
def tokens() -> WhitespaceTokens:
    return WhitespaceTokens()


@pytest.fixture
def embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()
