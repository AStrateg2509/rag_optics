"""Авторы: поиск и CRUD."""

import logging
from collections.abc import Sequence

from corag_api.schemas.author import AuthorCreate, AuthorUpdate
from corag_db.exceptions import Conflict
from corag_db.models import Author
from corag_db.repositories import AuthorRepository

log = logging.getLogger("corag_api")


class AuthorService:
    def __init__(self, authors: AuthorRepository) -> None:
        self.authors = authors

    async def search(self, q: str | None, limit: int, offset: int) -> tuple[Sequence[Author], int]:
        return await self.authors.search(q, limit, offset), await self.authors.count_search(q)

    async def get(self, author_id: int) -> Author:
        return await self.authors.get_or_raise(author_id)

    async def create(self, data: AuthorCreate) -> Author:
        await self._ensure_orcid_free(data.orcid)
        author = await self.authors.add(Author(**data.model_dump()))
        log.info("[api] author_id=%d создан: %s", author.id, author.full_name)
        return author

    async def update(self, author_id: int, data: AuthorUpdate) -> Author:
        author = await self.authors.get_or_raise(author_id)
        changes = data.changes()
        if changes.get("orcid") and changes["orcid"] != author.orcid:
            await self._ensure_orcid_free(changes["orcid"])
        return await self.authors.update(author, **changes)

    async def delete(self, author_id: int) -> None:
        """Автора, указанного в статьях, удалить нельзя (в БД — ON DELETE RESTRICT) → 409."""
        author = await self.authors.get_or_raise(author_id)
        if await self.authors.has_articles(author_id):
            raise Conflict(f"автор {author_id} указан в статьях: сначала уберите его из статей")
        await self.authors.delete(author)
        log.info("[api] author_id=%d удалён", author_id)

    async def _ensure_orcid_free(self, orcid: str | None) -> None:
        if orcid and await self.authors.get_by_orcid(orcid) is not None:
            raise Conflict(f"ORCID {orcid} уже есть у другого автора")
