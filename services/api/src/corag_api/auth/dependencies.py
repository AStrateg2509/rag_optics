"""Аутентификация и авторизация: текущий пользователь по JWT и проверка роли."""

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends

from corag_api.auth.jwt import oauth2_scheme, read_token
from corag_api.deps import SettingsDep, UserRepoDep
from corag_api.errors import AuthFailed, Forbidden
from corag_contracts.auth import InvalidToken
from corag_db.enums import UserRole
from corag_db.models import User

# Иерархия ролей: admin наследует права researcher
ROLE_LEVEL = {UserRole.RESEARCHER: 1, UserRole.ADMIN: 2}


async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    users: UserRepoDep,
    settings: SettingsDep,
) -> User:
    """Пользователь из заголовка Authorization: Bearer <JWT>.

    Нет токена, подпись неверна, срок истёк, пользователя нет → 401 (AuthFailed);
    пользователь заблокирован → 403. Роль берётся из БД, а не из токена: смена роли
    и блокировка действуют сразу, не дожидаясь истечения токена.
    """
    if not token:
        raise AuthFailed("требуется аутентификация: Authorization: Bearer <token>")
    try:
        claims = read_token(token, settings)
    except InvalidToken as exc:
        raise AuthFailed(f"недействительный токен: {exc}") from exc
    user = await users.get(claims.user_id)
    if user is None:
        raise AuthFailed("пользователь токена не найден")
    if not user.is_active:
        raise Forbidden("пользователь заблокирован")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(role: UserRole) -> Callable[..., Awaitable[User]]:
    """Фабрика зависимостей: пропускает пользователя с ролью не ниже role, иначе 403."""

    async def dependency(user: CurrentUser) -> User:
        if ROLE_LEVEL[user.role] < ROLE_LEVEL[role]:
            raise Forbidden(f"недостаточно прав: нужна роль {role.value}")
        return user

    dependency.__name__ = f"require_{role.value}"
    return dependency


require_researcher = require_role(UserRole.RESEARCHER)
require_admin = require_role(UserRole.ADMIN)

ResearcherUser = Annotated[User, Depends(require_researcher)]
AdminUser = Annotated[User, Depends(require_admin)]
