"""Пользователи: регистрация, поиск по e-mail, роль и блокировка (Ф1, А6)."""

from sqlalchemy import select

from corag_db.enums import UserRole
from corag_db.exceptions import Conflict
from corag_db.models import User
from corag_db.passwords import hash_password
from corag_db.repositories.base import BaseRepository


def normalize_email(email: str) -> str:
    return email.strip().lower()


class UserRepository(BaseRepository[User]):
    model = User

    async def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(User.email == normalize_email(email))
        return await self.session.scalar(stmt)

    async def create(
        self,
        email: str,
        password: str,
        full_name: str,
        role: UserRole = UserRole.RESEARCHER,
    ) -> User:
        """Новый пользователь с хешем пароля; занятый e-mail → Conflict."""
        if await self.get_by_email(email) is not None:
            raise Conflict(f"e-mail {email!r} уже зарегистрирован")
        user = User(
            email=normalize_email(email),
            password_hash=hash_password(password),
            full_name=full_name,
            role=role,
        )
        return await self.add(user)

    async def set_role(self, user: User, role: UserRole) -> User:
        return await self.update(user, role=role)

    async def set_active(self, user: User, is_active: bool) -> User:
        return await self.update(user, is_active=is_active)
