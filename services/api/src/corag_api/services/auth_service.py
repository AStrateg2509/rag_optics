"""Регистрация и вход."""

import logging

from corag_api.auth.jwt import issue_token
from corag_api.errors import AuthFailed, Forbidden
from corag_api.schemas.user import RegisterIn, TokenOut
from corag_api.settings import ApiSettings
from corag_db.models import User
from corag_db.passwords import verify_password
from corag_db.repositories import UserRepository

log = logging.getLogger("corag_api")


def _password_ok(password: str, password_hash: str) -> bool:
    # bcrypt 5 отклоняет пароли длиннее 72 байт исключением; такой пароль просто неверный
    return len(password.encode()) <= 72 and verify_password(password, password_hash)


class AuthService:
    def __init__(self, users: UserRepository, settings: ApiSettings) -> None:
        self.users = users
        self.settings = settings

    async def register(self, data: RegisterIn) -> User:
        """Новый пользователь, роль всегда researcher; занятый e-mail → Conflict (409)."""
        user = await self.users.create(data.email, data.password, data.full_name)
        log.info("[api] register user_id=%d email=%s role=%s", user.id, user.email, user.role)
        return user

    async def authenticate(self, email: str, password: str) -> User:
        """Проверка пароля: неверные данные → 401, заблокированный пользователь → 403."""
        user = await self.users.get_by_email(email)
        if user is None or not _password_ok(password, user.password_hash):
            raise AuthFailed("неверный e-mail или пароль")
        if not user.is_active:
            raise Forbidden("пользователь заблокирован")
        return user

    async def login(self, email: str, password: str) -> TokenOut:
        user = await self.authenticate(email, password)
        token = issue_token(user.id, user.role.value, self.settings)
        log.info(
            "[api] login user_id=%d role=%s → JWT на %d мин",
            user.id,
            user.role,
            self.settings.JWT_TTL_MIN,
        )
        return TokenOut(access_token=token, expires_in=self.settings.JWT_TTL_MIN * 60)
