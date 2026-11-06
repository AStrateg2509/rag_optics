"""Общие фикстуры: отдельная БД TEST_DATABASE_URL, откат после каждого теста."""

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

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
