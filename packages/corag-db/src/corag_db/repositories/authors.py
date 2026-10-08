"""Авторы: поиск по ФИО и выборка по списку id (А5)."""

from collections.abc import Sequence

from sqlalchemy import select

from corag_db.exceptions import NotFound
from corag_db.models import Author
from corag_db.repositories.base import BaseRepository


class AuthorRepository(BaseRepository[Author]):
    model = Author

    async def search(self, q: str, limit: int = 20) -> Sequence[Author]:
        """Поиск по подстроке ФИО без учёта регистра (ILIKE)."""
        stmt = (
            select(Author)
            .where(Author.full_name.ilike(f"%{q.strip()}%"))
            .order_by(Author.full_name)
            .limit(limit)
        )
        return (await self.session.scalars(stmt)).all()

    async def get_many(self, ids: Sequence[int]) -> list[Author]:
        """Авторы в порядке ids; отсутствующий id → NotFound."""
        found = {
            a.id: a for a in await self.session.scalars(select(Author).where(Author.id.in_(ids)))
        }
        missing = [i for i in ids if i not in found]
        if missing:
            raise NotFound("Author", missing)
        return [found[i] for i in ids]
