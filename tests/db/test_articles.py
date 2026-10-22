"""Фильтры каталога статей и страницы статьи по диапазону."""

import pytest

from corag_db.models import Author, Issue
from corag_db.repositories import ArticleCreate, ArticleRepository, AuthorRepository


@pytest.fixture
async def catalog(session, user, issue, pages):
    """Два выпуска (2020 и 2005), три статьи с разными авторами и словами."""
    old = Issue(year=2005, number=2, created_by=user.id)
    session.add(old)
    await session.flush()
    authors = AuthorRepository(session)
    ivanov = await authors.add(Author(full_name="Иванов Иван Иванович"))
    petrov = await authors.add(Author(full_name="Петров Пётр Петрович"))

    repo = ArticleRepository(session)
    a1 = await repo.create_with_relations(
        ArticleCreate(issue_id=issue.id, title="Голография", page_start=1, page_end=3),
        [ivanov.id, petrov.id],
        ["голография"],
    )
    a2 = await repo.create_with_relations(
        ArticleCreate(issue_id=issue.id, title="Нейросети", page_start=4, page_end=6),
        [petrov.id],
        ["нейронные сети"],
    )
    a3 = await repo.create_with_relations(
        ArticleCreate(issue_id=old.id, title="Дифракция", page_start=1, page_end=8),
        [ivanov.id],
        ["голография", "дифракция"],
    )
    return a1, a2, a3


async def test_filter_by_year(session, catalog):
    a1, a2, a3 = catalog
    result = await ArticleRepository(session).filter(year=2020)
    assert [a.id for a in result] == [a1.id, a2.id]


async def test_filter_by_author(session, catalog):
    a1, a2, a3 = catalog
    result = await ArticleRepository(session).filter(author="иванов")
    assert {a.id for a in result} == {a1.id, a3.id}


async def test_filter_by_keyword_and_year(session, catalog):
    a1, a2, a3 = catalog
    repo = ArticleRepository(session)
    assert {a.id for a in await repo.filter(keyword=" Голография")} == {a1.id, a3.id}
    assert [a.id for a in await repo.filter(keyword="голография", year=2005)] == [a3.id]


async def test_filter_pagination_and_loaded_relations(session, catalog):
    session.expunge_all()
    result = await ArticleRepository(session).filter(limit=1, offset=1)
    assert len(result) == 1
    # Связи загружены заранее (selectinload): обращение не вызывает ленивую загрузку
    article = result[0]
    assert article.issue.year == 2020
    assert article.authors and article.keywords is not None


async def test_pages_of_article(session, catalog):
    a1, _, _ = catalog
    pages = await ArticleRepository(session).pages_of(a1)
    assert [p.page_number for p in pages] == [1, 2, 3]


async def test_filter_by_issue_and_count(session, catalog, issue):
    a1, a2, a3 = catalog
    repo = ArticleRepository(session)
    assert [a.id for a in await repo.filter(issue_id=issue.id)] == [a1.id, a2.id]
    assert await repo.count_filtered() == 3
    assert await repo.count_filtered(issue_id=issue.id) == 2
    assert await repo.count_filtered(author="иванов", keyword="голография") == 2
    assert await repo.count_filtered(year=1999) == 0


async def test_set_keywords_replaces(session, catalog):
    a1, _, _ = catalog
    repo = ArticleRepository(session)
    article = await repo.get_full(a1.id)
    await repo.set_keywords(article, ["ДОЭ", " голография "])
    assert [k.value for k in article.keywords] == ["доэ", "голография"]
    await repo.set_keywords(article, [])
    assert article.keywords == []


async def test_author_search_count_and_has_articles(session, catalog):
    authors = AuthorRepository(session)
    assert await authors.count_search("ИВАНОВ") == 1
    assert await authors.count_search(None) == 2
    assert [a.full_name for a in await authors.search(None, limit=1, offset=1)] == [
        "Петров Пётр Петрович"
    ]
    ivanov = (await authors.search("иванов"))[0]
    assert await authors.has_articles(ivanov.id)
    lonely = await authors.add(Author(full_name="Без Статей"))
    assert not await authors.has_articles(lonely.id)
