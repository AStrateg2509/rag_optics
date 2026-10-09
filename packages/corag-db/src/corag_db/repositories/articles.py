"""Статьи: каталог с фильтрами, карточка, разметка выпуска (Ф2, Ф3, А5)."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from corag_db.exceptions import NotFound
from corag_db.models import Article, ArticleAuthor, Author, Issue, Keyword, Page
from corag_db.repositories.authors import AuthorRepository
from corag_db.repositories.base import BaseRepository
from corag_db.repositories.keywords import KeywordRepository, normalize_keyword


@dataclass(slots=True)
class ArticleCreate:
    """Поля новой статьи (без связей)."""

    issue_id: int
    title: str
    page_start: int
    page_end: int
    abstract: str | None = None
    doi: str | None = None
    language: str = "ru"
    udc: str | None = None


# Явная загрузка связей карточки: авторы по порядку и ключевые слова — без N+1
_RELATIONS = (
    selectinload(Article.authors_assoc).joinedload(ArticleAuthor.author),
    selectinload(Article.keywords),
)


class ArticleRepository(BaseRepository[Article]):
    model = Article

    async def filter(
        self,
        year: int | None = None,
        author: str | None = None,
        keyword: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Sequence[Article]:
        """Каталог статей с фильтрами по году выпуска, автору (подстрока ФИО) и ключевому слову."""
        stmt = (
            select(Article)
            .join(Article.issue)
            .options(contains_eager(Article.issue), *_RELATIONS)
            .order_by(Issue.year.desc(), Issue.number, Article.page_start)
            .limit(limit)
            .offset(offset)
        )
        if year is not None:
            stmt = stmt.where(Issue.year == year)
        if author:
            # EXISTS по связке: статья не дублируется, если подходят несколько авторов
            stmt = stmt.where(
                Article.authors_assoc.any(
                    ArticleAuthor.author.has(Author.full_name.ilike(f"%{author.strip()}%"))
                )
            )
        if keyword:
            stmt = stmt.where(Article.keywords.any(Keyword.value == normalize_keyword(keyword)))
        return (await self.session.scalars(stmt)).all()

    async def get_full(self, article_id: int) -> Article:
        """Карточка статьи: выпуск, авторы по порядку, ключевые слова."""
        stmt = (
            select(Article)
            .where(Article.id == article_id)
            .options(joinedload(Article.issue), *_RELATIONS)
        )
        article = await self.session.scalar(stmt)
        if article is None:
            raise NotFound("Article", article_id)
        return article

    async def get_by_doi(self, doi: str) -> Article | None:
        return await self.session.scalar(select(Article).where(Article.doi == doi))

    async def create_with_relations(
        self, dto: ArticleCreate, author_ids: Sequence[int], keywords: Sequence[str]
    ) -> Article:
        """Статья вместе с авторами (порядок = порядок author_ids) и ключевыми словами."""
        authors = await AuthorRepository(self.session).get_many(author_ids)
        kws = await KeywordRepository(self.session).get_or_create_many(keywords)
        article = Article(**asdict(dto))
        article.set_authors(authors)
        article.keywords = kws
        await self.add(article)
        return await self.get_full(article.id)

    async def set_authors(self, article: Article, author_ids: Sequence[int]) -> Article:
        """Заменить авторов статьи; порядок = порядок author_ids. Статья — из get_full."""
        article.set_authors(await AuthorRepository(self.session).get_many(author_ids))
        await self.session.flush()
        return article

    async def add_keywords(self, article: Article, values: Sequence[str]) -> Article:
        """Добавить ключевые слова к статье (существующие не дублируются). Статья — из get_full."""
        for kw in await KeywordRepository(self.session).get_or_create_many(values):
            if kw not in article.keywords:
                article.keywords.append(kw)
        await self.session.flush()
        return article

    async def pages_of(self, article: Article) -> Sequence[Page]:
        """Страницы статьи — по диапазону page_start..page_end внутри выпуска."""
        stmt = (
            select(Page)
            .where(
                Page.issue_id == article.issue_id,
                Page.page_number.between(article.page_start, article.page_end),
            )
            .order_by(Page.page_number)
        )
        return (await self.session.scalars(stmt)).all()
