"""Ключевые слова: нормализация и создание недостающих (А5)."""

from collections.abc import Iterable

from sqlalchemy import select
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
