"""Асинхронный движок, фабрика сессий и создание схемы без Alembic."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from corag_db import models  # noqa: F401  — регистрирует таблицы в Base.metadata
from corag_db.base import Base
from corag_db.settings import get_settings

log = logging.getLogger("corag_db")


def create_engine(url: str | None = None, echo: bool | None = None) -> AsyncEngine:
    """Движок asyncpg с пулом соединений. По умолчанию — DATABASE_URL из настроек."""
    settings = get_settings()
    return create_async_engine(
        url or settings.DATABASE_URL,
        echo=settings.ECHO_SQL if echo is None else echo,
        pool_pre_ping=True,
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: после commit атрибуты остаются в памяти,
    # иначе обращение к ним вызовет ленивую загрузку и MissingGreenlet
    return async_sessionmaker(engine, expire_on_commit=False)


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    """Движок процесса по умолчанию (создаётся при первом обращении)."""
    global _engine, _sessionmaker
    if _engine is None:
        _engine = create_engine()
        _sessionmaker = create_sessionmaker(_engine)
    return _engine


@asynccontextmanager
async def get_session() -> AsyncIterator[AsyncSession]:
    """Одна сессия = одна транзакция (unit of work): commit при успехе, rollback при ошибке."""
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


async def dispose_engine() -> None:
    """Закрыть пул соединений движка по умолчанию."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
        _engine = _sessionmaker = None


async def create_schema(engine: AsyncEngine) -> None:
    """Создать расширение vector и все таблицы. Используется, когда нет Alembic (тесты)."""
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
    log.info("[db] схема создана: %d таблиц", len(Base.metadata.tables))


async def drop_schema(engine: AsyncEngine) -> None:
    """Удалить все таблицы и ENUM-типы."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
