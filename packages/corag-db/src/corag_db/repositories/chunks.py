"""Чанки: замена при индексации и векторный поиск с источниками (Ф4, Ф5, А3)."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import and_, delete, insert, select

from corag_db.models import Article, Chunk, Issue, Page
from corag_db.repositories.base import BaseRepository


@dataclass(slots=True)
class ChunkIn:
    """Новый чанк страницы."""

    page_id: int
    chunk_index: int
    text: str
    embedding: Sequence[float]
    token_count: int | None = None


@dataclass(frozen=True, slots=True)
class ChunkHit:
    """Найденный фрагмент с источником: выпуск, страница и (если размечена) статья."""

    chunk_id: int
    text: str
    page_number: int
    issue_id: int
    year: int
    number: int
    article_id: int | None
    article_title: str | None
    score: float


class ChunkRepository(BaseRepository[Chunk]):
    model = Chunk

    async def replace_for_pages(self, page_ids: Sequence[int], chunks: Sequence[ChunkIn]) -> int:
        """Удалить старые чанки страниц и вставить новые (переиндексация).

        Возвращает число вставленных чанков.
        """
        await self.session.execute(delete(Chunk).where(Chunk.page_id.in_(page_ids)))
        if chunks:
            await self.session.execute(insert(Chunk), [asdict(c) for c in chunks])
        return len(chunks)

    async def nearest(
        self,
        query_vec: Sequence[float],
        top_k: int = 5,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> list[ChunkHit]:
        """top_k ближайших по косинусному расстоянию чанков (запрос из раздела 5.3 ТЗ).

        Статья находится по диапазону страниц (LEFT JOIN): неразмеченные страницы тоже ищутся.
        """
        distance = Chunk.embedding.cosine_distance(query_vec)
        stmt = (
            select(
                Chunk.id,
                Chunk.text,
                Page.page_number,
                Issue.id,
                Issue.year,
                Issue.number,
                Article.id,
                Article.title,
                (1 - distance).label("score"),
            )
            .join(Page, Page.id == Chunk.page_id)
            .join(Issue, Issue.id == Page.issue_id)
            .outerjoin(
                Article,
                and_(
                    Article.issue_id == Page.issue_id,
                    Page.page_number.between(Article.page_start, Article.page_end),
                ),
            )
            .order_by(distance)
            .limit(top_k)
        )
        if year_from is not None:
            stmt = stmt.where(Issue.year >= year_from)
        if year_to is not None:
            stmt = stmt.where(Issue.year <= year_to)
        rows = await self.session.execute(stmt)
        return [ChunkHit(*row) for row in rows]
