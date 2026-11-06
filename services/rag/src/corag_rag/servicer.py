"""RagServicer — реализация gRPC-сервиса corag.rag.v1.RagService.

Ask, Search, IndexIssue — unary; AskStream — server streaming (ответ LLM частями).
"""

import functools
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import NoReturn

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
from corag_rag.retriever import Hit, Retriever, SearchResult
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

type Context = grpc.aio.ServicerContext
type Handler[Req, Resp] = Callable[["RagServicer", Req, Context], Awaitable[Resp]]
type StreamHandler[Req, Resp] = Callable[["RagServicer", Req, Context], AsyncIterator[Resp]]


async def _abort(context: Context, method: str, exc: Exception) -> NoReturn:
    """Исключение → статус gRPC; context.abort бросает исключение и завершает вызов."""
    code = next((c for cls, c in STATUS_CODES.items() if isinstance(exc, cls)), None)
    if code is None:
        log.error("[rag] %s → INTERNAL", method, exc_info=exc)
        await context.abort(grpc.StatusCode.INTERNAL, "внутренняя ошибка RAG Service")
    else:
        log.info("[rag] %s → %s: %s", method, code.name, exc)
        await context.abort(code, str(exc))
    raise AssertionError("context.abort всегда бросает исключение")  # pragma: no cover


def rpc[Req, Resp](method: Handler[Req, Resp]) -> Handler[Req, Resp]:
    """Unary-метод: перевод исключений в статус gRPC (context.abort) — в одном месте."""

    @functools.wraps(method)
    async def wrapper(self: "RagServicer", request: Req, context: Context) -> Resp:
        try:
            return await method(self, request, context)
        except Exception as exc:
            await _abort(context, method.__name__, exc)

    return wrapper


def rpc_stream[Req, Resp](method: StreamHandler[Req, Resp]) -> StreamHandler[Req, Resp]:
    """Server streaming: то же для async-генератора. Ошибка посреди потока — клиент уже
    получил часть сообщений, а статус ошибки придёт в trailers вместо OK."""

    @functools.wraps(method)
    async def wrapper(self: "RagServicer", request: Req, context: Context) -> AsyncIterator[Resp]:
        try:
            async for response in method(self, request, context):
                yield response
        except Exception as exc:
            await _abort(context, method.__name__, exc)

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
    async def Ask(self, request: rag_pb2.AskRequest, context: Context) -> rag_pb2.AskResponse:
        question, result = await self._retrieve("Ask", request)
        if not result.hits:
            return rag_pb2.AskResponse(answer=NOT_FOUND_ANSWER, found=False)

        answer = await self.llm.chat(
            build_messages(question, result.hits), timeout_s=self._llm_timeout(context)
        )
        citations = [self._citation(hit) for hit in result.hits]
        log.info("[rag] → AskResponse %d citations", len(citations))
        return rag_pb2.AskResponse(answer=answer, found=True, citations=citations)

    @rpc_stream
    async def AskStream(
        self, request: rag_pb2.AskRequest, context: Context
    ) -> AsyncIterator[rag_pb2.AskStreamResponse]:
        """Server streaming: источники — сразу после поиска, текст — по мере генерации LLM."""
        question, result = await self._retrieve("AskStream", request)
        if not result.hits:
            yield rag_pb2.AskStreamResponse(sources=rag_pb2.AskSources(found=False))
            yield rag_pb2.AskStreamResponse(delta=NOT_FOUND_ANSWER)
            return

        citations = [self._citation(hit) for hit in result.hits]
        yield rag_pb2.AskStreamResponse(sources=rag_pb2.AskSources(found=True, citations=citations))
        log.info("[rag] → AskStream: отправлены источники (%d)", len(citations))
        parts = 0
        stream = self.llm.chat_stream(
            build_messages(question, result.hits), timeout_s=self._llm_timeout(context)
        )
        async for delta in stream:
            parts += 1
            yield rag_pb2.AskStreamResponse(delta=delta)
        log.info("[rag] → AskStream: поток закрыт, %d частей ответа", parts)

    @rpc
    async def Search(
        self, request: rag_pb2.SearchRequest, context: Context
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
        self, request: rag_pb2.IndexIssueRequest, context: Context
    ) -> rag_pb2.IndexIssueResponse:
        log.info("[rag] ← IndexIssue issue=%d", request.issue_id)
        if request.issue_id <= 0:
            raise InvalidArgument("issue_id должен быть положительным")
        result = await self.indexer.index_issue(request.issue_id)
        return rag_pb2.IndexIssueResponse(pages_indexed=result.pages, chunks_indexed=result.chunks)

    async def _retrieve(self, method: str, request: rag_pb2.AskRequest) -> tuple[str, SearchResult]:
        """Общая часть Ask и AskStream: проверка запроса и поиск фрагментов."""
        question = request.question.strip()
        top_k, year_from, year_to = self._validate(question, request.top_k, request.filters)
        log.info(
            '[rag] ← %s "%s" top_k=%d%s',
            method,
            _short(question),
            top_k,
            _years(year_from, year_to),
        )
        result = await self.retriever.search(question, top_k, year_from, year_to)
        if not result.hits:
            log.info(
                "[rag] нет фрагментов со сходством ≥ MIN_SCORE=%.2f (best %s) "
                "→ found=false, LLM не вызывается",
                self.settings.MIN_SCORE,
                _score(result.best_score),
            )
        else:
            log.info("[rag] retrieved %d chunks (best %.2f)", len(result.hits), result.best_score)
            log.info("[rag] → LLM %s: %d фрагментов в контексте", self.llm.model, len(result.hits))
        return question, result

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

    def _llm_timeout(self, context: Context) -> float:
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
