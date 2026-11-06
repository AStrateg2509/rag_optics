"""Ключевые слова: нормализация и создание недостающих (А5)."""

from collections.abc import Iterable, Sequence

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from corag_db.models import Keyword
from corag_db.repositories.base import BaseRepository


def normalize_keyword(value: str) -> str:
    return value.strip().lower()


class KeywordRepository(BaseRepository[Keyword]):
    model = Keyword

    async def get_or_create_many(self, values: Iterable[str]) -> list[Keyword]:
        """Вернуть ключевые слова в порядке values, недостающие создать (без дублей)."""
        normalized = list(dict.fromkeys(v for v in map(normalize_keyword, values) if v))
        if not normalized:
            return []
        insert_stmt = pg_insert(Keyword).values([{"value": v} for v in normalized])
        await self.session.execute(insert_stmt.on_conflict_do_nothing(index_elements=["value"]))
        rows = await self.session.scalars(select(Keyword).where(Keyword.value.in_(normalized)))
        by_value = {k.value: k for k in rows}
        return [by_value[v] for v in normalized]

    @staticmethod
    def _search_conditions(q: str | None) -> list[ColumnElement[bool]]:
        return [Keyword.value.contains(normalize_keyword(q))] if q and q.strip() else []

    async def search(self, q: str | None, limit: int = 50, offset: int = 0) -> Sequence[Keyword]:
        """Ключевые слова по подстроке (значения хранятся в нижнем регистре); пустой q — все."""
        stmt = (
            select(Keyword)
            .where(*self._search_conditions(q))
            .order_by(Keyword.value)
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.scalars(stmt)).all()

    async def count_search(self, q: str | None) -> int:
        stmt = select(func.count(Keyword.id)).where(*self._search_conditions(q))
        return await self.session.scalar(stmt) or 0

    async def get_by_value(self, value: str) -> Keyword | None:
        return await self.session.scalar(
            select(Keyword).where(Keyword.value == normalize_keyword(value))
        )
