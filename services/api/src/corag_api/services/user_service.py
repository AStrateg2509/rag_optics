"""Управление пользователями (администратор)."""

import logging
from collections.abc import Sequence

from corag_api.schemas.user import UserAdminUpdate
from corag_db.exceptions import Conflict
from corag_db.models import User
from corag_db.repositories import UserRepository

log = logging.getLogger("corag_api")


class UserService:
    def __init__(self, users: UserRepository) -> None:
        self.users = users

    async def list(self, limit: int, offset: int) -> tuple[Sequence[User], int]:
        return await self.users.list(limit, offset), await self.users.count()

    async def update(self, user_id: int, data: UserAdminUpdate, actor: User) -> User:
        """Смена роли и блокировка. Себя понизить или заблокировать нельзя (409)."""
        user = await self.users.get_or_raise(user_id)
        changes = data.changes()
        if user.id == actor.id and (
            changes.get("role", user.role) != user.role or changes.get("is_active") is False
        ):
            raise Conflict("нельзя понизить роль или заблокировать самого себя")
        await self.users.update(user, **changes)
        log.info("[api] user_id=%d изменён администратором %d: %s", user.id, actor.id, changes)
        return user
