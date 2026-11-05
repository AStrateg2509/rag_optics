"""Доменные исключения RAG Service. В коды gRPC их переводит RagServicer (servicer.py)."""

from corag_db.exceptions import NotFound

__all__ = ["FailedPrecondition", "InvalidArgument", "LLMTimeout", "LLMUnavailable", "NotFound"]


class InvalidArgument(Exception):
    """Некорректный запрос (пустой вопрос, top_k вне диапазона) → INVALID_ARGUMENT."""


class FailedPrecondition(Exception):
    """Операцию нельзя выполнить в текущем состоянии (нет текста страниц) → FAILED_PRECONDITION."""


class LLMUnavailable(Exception):
    """LLM-сервер недоступен или модель не загружена → UNAVAILABLE."""


class LLMTimeout(Exception):
    """LLM не ответила за отведённое время → DEADLINE_EXCEEDED."""
