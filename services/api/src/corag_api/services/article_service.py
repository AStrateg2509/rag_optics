"""Статьи: каталог с фильтрами, карточка, разметка выпуска (CRUD с авторами и ключевыми словами)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from corag_api.errors import ValidationFailed
from corag_api.schemas.article import ArticleCreate, ArticleUpdate
from corag_db.exceptions import Conflict, NotFound
from corag_db.models import Article, Issue, Page
from corag_db.repositories import ArticleCreate as ArticleFields
from corag_db.repositories import ArticleRepository, IssueRepository

log = logging.getLogger("corag_api")


@dataclass(frozen=True, slots=True)
class ArticleFilters:
    year: int | None = None
    author: str | None = None
    keyword: str | None = None
    issue_id: int | None = None


class ArticleService:
    def __init__(self, articles: ArticleRepository, issues: IssueRepository) -> None:
        self.articles = articles
        self.issues = issues

    async def search(
        self, filters: ArticleFilters, limit: int, offset: int
    ) -> tuple[Sequence[Article], int]:
        f = filters
        items = await self.articles.filter(
            f.year, f.author, f.keyword, limit=limit, offset=offset, issue_id=f.issue_id
        )
        total = await self.articles.count_filtered(f.year, f.author, f.keyword, f.issue_id)
        return items, total

    async def get(self, article_id: int) -> tuple[Article, Sequence[Page]]:
        """Карточка статьи и распознанные страницы её диапазона."""
        article = await self.articles.get_full(article_id)
        return article, await self.articles.pages_of(article)

    async def create(self, data: ArticleCreate) -> Article:
        issue = await self.issues.get(data.issue_id)
        if issue is None:
            raise ValidationFailed(f"выпуск {data.issue_id} не существует")
        self._check_range(issue, data.page_start, data.page_end)
        await self._ensure_doi_free(data.doi)
        fields = ArticleFields(
            **data.model_dump(exclude={"author_ids", "keywords"}),
        )
        try:
            article = await self.articles.create_with_relations(
                fields, data.author_ids, data.keywords
            )
        except NotFound as exc:
            raise ValidationFailed(f"авторы не найдены: {exc.key}") from exc
        log.info(
            "[api] article_id=%d создана: issue_id=%d, стр. %d–%d, авторы %s, слова %s",
            article.id,
            issue.id,
            article.page_start,
            article.page_end,
            data.author_ids,
            [k.value for k in article.keywords],
        )
        return article

    async def update(self, article_id: int, data: ArticleUpdate) -> Article:
        article = await self.articles.get_full(article_id)
        changes = data.changes()
        author_ids = changes.pop("author_ids", None)
        keywords = changes.pop("keywords", None)

        start = changes.get("page_start", article.page_start)
        end = changes.get("page_end", article.page_end)
        if start > end:
            raise ValidationFailed(f"page_start ({start}) больше page_end ({end})")
        self._check_range(article.issue, start, end)
        if changes.get("doi") and changes["doi"] != article.doi:
            await self._ensure_doi_free(changes["doi"])

        await self.articles.update(article, **changes)
        if author_ids is not None:
            try:
                await self.articles.set_authors(article, author_ids)
            except NotFound as exc:
                raise ValidationFailed(f"авторы не найдены: {exc.key}") from exc
        if keywords is not None:
            await self.articles.set_keywords(article, keywords)
        log.info("[api] article_id=%d изменена: %s", article.id, sorted(data.changes()))
        return await self.articles.get_full(article.id)

    async def delete(self, article_id: int) -> None:
        await self.articles.delete(await self.articles.get_or_raise(article_id))
        log.info("[api] article_id=%d удалена", article_id)

    @staticmethod
    def _check_range(issue: Issue, start: int, end: int) -> None:
        """Диапазон страниц статьи должен лежать внутри выпуска (если число страниц известно)."""
        if issue.page_count is not None and end > issue.page_count:
            raise ValidationFailed(
                f"page_end ({end}) больше числа страниц выпуска ({issue.page_count})"
            )

    async def _ensure_doi_free(self, doi: str | None) -> None:
        if doi and await self.articles.get_by_doi(doi) is not None:
            raise Conflict(f"DOI {doi} уже присвоен другой статье")
