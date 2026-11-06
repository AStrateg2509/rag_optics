"""Фикстуры слоя данных: пользователь, выпуск, страницы (engine и session — в tests/conftest.py)."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from corag_db.enums import UserRole
from corag_db.models import Issue, Page, User


@pytest.fixture
async def user(session: AsyncSession) -> User:
    u = User(email="admin@test.local", password_hash="x", full_name="Админ", role=UserRole.ADMIN)
    session.add(u)
    await session.flush()
    return u


@pytest.fixture
async def issue(session: AsyncSession, user: User) -> Issue:
    i = Issue(year=2020, number=1, volume=44, created_by=user.id, page_count=6)
    session.add(i)
    await session.flush()
    return i


@pytest.fixture
async def pages(session: AsyncSession, issue: Issue) -> list[Page]:
    ps = [Page(issue_id=issue.id, page_number=n, text_md=f"Страница {n}") for n in range(1, 7)]
    session.add_all(ps)
    await session.flush()
    return ps
