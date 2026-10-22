"""Выпуски: каталог по году, статус обработки, счётчики (Ф2, А1)."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select

from corag_db.enums import IssueStatus
from corag_db.exceptions import NotFound
from corag_db.models import Article, Chunk, Issue, Page
from corag_db.repositories.base import BaseRepository


@dataclass(frozen=True, slots=True)
class IssueWithCounts:
    issue: Issue
    pages: int
    articles: int
    chunks: int


class IssueRepository(BaseRepository[Issue]):
    model = Issue

    async def list(
        self, limit: int = 50, offset: int = 0, *, year: int | None = None
    ) -> Sequence[Issue]:
        stmt = select(Issue).order_by(Issue.year.desc(), Issue.number).limit(limit).offset(offset)
        if year is not None:
            stmt = stmt.where(Issue.year == year)
        return (await self.session.scalars(stmt)).all()

    async def get_by_year_number(self, year: int, number: int) -> Issue | None:
        stmt = select(Issue).where(Issue.year == year, Issue.number == number)
        return await self.session.scalar(stmt)

    async def set_status(self, issue: Issue, status: IssueStatus) -> Issue:
        return await self.update(issue, status=status)

    async def get_with_counts(self, issue_id: int) -> IssueWithCounts:
        """Выпуск и количество страниц, статей и чанков — одним запросом."""
        pages = select(func.count(Page.id)).where(Page.issue_id == Issue.id).scalar_subquery()
        articles = (
            select(func.count(Article.id)).where(Article.issue_id == Issue.id).scalar_subquery()
        )
        chunks = (
            select(func.count(Chunk.id))
            .join(Page, Page.id == Chunk.page_id)
            .where(Page.issue_id == Issue.id)
            .scalar_subquery()
        )
        stmt = select(Issue, pages, articles, chunks).where(Issue.id == issue_id)
        row = (await self.session.execute(stmt)).one_or_none()
        if row is None:
            raise NotFound("Issue", issue_id)
        return IssueWithCounts(*row)
