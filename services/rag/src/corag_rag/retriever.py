"""Ретривер: эмбеддинг вопроса → top-k ближайших чанков (pgvector) → отсев по MIN_SCORE."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from corag_db.repositories import ArticleRepository, ChunkHit, ChunkRepository
from corag_rag.db import SessionFactory
from corag_rag.embedder import Embedder

log = logging.getLogger("corag_rag")


@dataclass(frozen=True, slots=True)
class Hit:
    """Найденный фрагмент и авторы его статьи (пусто, если страница вне размеченных статей)."""

    chunk: ChunkHit
    authors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SearchResult:
    hits: list[Hit]
    # Лучшее сходство до отсева по порогу — для логов («best 0.21 → found=false»)
    best_score: float | None


class Retriever:
    def __init__(self, embedder: Embedder, sessions: SessionFactory, min_score: float) -> None:
        self.embedder = embedder
        self.sessions = sessions
        self.min_score = min_score

    async def search(
        self,
        query: str,
        top_k: int,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> SearchResult:
        [query_vec] = await self.embedder.embed_queries([query])
        async with self.sessions() as session:
            # Запас ×2: LEFT JOIN по диапазону страниц дублирует чанк, если страница
            # принадлежит двум статьям (одна кончается, другая начинается)
            rows = await ChunkRepository(session).nearest(
                query_vec, top_k * 2, year_from=year_from, year_to=year_to
            )
            unique = _dedupe(rows)[:top_k]
            kept = [row for row in unique if row.score >= self.min_score]
            authors = await ArticleRepository(session).author_names(
                [row.article_id for row in kept if row.article_id is not None]
            )
        hits = [Hit(row, tuple(authors.get(row.article_id or 0, ()))) for row in kept]
        return SearchResult(hits=hits, best_score=unique[0].score if unique else None)


def _dedupe(rows: Sequence[ChunkHit]) -> list[ChunkHit]:
    seen: set[int] = set()
    result = []
    for row in rows:
        if row.chunk_id not in seen:
            seen.add(row.chunk_id)
            result.append(row)
    return result
