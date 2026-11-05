"""Асинхронный клиент RAG Service (gRPC): API Service (ЛР3) и OCR Worker (ЛР4).

- один канал grpc.aio на процесс: создаётся при старте, закрывается при остановке;
- deadline на каждый вызов: Ask 120 с, Search 15 с, IndexIssue 30 мин;
- ответы protobuf переводятся в модели Pydantic, ошибки gRPC — в исключения RagError.
"""

from types import TracebackType

import grpc
from pydantic import BaseModel

from corag_contracts.rag.v1 import rag_pb2, rag_pb2_grpc

ASK_TIMEOUT_S = 120.0
SEARCH_TIMEOUT_S = 15.0
INDEX_TIMEOUT_S = 30 * 60.0

# Короткий backoff переподключения: после перезапуска RAG Service канал быстро оживает
# (по умолчанию пауза между попытками растёт до 120 с)
CHANNEL_OPTIONS = [
    ("grpc.initial_reconnect_backoff_ms", 500),
    ("grpc.min_reconnect_backoff_ms", 500),
    ("grpc.max_reconnect_backoff_ms", 5000),
]


class Citation(BaseModel):
    """Источник ответа: фрагмент, страница, выпуск и (если размечена) статья."""

    chunk_id: int
    article_id: int | None
    article_title: str | None
    authors: list[str]
    issue_id: int
    year: int
    issue_number: int
    page_number: int
    snippet: str
    score: float


class AskResult(BaseModel):
    answer: str
    found: bool
    citations: list[Citation]


class IndexResult(BaseModel):
    issue_id: int
    pages_indexed: int
    chunks_indexed: int


class RagError(Exception):
    """Ошибка вызова RAG Service: статус gRPC и сообщение сервера."""

    def __init__(self, code: grpc.StatusCode, details: str) -> None:
        super().__init__(details)
        self.code = code
        self.details = details


class RagInvalidArgument(RagError):
    """INVALID_ARGUMENT — некорректный запрос (HTTP 422)."""


class RagNotFound(RagError):
    """NOT_FOUND — выпуск не найден (HTTP 404)."""


class RagFailedPrecondition(RagError):
    """FAILED_PRECONDITION — у выпуска нет текста страниц (HTTP 409)."""


class RagUnavailable(RagError):
    """UNAVAILABLE — RAG Service или LLM недоступны (HTTP 503)."""


class RagDeadlineExceeded(RagError):
    """DEADLINE_EXCEEDED — ответ не получен до deadline (HTTP 504)."""


ERRORS: dict[grpc.StatusCode, type[RagError]] = {
    grpc.StatusCode.INVALID_ARGUMENT: RagInvalidArgument,
    grpc.StatusCode.NOT_FOUND: RagNotFound,
    grpc.StatusCode.FAILED_PRECONDITION: RagFailedPrecondition,
    grpc.StatusCode.UNAVAILABLE: RagUnavailable,
    grpc.StatusCode.DEADLINE_EXCEEDED: RagDeadlineExceeded,
}


def map_rpc_error(exc: grpc.aio.AioRpcError) -> RagError:
    """Статус gRPC → исключение; прочие коды (INTERNAL, UNKNOWN…) — базовый RagError."""
    code = exc.code()
    return ERRORS.get(code, RagError)(code, exc.details() or code.name)


def _filters(year_from: int | None, year_to: int | None) -> rag_pb2.Filters:
    fields = {"year_from": year_from, "year_to": year_to}
    return rag_pb2.Filters(**{k: v for k, v in fields.items() if v is not None})


def _citation(c: rag_pb2.Citation) -> Citation:
    return Citation(
        chunk_id=c.chunk_id,
        article_id=c.article_id if c.HasField("article_id") else None,
        article_title=c.article_title or None,
        authors=list(c.authors),
        issue_id=c.issue_id,
        year=c.year,
        issue_number=c.issue_number,
        page_number=c.page_number,
        snippet=c.snippet,
        score=round(c.score, 4),
    )


class RagClient:
    def __init__(
        self,
        address: str,
        *,
        ask_timeout_s: float = ASK_TIMEOUT_S,
        search_timeout_s: float = SEARCH_TIMEOUT_S,
        index_timeout_s: float = INDEX_TIMEOUT_S,
    ) -> None:
        self.address = address
        self.ask_timeout_s = ask_timeout_s
        self.search_timeout_s = search_timeout_s
        self.index_timeout_s = index_timeout_s
        # Канал сам держит HTTP/2-соединение и переподключается; создавать его на запрос нельзя
        self._channel = grpc.aio.insecure_channel(address, options=CHANNEL_OPTIONS)
        self._stub = rag_pb2_grpc.RagServiceStub(self._channel)

    async def ask(
        self,
        question: str,
        *,
        top_k: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> AskResult:
        request = rag_pb2.AskRequest(
            question=question, top_k=top_k or 0, filters=_filters(year_from, year_to)
        )
        try:
            resp = await self._stub.Ask(request, timeout=self.ask_timeout_s)
        except grpc.aio.AioRpcError as exc:
            raise map_rpc_error(exc) from exc
        return AskResult(
            answer=resp.answer, found=resp.found, citations=[_citation(c) for c in resp.citations]
        )

    async def search(
        self,
        query: str,
        *,
        top_k: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[Citation]:
        request = rag_pb2.SearchRequest(
            query=query, top_k=top_k or 0, filters=_filters(year_from, year_to)
        )
        try:
            resp = await self._stub.Search(request, timeout=self.search_timeout_s)
        except grpc.aio.AioRpcError as exc:
            raise map_rpc_error(exc) from exc
        return [_citation(c) for c in resp.results]

    async def index_issue(self, issue_id: int) -> IndexResult:
        request = rag_pb2.IndexIssueRequest(issue_id=issue_id)
        try:
            resp = await self._stub.IndexIssue(request, timeout=self.index_timeout_s)
        except grpc.aio.AioRpcError as exc:
            raise map_rpc_error(exc) from exc
        return IndexResult(
            issue_id=issue_id,
            pages_indexed=resp.pages_indexed,
            chunks_indexed=resp.chunks_indexed,
        )

    async def close(self) -> None:
        await self._channel.close()

    async def __aenter__(self) -> "RagClient":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()
