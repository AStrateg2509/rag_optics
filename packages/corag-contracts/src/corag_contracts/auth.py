"""JWT access-токены CO-RAG.

Выдаёт API Service, проверяют API Service и Notification Service (ЛР4) по общему JWT_SECRET.
Подпись HS256; claims: sub (id пользователя), role, iat, exp.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "role", "iat", "exp"]


class InvalidToken(Exception):
    """Токен подделан, истёк или имеет неверный формат."""


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: int
    role: str
    issued_at: datetime
    expires_at: datetime


def create_access_token(user_id: int, role: str, secret: str, ttl: timedelta) -> str:
    """Подписанный токен со сроком жизни ttl."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> TokenClaims:
    """Проверить подпись и срок действия и вернуть claims; иначе InvalidToken.

    Алгоритм задан жёстко (algorithms=[HS256]): токен с alg=none или другим алгоритмом
    отклоняется, даже если в его заголовке указано иное.
    """
    try:
        payload = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS}
        )
        return TokenClaims(
            user_id=int(payload["sub"]),
            role=str(payload["role"]),
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise InvalidToken(str(exc)) from exc
