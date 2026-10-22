"""Фикстуры тестов слоя данных: отдельная БД TEST_DATABASE_URL, откат после каждого теста."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from corag_db.enums import UserRole
from corag_db.models import Issue, Page, User
from corag_db.session import create_engine, create_schema, drop_schema
from corag_db.settings import get_settings


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    """Схема пересоздаётся один раз за прогон."""
    eng = create_engine(get_settings().TEST_DATABASE_URL)
    await drop_schema(eng)
    await create_schema(eng)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия внутри внешней транзакции; commit/rollback кода под тестом становятся SAVEPOINT."""
    async with engine.connect() as conn:
        trans = await conn.begin()
        sess = AsyncSession(
            bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        )
        try:
            yield sess
        finally:
            await sess.close()
            await trans.rollback()


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
