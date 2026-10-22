"""Базовый репозиторий: CRUD над одной ORM-моделью.

Методы не коммитят: транзакцией управляет вызывающий код (unit of work),
flush нужен, чтобы получить id и сразу увидеть нарушения ограничений.
"""

from collections.abc import Sequence
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.ext.asyncio import AsyncSession

from corag_db.base import Base
from corag_db.exceptions import NotFound


class BaseRepository[ModelT: Base]:
    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, id_: Any) -> ModelT | None:
        return await self.session.get(self.model, id_)

    async def get_or_raise(self, id_: Any) -> ModelT:
        obj = await self.get(id_)
        if obj is None:
            raise NotFound(self.model.__name__, id_)
        return obj

    async def list(self, limit: int = 50, offset: int = 0) -> Sequence[ModelT]:
        pk = inspect(self.model).primary_key
        stmt = select(self.model).order_by(*pk).limit(limit).offset(offset)
        return (await self.session.scalars(stmt)).all()

    async def add(self, obj: ModelT) -> ModelT:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def update(self, obj: ModelT, **fields: Any) -> ModelT:
        mapper = inspect(self.model)
        for name, value in fields.items():
            if name not in mapper.attrs:
                raise AttributeError(f"{self.model.__name__} не имеет поля {name!r}")
            setattr(obj, name, value)
        await self.session.flush()
        return obj

    async def delete(self, obj: ModelT) -> None:
        await self.session.delete(obj)
        await self.session.flush()
