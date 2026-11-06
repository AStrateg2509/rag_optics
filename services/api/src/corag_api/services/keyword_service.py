"""Ключевые слова: список, создание, удаление."""

import logging
from collections.abc import Sequence

from corag_api.errors import ValidationFailed
from corag_api.schemas.keyword import KeywordCreate
from corag_db.exceptions import Conflict
from corag_db.models import Keyword
from corag_db.repositories import KeywordRepository
from corag_db.repositories.keywords import normalize_keyword

log = logging.getLogger("corag_api")


class KeywordService:
    def __init__(self, keywords: KeywordRepository) -> None:
        self.keywords = keywords

    async def search(self, q: str | None, limit: int, offset: int) -> tuple[Sequence[Keyword], int]:
        return await self.keywords.search(q, limit, offset), await self.keywords.count_search(q)

    async def create(self, data: KeywordCreate) -> Keyword:
        """Значение хранится нормализованным (нижний регистр, без пробелов по краям)."""
        value = normalize_keyword(data.value)
        if not value:
            raise ValidationFailed("пустое ключевое слово")
        if await self.keywords.get_by_value(value) is not None:
            raise Conflict(f"ключевое слово {value!r} уже есть")
        keyword = await self.keywords.add(Keyword(value=value))
        log.info("[api] keyword_id=%d создано: %s", keyword.id, value)
        return keyword

    async def delete(self, keyword_id: int) -> None:
        """Связи со статьями удаляет БД каскадом (article_keywords ON DELETE CASCADE)."""
        await self.keywords.delete(await self.keywords.get_or_raise(keyword_id))
        log.info("[api] keyword_id=%d удалено", keyword_id)
