"""Индексатор (IndexIssue): страницы выпуска → чанки → эмбеддинги → таблица chunks."""

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass

from corag_db.enums import IssueStatus
from corag_db.exceptions import NotFound
from corag_db.repositories import ChunkIn, ChunkRepository, IssueRepository, PageRepository
from corag_rag.chunker import ChunkText, MarkdownChunker
from corag_rag.db import SessionFactory
from corag_rag.embedder import Embedder
from corag_rag.errors import FailedPrecondition

log = logging.getLogger("corag_rag")


@dataclass(frozen=True, slots=True)
class IndexResult:
    issue_id: int
    pages: int
    chunks: int
    seconds: float


class Indexer:
    def __init__(
        self,
        embedder: Embedder,
        chunker: MarkdownChunker,
        sessions: SessionFactory,
        batch_size: int = 32,
    ) -> None:
        self.embedder = embedder
        self.chunker = chunker
        self.sessions = sessions
        self.batch_size = batch_size
        # Один выпуск не индексируется параллельно дважды (reindex админа и OCR Worker в ЛР4)
        self._locks: defaultdict[int, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def index_issue(self, issue_id: int) -> IndexResult:
        async with self._locks[issue_id]:
            return await self._index(issue_id)

    async def _index(self, issue_id: int) -> IndexResult:
        started = time.perf_counter()
        # 1. Короткая транзакция: прочитать страницы
        async with self.sessions() as session:
            if await IssueRepository(session).get(issue_id) is None:
                raise NotFound("выпуск", issue_id)
            pages = [
                (p.id, p.text_md) for p in await PageRepository(session).list_by_issue(issue_id)
            ]
        with_text = [(page_id, text) for page_id, text in pages if text and text.strip()]
        if not with_text:
            raise FailedPrecondition(f"у выпуска {issue_id} нет текста страниц (нужен OCR)")

        # 2. Чанкинг и эмбеддинги — вне транзакции: на CPU это минуты
        drafts = await asyncio.to_thread(self._chunk_pages, with_text)
        vectors: list[list[float]] = []
        batches = (len(drafts) + self.batch_size - 1) // self.batch_size
        for n, start in enumerate(range(0, len(drafts), self.batch_size), start=1):
            batch = [chunk.text for _, _, chunk in drafts[start : start + self.batch_size]]
            vectors.extend(await self.embedder.embed_passages(batch))
            log.info(
                "[rag] IndexIssue issue=%d: эмбеддинги %d/%d (%d чанков, %.1f s)",
                issue_id,
                n,
                batches,
                len(vectors),
                time.perf_counter() - started,
            )
        chunks = [
            ChunkIn(page_id, index, chunk.text, vec, chunk.token_count)
            for (page_id, index, chunk), vec in zip(drafts, vectors, strict=True)
        ]

        # 3. Одна транзакция: старые чанки всех страниц выпуска → новые, статус indexed
        async with self.sessions() as session:
            issues = IssueRepository(session)
            issue = await issues.get(issue_id)
            if issue is None:
                raise NotFound("выпуск", issue_id)
            await ChunkRepository(session).replace_for_pages([p for p, _ in pages], chunks)
            await issues.set_status(issue, IssueStatus.INDEXED)

        result = IndexResult(issue_id, len(with_text), len(chunks), time.perf_counter() - started)
        log.info(
            "[rag] IndexIssue issue=%d: %d pages → %d chunks (%.1f s)",
            issue_id,
            result.pages,
            result.chunks,
            result.seconds,
        )
        return result

    def _chunk_pages(self, pages: list[tuple[int, str]]) -> list[tuple[int, int, ChunkText]]:
        """(page_id, chunk_index, чанк); нумерация чанков — внутри страницы."""
        return [
            (page_id, index, chunk)
            for page_id, text in pages
            for index, chunk in enumerate(self.chunker.split(text))
        ]
