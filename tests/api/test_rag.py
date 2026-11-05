"""/rag/ask, /rag/search и /issues/{id}/reindex: вызов RagClient и перевод статусов gRPC в HTTP."""

import grpc
import pytest

from corag_contracts.rag_client import (
    AskResult,
    RagDeadlineExceeded,
    RagError,
    RagFailedPrecondition,
    RagInvalidArgument,
    RagNotFound,
    RagUnavailable,
)

from .conftest import CITATION


async def test_ask_returns_answer_with_citations(client, researcher, rag):
    body = {"question": "  Как ДОЭ фокусирует в кольцо?  ", "top_k": 3, "year_from": 1990}
    resp = await client.post("/rag/ask", json=body, headers=researcher.headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["found"] is True
    assert data["answer"] == "ДОЭ фокусирует свет в кольцо [1]."
    assert data["citations"] == [CITATION.model_dump()]
    # Вопрос обрезан по краям, фильтры переданы как есть
    assert rag.calls == [("ask", "Как ДОЭ фокусирует в кольцо?", 3, 1990, None)]


async def test_ask_not_found(client, researcher, rag):
    rag.ask_result = AskResult(answer="В архиве не найдено…", found=False, citations=[])
    resp = await client.post("/rag/ask", json={"question": "борщ"}, headers=researcher.headers)
    assert resp.status_code == 200
    assert resp.json() == {"answer": "В архиве не найдено…", "found": False, "citations": []}


async def test_ask_requires_token(client, rag):
    resp = await client.post("/rag/ask", json={"question": "вопрос"})
    assert resp.status_code == 401
    assert rag.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"question": "   "},
        {"question": "x" * 2001},
        {"question": "вопрос", "top_k": 0},
        {"question": "вопрос", "top_k": 21},
        {"question": "вопрос", "year_from": 2000, "year_to": 1990},
        {"question": "вопрос", "year_from": 1900},
    ],
)
async def test_ask_validation_422_without_grpc_call(client, researcher, rag, body):
    resp = await client.post("/rag/ask", json=body, headers=researcher.headers)
    assert resp.status_code == 422
    assert rag.calls == []


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (RagInvalidArgument(grpc.StatusCode.INVALID_ARGUMENT, "пустой вопрос"), 422),
        (RagUnavailable(grpc.StatusCode.UNAVAILABLE, "failed to connect"), 503),
        (RagDeadlineExceeded(grpc.StatusCode.DEADLINE_EXCEEDED, "Deadline Exceeded"), 504),
        (RagError(grpc.StatusCode.INTERNAL, "внутренняя ошибка RAG Service"), 502),
    ],
)
async def test_grpc_status_to_http(client, researcher, rag, error, status):
    rag.error = error
    resp = await client.post("/rag/ask", json={"question": "вопрос"}, headers=researcher.headers)
    assert resp.status_code == status
    assert error.details in resp.json()["detail"]


async def test_search_returns_fragments(client, researcher, rag):
    resp = await client.post(
        "/rag/search", json={"query": "кольцо", "year_to": 2000}, headers=researcher.headers
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"results": [CITATION.model_dump()]}
    assert rag.calls == [("search", "кольцо", None, None, 2000)]


async def test_search_rag_down_503(client, researcher, rag):
    rag.error = RagUnavailable(grpc.StatusCode.UNAVAILABLE, "failed to connect to all addresses")
    resp = await client.post("/rag/search", json={"query": "кольцо"}, headers=researcher.headers)
    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("RAG Service или LLM недоступны")


async def test_reindex_admin_only(client, admin, researcher, rag):
    resp = await client.post("/issues/5/reindex", headers=researcher.headers)
    assert resp.status_code == 403
    assert rag.calls == []
    resp = await client.post("/issues/5/reindex", headers=admin.headers)
    assert resp.status_code == 200
    assert resp.json() == {"issue_id": 5, "pages_indexed": 7, "chunks_indexed": 9}
    assert rag.calls == [("index_issue", 5)]


@pytest.mark.parametrize(
    ("error", "status", "detail"),
    [
        (RagNotFound(grpc.StatusCode.NOT_FOUND, "выпуск 5 не найден"), 404, "выпуск 5 не найден"),
        (
            RagFailedPrecondition(grpc.StatusCode.FAILED_PRECONDITION, "у выпуска 5 нет текста"),
            409,
            "у выпуска 5 нет текста",
        ),
    ],
)
async def test_reindex_errors(client, admin, rag, error, status, detail):
    rag.error = error
    resp = await client.post("/issues/5/reindex", headers=admin.headers)
    assert resp.status_code == status
    assert resp.json() == {"detail": detail}
