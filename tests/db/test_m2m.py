"""Связи M:N статья ↔ автор (с порядком) и статья ↔ ключевое слово в обе стороны."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from corag_db.models import Author, Keyword
from corag_db.repositories import ArticleCreate, ArticleRepository, AuthorRepository


@pytest.fixture
async def authors(session):
    repo = AuthorRepository(session)
    return [
        await repo.add(Author(full_name=name))
        for name in ("Иванов И. И.", "Петров П. П.", "Сидоров С. С.")
    ]


async def test_article_authors_order_and_keywords(session, issue, authors):
    repo = ArticleRepository(session)
    a1, a2, a3 = authors
    article = await repo.create_with_relations(
        ArticleCreate(issue_id=issue.id, title="Дифракционная оптика", page_start=1, page_end=3),
        author_ids=[a2.id, a1.id],
        keywords=["Дифракция", "голография", "дифракция "],
    )
    session.expunge_all()  # читаем заново из БД, а не из identity map

    article = await repo.get_full(article.id)
    assert [a.full_name for a in article.authors] == ["Петров П. П.", "Иванов И. И."]
    assert [x.author_order for x in article.authors_assoc] == [1, 2]
    assert [k.value for k in article.keywords] == ["голография", "дифракция"]

    # Смена порядка авторов + новый автор, добавление ключевого слова
    await repo.set_authors(article, [a1.id, a2.id, a3.id])
    await repo.add_keywords(article, ["Фазовая пластинка", "голография"])
    session.expunge_all()

    article = await repo.get_full(article.id)
    assert [a.full_name for a in article.authors] == [
        "Иванов И. И.",
        "Петров П. П.",
        "Сидоров С. С.",
    ]
    assert len(article.keywords) == 3

    # Обратная сторона связей
    author = await session.scalar(
        select(Author).where(Author.id == a3.id).options(selectinload(Author.articles))
    )
    assert [x.id for x in author.articles] == [article.id]
    kw = await session.scalar(
        select(Keyword).where(Keyword.value == "дифракция").options(selectinload(Keyword.articles))
    )
    assert [x.id for x in kw.articles] == [article.id]


async def test_author_with_articles_cannot_be_deleted(session, issue, authors):
    """article_authors.author_id ON DELETE RESTRICT: автора со статьями удалить нельзя."""
    await ArticleRepository(session).create_with_relations(
        ArticleCreate(issue_id=issue.id, title="Статья", page_start=1, page_end=1),
        author_ids=[authors[0].id],
        keywords=[],
    )
    session.expunge_all()
    author = await AuthorRepository(session).get(authors[0].id)
    with pytest.raises(IntegrityError, match="fk_article_authors_author_id_authors"):
        await AuthorRepository(session).delete(author)
