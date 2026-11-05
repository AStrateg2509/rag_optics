"""RagServicer — реализация gRPC-сервиса corag.rag.v1.RagService (Ask, Search, IndexIssue)."""

import functools
import logging
from collections.abc import Awaitable, Callable

import grpc

from corag_contracts.rag.v1 import rag_pb2, rag_pb2_grpc
from corag_rag.errors import (
    FailedPrecondition,
    InvalidArgument,
    LLMTimeout,
    LLMUnavailable,
    NotFound,
)
from corag_rag.indexer import Indexer
from corag_rag.llm import LLMClient
from corag_rag.prompts import NOT_FOUND_ANSWER, build_messages
from corag_rag.retriever import Hit, Retriever
from corag_rag.settings import RagSettings

log = logging.getLogger("corag_rag")

# Доменное исключение → статус gRPC (клиент переводит статус в HTTP: 422/404/409/503/504)
STATUS_CODES: dict[type[Exception], grpc.StatusCode] = {
    InvalidArgument: grpc.StatusCode.INVALID_ARGUMENT,
    NotFound: grpc.StatusCode.NOT_FOUND,
    FailedPrecondition: grpc.StatusCode.FAILED_PRECONDITION,
    LLMUnavailable: grpc.StatusCode.UNAVAILABLE,
    LLMTimeout: grpc.StatusCode.DEADLINE_EXCEEDED,
}

type Handler[Req, Resp] = Callable[["RagServicer", Req, grpc.aio.ServicerContext], Awaitable[Resp]]


def rpc[Req, Resp](method: Handler[Req, Resp]) -> Handler[Req, Resp]:
    """Перевод исключений метода в статус gRPC (context.abort) — в одном месте."""

    @functools.wraps(method)
    async def wrapper(self: "RagServicer", request: Req, context: grpc.aio.ServicerContext) -> Resp:
        try:
            return await method(self, request, context)
        except tuple(STATUS_CODES) as exc:
            code = next(c for cls, c in STATUS_CODES.items() if isinstance(exc, cls))
            log.info("[rag] %s → %s: %s", method.__name__, code.name, exc)
            await context.abort(code, str(exc))
        except Exception:
            log.exception("[rag] %s → INTERNAL", method.__name__)
            await context.abort(grpc.StatusCode.INTERNAL, "внутренняя ошибка RAG Service")
        raise AssertionError("context.abort всегда бросает исключение")  # pragma: no cover

    return wrapper


class RagServicer(rag_pb2_grpc.RagServiceServicer):
    def __init__(
        self, retriever: Retriever, indexer: Indexer, llm: LLMClient, settings: RagSettings
    ) -> None:
        self.retriever = retriever
        self.indexer = indexer
        self.llm = llm
        self.settings = settings

    @rpc
    async def Ask(
        self, request: rag_pb2.AskRequest, context: grpc.aio.ServicerContext
    ) -> rag_pb2.AskResponse:
        question = request.question.strip()
        top_k, year_from, year_to = self._validate(question, request.top_k, request.filters)
        log.info('[rag] ← Ask "%s" top_k=%d%s', _short(question), top_k, _years(year_from, year_to))
        result = await self.retriever.search(question, top_k, year_from, year_to)
        if not result.hits:
            log.info(
                "[rag] нет фрагментов со сходством ≥ MIN_SCORE=%.2f (best %s) "
                "→ found=false, LLM не вызывается",
                self.settings.MIN_SCORE,
                _score(result.best_score),
            )
            return rag_pb2.AskResponse(answer=NOT_FOUND_ANSWER, found=False)

        log.info("[rag] retrieved %d chunks (best %.2f)", len(result.hits), result.best_score)
        log.info("[rag] → LLM %s: %d фрагментов в контексте", self.llm.model, len(result.hits))
        answer = await self.llm.chat(
            build_messages(question, result.hits), timeout_s=self._llm_timeout(context)
        )
        citations = [self._citation(hit) for hit in result.hits]
        log.info("[rag] → AskResponse %d citations", len(citations))
        return rag_pb2.AskResponse(answer=answer, found=True, citations=citations)

    @rpc
    async def Search(
        self, request: rag_pb2.SearchRequest, context: grpc.aio.ServicerContext
    ) -> rag_pb2.SearchResponse:
        query = request.query.strip()
        top_k, year_from, year_to = self._validate(query, request.top_k, request.filters)
        log.info('[rag] ← Search "%s" top_k=%d%s', _short(query), top_k, _years(year_from, year_to))
        result = await self.retriever.search(query, top_k, year_from, year_to)
        log.info(
            "[rag] → SearchResponse %d results (best %s, MIN_SCORE=%.2f)",
            len(result.hits),
            _score(result.best_score),
            self.settings.MIN_SCORE,
        )
        return rag_pb2.SearchResponse(results=[self._citation(hit) for hit in result.hits])

    @rpc
    async def IndexIssue(
        self, request: rag_pb2.IndexIssueRequest, context: grpc.aio.ServicerContext
    ) -> rag_pb2.IndexIssueResponse:
        log.info("[rag] ← IndexIssue issue=%d", request.issue_id)
        if request.issue_id <= 0:
            raise InvalidArgument("issue_id должен быть положительным")
        result = await self.indexer.index_issue(request.issue_id)
        return rag_pb2.IndexIssueResponse(pages_indexed=result.pages, chunks_indexed=result.chunks)

    def _validate(
        self, text: str, top_k: int, filters: rag_pb2.Filters
    ) -> tuple[int, int | None, int | None]:
        """Проверка запроса Ask/Search; top_k=0 (не задан) → TOP_K из настроек."""
        if not text:
            raise InvalidArgument("пустой вопрос")
        if len(text) > self.settings.QUESTION_MAX_CHARS:
            raise InvalidArgument(f"вопрос длиннее {self.settings.QUESTION_MAX_CHARS} символов")
        if not 0 <= top_k <= self.settings.TOP_K_MAX:
            raise InvalidArgument(f"top_k должен быть от 1 до {self.settings.TOP_K_MAX}")
        year_from = filters.year_from if filters.HasField("year_from") else None
        year_to = filters.year_to if filters.HasField("year_to") else None
        if year_from is not None and year_to is not None and year_from > year_to:
            raise InvalidArgument("year_from больше year_to")
        return top_k or self.settings.TOP_K, year_from, year_to

    def _llm_timeout(self, context: grpc.aio.ServicerContext) -> float:
        """Не ждать LLM дольше, чем осталось до deadline клиента."""
        remaining = context.time_remaining()
        timeout = self.settings.LLM_TIMEOUT_S
        if remaining is not None:
            timeout = min(timeout, remaining - 0.5)
        if timeout <= 0:
            raise LLMTimeout("до deadline клиента не осталось времени на вызов LLM")
        return timeout

    def _citation(self, hit: Hit) -> rag_pb2.Citation:
        chunk = hit.chunk
        citation = rag_pb2.Citation(
            chunk_id=chunk.chunk_id,
            article_title=chunk.article_title or "",
            year=chunk.year,
            issue_number=chunk.number,
            page_number=chunk.page_number,
            snippet=snippet(chunk.text, self.settings.SNIPPET_CHARS),
            score=chunk.score,
            issue_id=chunk.issue_id,
            authors=hit.authors,
        )
        if chunk.article_id is not None:
            citation.article_id = chunk.article_id
        return citation


def snippet(text: str, limit: int) -> str:
    """Фрагмент для источника: пробелы схлопнуты, обрезка по границе слова с «…»."""
    flat = " ".join(text.split())
    if len(flat) <= limit:
        return flat
    return flat[:limit].rsplit(" ", 1)[0] + "…"


def _short(text: str, limit: int = 80) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _years(year_from: int | None, year_to: int | None) -> str:
    if year_from is None and year_to is None:
        return ""
    return f" years={year_from or '…'}–{year_to or '…'}"


def _score(score: float | None) -> str:
    return "—" if score is None else f"{score:.2f}"
