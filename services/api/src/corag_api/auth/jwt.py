"""JWT в API Service: схема OAuth2 Password + Bearer, выдача и проверка токена."""

from datetime import timedelta

from fastapi.security import OAuth2PasswordBearer

from corag_api.settings import ApiSettings
from corag_contracts.auth import TokenClaims, create_access_token, decode_token

# tokenUrl относительный: Swagger UI построит его от адреса /openapi.json (учитывает root_path).
# auto_error=False — 401 формирует get_current_user, в общем формате ошибок
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="auth/login", auto_error=False)


def issue_token(user_id: int, role: str, settings: ApiSettings) -> str:
    return create_access_token(
        user_id, role, settings.JWT_SECRET, timedelta(minutes=settings.JWT_TTL_MIN)
    )


def read_token(token: str, settings: ApiSettings) -> TokenClaims:
    """Claims проверенного токена; подделанный или истёкший → corag_contracts.auth.InvalidToken."""
    return decode_token(token, settings.JWT_SECRET)
