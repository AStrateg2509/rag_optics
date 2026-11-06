"""Вызовы RAG Service по gRPC: Ask, Search, IndexIssue и перевод статусов gRPC в ошибки API."""

import logging
import time
from collections.abc import Awaitable

from corag_api.errors import BadGateway, ServiceUnavailable, UpstreamTimeout, ValidationFailed
from corag_api.schemas.rag import AskIn, SearchIn
from corag_contracts.rag_client import (
    AskResult,
    Citation,
    IndexResult,
    RagClient,
    RagDeadlineExceeded,
    RagError,
    RagFailedPrecondition,
    RagInvalidArgument,
    RagNotFound,
    RagUnavailable,
)
from corag_db.exceptions import Conflict, NotFound

log = logging.getLogger("corag_api")


class RagService:
    def __init__(self, client: RagClient) -> None:
        self.client = client

    async def ask(self, data: AskIn) -> AskResult:
        log.info(
            '[api] → gRPC Ask question="%s" top_k=%s%s',
            _short(data.question),
            data.top_k or "по умолчанию",
            _years(data.year_from, data.year_to),
        )
        started = time.perf_counter()
        result = await self._call(
            "Ask",
            self.client.ask(
                data.question, top_k=data.top_k, year_from=data.year_from, year_to=data.year_to
            ),
        )
        log.info(
            "[api] ← gRPC Ask %.1f s, found=%s, %d citations",
            time.perf_counter() - started,
            str(result.found).lower(),
            len(result.citations),
        )
        return result

    async def search(self, data: SearchIn) -> list[Citation]:
        log.info(
            '[api] → gRPC Search query="%s" top_k=%s%s',
            _short(data.query),
            data.top_k or "по умолчанию",
            _years(data.year_from, data.year_to),
        )
        started = time.perf_counter()
        results = await self._call(
            "Search",
            self.client.search(
                data.query, top_k=data.top_k, year_from=data.year_from, year_to=data.year_to
            ),
        )
        log.info(
            "[api] ← gRPC Search %.1f s, %d results", time.perf_counter() - started, len(results)
        )
        return results

    async def reindex(self, issue_id: int) -> IndexResult:
        log.info("[api] → gRPC IndexIssue issue_id=%d", issue_id)
        started = time.perf_counter()
        result = await self._call("IndexIssue", self.client.index_issue(issue_id), issue_id)
        log.info(
            "[api] ← gRPC IndexIssue %.1f s: %d pages → %d chunks",
            time.perf_counter() - started,
            result.pages_indexed,
            result.chunks_indexed,
        )
        return result

    async def _call[T](self, method: str, call: Awaitable[T], issue_id: int | None = None) -> T:
        """Статус gRPC → доменное исключение API → HTTP (422/404/409/503/504/502)."""
        try:
            return await call
        except RagError as exc:
            log.warning("[api] ← gRPC %s %s: %s", method, exc.code.name, exc.details)
            raise _translate(exc, issue_id) from exc


def _translate(exc: RagError, issue_id: int | None) -> Exception:
    match exc:
        case RagInvalidArgument():
            return ValidationFailed(exc.details)
        case RagNotFound() if issue_id is not None:
            return NotFound("выпуск", issue_id)
        case RagFailedPrecondition():
            return Conflict(exc.details)
        case RagUnavailable():
            return ServiceUnavailable(f"RAG Service или LLM недоступны: {exc.details}")
        case RagDeadlineExceeded():
            return UpstreamTimeout(f"RAG Service не ответил до deadline: {exc.details}")
        case _:
            return BadGateway(f"ошибка RAG Service ({exc.code.name}): {exc.details}")


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _years(year_from: int | None, year_to: int | None) -> str:
    if year_from is None and year_to is None:
        return ""
    return f" years={year_from or '…'}–{year_to or '…'}"
