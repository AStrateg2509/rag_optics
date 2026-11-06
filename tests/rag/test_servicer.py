"""RagServicer в настоящем grpc.aio-сервере (на свободном порту) через настоящий RagClient.

Подменены только эмбеддер и LLM (двойники из conftest.py); БД — тестовая PostgreSQL + pgvector.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import grpc
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from corag_contracts.rag.v1 import rag_pb2_grpc
from corag_contracts.rag_client import (
    AskSources,
    RagClient,
    RagDeadlineExceeded,
    RagError,
    RagFailedPrecondition,
    RagInvalidArgument,
    RagNotFound,
    RagUnavailable,
)
from corag_db.enums import IssueStatus, UserRole
from corag_db.models import Article, Author, Chunk, Issue, Page, User
from corag_db.repositories import ArticleCreate, ArticleRepository
from corag_rag.chunker import MarkdownChunker
from corag_rag.errors import LLMTimeout, LLMUnavailable
from corag_rag.indexer import Indexer
from corag_rag.prompts import NOT_FOUND_ANSWER
from corag_rag.retriever import Retriever
from corag_rag.servicer import RagServicer
from corag_rag.settings import RagSettings

from .conftest import WhitespaceTokens

PAGES = {
    1: "Вихревые лазерные пучки формирует спиральная фазовая пластинка.\n\n"
    "Топологический заряд вихря равен числу витков фазы.",
    2: "Спиральная фазовая пластинка изготовлена методом фотолитографии.",
    3: "Хроника: конференция по голографии прошла в Самаре.",
}


@pytest.fixture
def sessions(session: AsyncSession):
    """Фабрика сессий сервиса поверх тестовой: commit/rollback — это SAVEPOINT."""

    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncSession]:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise

    return factory


@pytest.fixture
async def archive(session: AsyncSession) -> dict:
    """Выпуск 1998 №1: статья на с. 1–2 (два автора), с. 3 вне статей; выпуск без текста."""
    user = User(email="rag@test.local", password_hash="x", full_name="Т", role=UserRole.ADMIN)
    session.add(user)
    await session.flush()
    issue = Issue(year=1998, number=1, created_by=user.id, page_count=3)
    empty = Issue(year=1999, number=2, created_by=user.id, page_count=1)
    session.add_all([issue, empty])
    await session.flush()
    session.add_all([Page(issue_id=issue.id, page_number=n, text_md=t) for n, t in PAGES.items()])
    session.add(Page(issue_id=empty.id, page_number=1, text_md=None))
    first, second = Author(full_name="Сойфер В. А."), Author(full_name="Котляр В. В.")
    session.add_all([first, second])
    await session.flush()
    article = await ArticleRepository(session).create_with_relations(
        ArticleCreate(issue_id=issue.id, title="Вихревые пучки", page_start=1, page_end=2),
        [second.id, first.id],  # порядок авторов: Котляр, затем Сойфер
        [],
    )
    # Фиксируем (SAVEPOINT): rollback сервиса после ошибки не должен откатить данные фикстуры
    await session.commit()
    # Только id: после rollback в сервисе ORM-объекты фикстуры становятся expired
    return {"issue_id": issue.id, "empty_id": empty.id, "article_id": article.id}


@pytest.fixture
async def rag(sessions, embedder, llm) -> AsyncIterator[RagClient]:
    settings = RagSettings(TOP_K=3, TOP_K_MAX=10, MIN_SCORE=0.3, SNIPPET_CHARS=50)
    chunker = MarkdownChunker(WhitespaceTokens(), chunk_tokens=12, overlap=2, min_tokens=3)
    servicer = RagServicer(
        retriever=Retriever(embedder, sessions, settings.MIN_SCORE),
        indexer=Indexer(embedder, chunker, sessions, batch_size=2),
        llm=llm,
        settings=settings,
    )
    server = grpc.aio.server()
    rag_pb2_grpc.add_RagServiceServicer_to_server(servicer, server)
    port = server.add_insecure_port("127.0.0.1:0")
    await server.start()
    client = RagClient(f"127.0.0.1:{port}", ask_timeout_s=10)
    yield client
    await client.close()
    await server.stop(None)


async def chunk_count(session: AsyncSession, issue_id: int) -> int:
    stmt = select(func.count()).select_from(Chunk).join(Page).where(Page.issue_id == issue_id)
    return await session.scalar(stmt)


@pytest.fixture
async def indexed(rag, archive, session) -> dict:
    result = await rag.index_issue(archive["issue_id"])
    return {**archive, "result": result}


async def test_index_issue_is_idempotent(rag, indexed, session, embedder):
    issue_id = indexed["issue_id"]
    first = indexed["result"]
    assert first.pages_indexed == 3
    assert first.chunks_indexed == await chunk_count(session, issue_id) > 3
    again = await rag.index_issue(issue_id)
    assert again == first
    assert await chunk_count(session, issue_id) == first.chunks_indexed
    issue = await session.get(Issue, issue_id, populate_existing=True)
    assert issue.status is IssueStatus.INDEXED
    # Эмбеддинги фрагментов пачками по 2 (task retrieval.passage)
    assert {kind for kind, _ in embedder.calls} == {"passage"}
    assert max(n for _, n in embedder.calls) == 2


async def test_index_unknown_issue_and_issue_without_text(rag, archive):
    with pytest.raises(RagNotFound):
        await rag.index_issue(999_999)
    with pytest.raises(RagFailedPrecondition, match="нет текста"):
        await rag.index_issue(archive["empty_id"])
    with pytest.raises(RagInvalidArgument):
        await rag.index_issue(0)


async def test_ask_found_calls_llm_with_numbered_sources(rag, indexed, llm):
    result = await rag.ask("Что формирует спиральная фазовая пластинка вихревые пучки?")
    assert result.found is True
    assert result.answer == llm.answer
    assert len(llm.calls) == 1
    assert 0 < llm.timeouts[0] <= 10  # не дольше deadline клиента
    scores = [c.score for c in result.citations]
    assert scores == sorted(scores, reverse=True)
    best = result.citations[0]
    assert best.page_number == 1
    assert best.issue_id == indexed["issue_id"]
    assert (best.year, best.issue_number) == (1998, 1)
    assert best.article_id == indexed["article_id"]
    assert best.article_title == "Вихревые пучки"
    assert best.authors == ["Котляр В. В.", "Сойфер В. А."]
    assert len(best.snippet) <= 51
    # Порядок citations совпадает с нумерацией [1]..[k] в промпте
    prompt = llm.calls[0][1]["content"]
    assert prompt.index("[1] (статья «Вихревые пучки», Котляр В. В., Сойфер В. А., 1998") >= 0
    assert f"[{len(result.citations)}] (" in prompt


async def test_ask_outside_archive_skips_llm(rag, indexed, llm):
    result = await rag.ask("Рецепт борща со сметаной")
    assert result.found is False
    assert result.answer == NOT_FOUND_ANSWER
    assert result.citations == []
    assert llm.calls == []


async def test_ask_year_filter(rag, indexed, llm):
    result = await rag.ask("спиральная фазовая пластинка", year_from=2000)
    assert result.found is False
    result = await rag.ask("спиральная фазовая пластинка", year_from=1990, year_to=1998)
    assert result.found is True


async def test_search_returns_page_outside_article_without_source(rag, indexed, llm):
    results = await rag.search("Хроника конференция по голографии Самаре")
    assert results[0].page_number == 3
    assert results[0].article_id is None
    assert results[0].article_title is None
    assert results[0].authors == []
    assert llm.calls == []


async def test_search_dedupes_page_shared_by_two_articles(rag, indexed, session):
    # Вторая статья начинается на с. 2, где кончается первая: LEFT JOIN дублирует чанки с. 2
    session.add(Article(issue_id=indexed["issue_id"], title="Литография", page_start=2, page_end=3))
    await session.flush()
    results = await rag.search("фотолитографии изготовлена", top_k=5)
    ids = [c.chunk_id for c in results]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"question": "   "}, "пустой вопрос"),
        ({"question": "вопрос", "top_k": 11}, "top_k"),
        ({"question": "вопрос", "year_from": 2000, "year_to": 1990}, "year_from"),
    ],
)
async def test_ask_invalid_argument(rag, kwargs, message):
    with pytest.raises(RagInvalidArgument, match=message):
        await rag.ask(**kwargs)


@pytest.mark.parametrize(
    ("failure", "expected"),
    [(LLMUnavailable, RagUnavailable), (LLMTimeout, RagDeadlineExceeded)],
)
async def test_llm_errors_map_to_grpc_status(rag, indexed, llm, failure, expected):
    llm.fail = failure
    with pytest.raises(expected) as info:
        await rag.ask("спиральная фазовая пластинка")
    assert type(info.value) is expected


async def test_unexpected_error_is_internal(rag, indexed, llm):
    llm.fail = RuntimeError
    with pytest.raises(RagError) as info:
        await rag.ask("спиральная фазовая пластинка")
    assert info.value.code is grpc.StatusCode.INTERNAL
    assert "внутренняя ошибка" in info.value.details


async def collect(stream) -> tuple[list, str]:
    """Поток AskStream → (все сообщения по порядку, склеенный текст ответа)."""
    events = [event async for event in stream]
    return events, "".join(e for e in events if isinstance(e, str))


async def test_ask_stream_sends_sources_first_then_answer_parts(rag, indexed, llm):
    events, text = await collect(rag.ask_stream("спиральная фазовая пластинка вихревые пучки"))
    sources = events[0]
    assert isinstance(sources, AskSources)
    assert sources.found is True
    assert sources.citations[0].authors == ["Котляр В. В.", "Сойфер В. А."]
    scores = [c.score for c in sources.citations]
    assert scores == sorted(scores, reverse=True)
    # Источники — только первым сообщением, дальше — части текста
    assert all(isinstance(e, str) for e in events[1:])
    assert len(events) - 1 == len(llm.answer.split(" ")) > 1
    assert text == llm.answer
    assert len(llm.calls) == 1
    assert 0 < llm.timeouts[0] <= 10


async def test_ask_stream_outside_archive_skips_llm(rag, indexed, llm):
    events, text = await collect(rag.ask_stream("Рецепт борща со сметаной"))
    assert events[0] == AskSources(found=False, citations=[])
    assert text == NOT_FOUND_ANSWER
    assert llm.calls == []


async def test_ask_stream_invalid_argument(rag):
    with pytest.raises(RagInvalidArgument, match="пустой вопрос"):
        await collect(rag.ask_stream("  "))


async def test_ask_stream_llm_error_after_sources(rag, indexed, llm):
    llm.fail = LLMUnavailable
    received = []
    with pytest.raises(RagUnavailable):
        async for event in rag.ask_stream("спиральная фазовая пластинка"):
            received.append(event)
    # Источники клиент уже получил; статус ошибки пришёл в trailers вместо OK
    assert len(received) == 1
    assert isinstance(received[0], AskSources)


async def test_rag_service_down_is_unavailable():
    async with RagClient("127.0.0.1:1", search_timeout_s=2) as client:
        with pytest.raises(RagUnavailable):
            await client.search("вопрос")
