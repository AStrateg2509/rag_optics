"""Доступ RAG Service к БД: фабрика сессий (одна сессия — одна транзакция)."""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy.ext.asyncio import AsyncSession

# В сервисе — corag_db.session.get_session; в тестах — фабрика поверх тестовой сессии
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]
