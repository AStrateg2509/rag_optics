"""CRUD через BaseRepository, пользователи, каскад и CHECK-ограничения."""

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from corag_db.enums import IssueStatus, UserRole
from corag_db.exceptions import Conflict, NotFound
from corag_db.models import Article, Author, Issue, Page
from corag_db.passwords import verify_password
from corag_db.repositories import (
    ArticleCreate,
    ArticleRepository,
    AuthorRepository,
    IssueRepository,
    KeywordRepository,
    UserRepository,
)


async def test_issue_crud(session, user):
    repo = IssueRepository(session)
    issue = await repo.add(Issue(year=2001, number=3, created_by=user.id))
    assert issue.id is not None
    assert issue.status is IssueStatus.CREATED  # server_default подтянулся через RETURNING

    assert await repo.get_or_raise(issue.id) is issue
    await repo.set_status(issue, IssueStatus.UPLOADED)
    assert (await repo.list(year=2001))[0].status is IssueStatus.UPLOADED

    await repo.delete(issue)
    assert await repo.get(issue.id) is None
    with pytest.raises(NotFound):
        await repo.get_or_raise(issue.id)


async def test_update_unknown_field(session, user):
    repo = IssueRepository(session)
    issue = await repo.add(Issue(year=2001, number=4, created_by=user.id))
    with pytest.raises(AttributeError):
        await repo.update(issue, nonexistent=1)


async def test_author_and_keyword_crud(session):
    authors = AuthorRepository(session)
    a = await authors.add(Author(full_name="Сойфер Виктор Александрович"))
    await authors.update(a, affiliation="Самарский университет")
    assert [x.id for x in await authors.search("сойфер")] == [a.id]
    await authors.delete(a)
    assert await authors.search("сойфер") == []

    kws = KeywordRepository(session)
    k = (await kws.get_or_create_many(["  Дифракция "]))[0]
    assert k.value == "дифракция"
    assert (await kws.list())[0].id == k.id


async def test_user_repository(session):
    repo = UserRepository(session)
    u = await repo.create("New@Corag.local", "secret", "Новый пользователь")
    assert u.role is UserRole.RESEARCHER and u.is_active
    assert verify_password("secret", u.password_hash)
    assert await repo.get_by_email("new@corag.local") is u
    with pytest.raises(Conflict):
        await repo.create("new@corag.local", "x", "Дубль")

    await repo.set_role(u, UserRole.ADMIN)
    await repo.set_active(u, False)
    assert (u.role, u.is_active) == (UserRole.ADMIN, False)


async def test_issue_delete_cascades(session, issue, pages):
    await ArticleRepository(session).add(
        Article(issue_id=issue.id, title="Статья", page_start=1, page_end=2)
    )
    counts = await IssueRepository(session).get_with_counts(issue.id)
    assert (counts.pages, counts.articles) == (6, 1)

    await IssueRepository(session).delete(issue)
    n_pages = await session.scalar(select(func.count(Page.id)).where(Page.issue_id == issue.id))
    n_articles = await session.scalar(
        select(func.count(Article.id)).where(Article.issue_id == issue.id)
    )
    assert (n_pages, n_articles) == (0, 0)


async def test_check_constraint_page_range(session, issue):
    with pytest.raises(IntegrityError, match="ck_articles_page_range"):
        await ArticleRepository(session).create_with_relations(
            ArticleCreate(issue_id=issue.id, title="Плохая", page_start=5, page_end=2), [], []
        )


async def test_check_constraint_year(session, user):
    with pytest.raises(IntegrityError, match="ck_issues_year_min"):
        await IssueRepository(session).add(Issue(year=1980, number=1, created_by=user.id))
