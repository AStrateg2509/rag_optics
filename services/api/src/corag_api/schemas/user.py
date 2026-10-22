"""DTO пользователей и аутентификации."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field

from corag_api.schemas.common import NonEmptyStr, PatchModel
from corag_db.enums import UserRole


def _bcrypt_limit(password: str) -> str:
    # bcrypt учитывает не больше 72 байт; кириллица занимает 2 байта на символ
    if len(password.encode()) > 72:
        raise ValueError("пароль длиннее 72 байт (ограничение bcrypt)")
    return password


Password = Annotated[str, Field(min_length=8), AfterValidator(_bcrypt_limit)]


class RegisterIn(BaseModel):
    email: EmailStr
    password: Password
    full_name: Annotated[NonEmptyStr, Field(max_length=255)]


class UserOut(BaseModel):
    """Пользователь без password_hash."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    email: str
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class UserAdminUpdate(PatchModel):
    """Изменение пользователя администратором: роль и блокировка."""

    role: UserRole | None = None
    is_active: bool | None = None


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int = Field(description="Срок жизни токена, секунды")
