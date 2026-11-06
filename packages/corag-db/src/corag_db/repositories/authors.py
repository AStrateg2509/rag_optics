"""Авторы: поиск по ФИО и выборка по списку id (А5)."""

from collections.abc import Sequence

from sqlalchemy import ColumnElement, exists, func, select

from corag_db.exceptions import NotFound
from corag_db.models import ArticleAuthor, Author
from corag_db.repositories.base import BaseRepository


class AuthorRepository(BaseRepository[Author]):
    model = Author

    @staticmethod
    def _search_conditions(q: str | None) -> list[ColumnElement[bool]]:
        return [Author.full_name.ilike(f"%{q.strip()}%")] if q and q.strip() else []

    async def search(self, q: str | None, limit: int = 20, offset: int = 0) -> Sequence[Author]:
        """Поиск по подстроке ФИО без учёта регистра (ILIKE); пустой q — все авторы."""
        stmt = (
            select(Author)
            .where(*self._search_conditions(q))
            .order_by(Author.full_name, Author.id)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def count_search(self, q: str | None) -> int:
        stmt = select(func.count(Author.id)).where(*self._search_conditions(q))
        return await self.session.scalar(stmt) or 0

    async def get_by_orcid(self, orcid: str) -> Author | None:
        return await self.session.scalar(select(Author).where(Author.orcid == orcid))

    async def has_articles(self, author_id: int) -> bool:
        """Указан ли автор хотя бы в одной статье (удаление тогда запрещено: RESTRICT)."""
        stmt = select(exists().where(ArticleAuthor.author_id == author_id))
        return bool(await self.session.scalar(stmt))

    async def get_many(self, ids: Sequence[int]) -> list[Author]:
        """Авторы в порядке ids; отсутствующий id → NotFound."""
        found = {
            a.id: a for a in await self.session.scalars(select(Author).where(Author.id.in_(ids)))
        }
        missing = [i for i in ids if i not in found]
        if missing:
            raise NotFound("Author", missing)
        return [found[i] for i in ids]
