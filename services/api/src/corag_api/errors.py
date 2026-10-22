"""Доменные исключения API и их перевод в HTTP-ответы с единым форматом {"detail": "..."}.

NotFound и Conflict объявлены в corag_db.exceptions (их бросают репозитории),
остальные — здесь (их бросают сервисы и зависимости аутентификации).
"""

import logging
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from corag_db.exceptions import Conflict, NotFound

log = logging.getLogger("corag_api")


class AuthFailed(Exception):
    """Нет токена, он недействителен или неверны e-mail и пароль → 401."""


class Forbidden(Exception):
    """Недостаточно прав или пользователь заблокирован → 403."""


class ValidationFailed(Exception):
    """Запрос корректен по форме, но нарушает правило предметной области → 422."""


class PayloadTooLarge(Exception):
    """Загружаемый файл больше допустимого → 413."""


class ErrorOut(BaseModel):
    """Тело ответа с ошибкой (для документации OpenAPI)."""

    detail: str


STATUS_CODES: dict[type[Exception], int] = {
    AuthFailed: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    PayloadTooLarge: 413,
    ValidationFailed: 422,
}


def _handler(status_code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        log.info("[api] %s %s → %d: %s", request.method, request.url.path, status_code, exc)
        # RFC 9110: ответ 401 обязан сообщить схему аутентификации
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        return JSONResponse({"detail": str(exc)}, status_code=status_code, headers=headers)

    return handle


async def _integrity_error(request: Request, exc: Exception) -> JSONResponse:
    """Подстраховка: параллельные запросы прошли проверки сервиса, но упёрлись в ограничение БД."""
    orig = getattr(exc, "orig", exc)
    log.warning("[api] %s %s → 409: %s", request.method, request.url.path, orig)
    return JSONResponse({"detail": "нарушено ограничение целостности данных"}, status_code=409)


def register_exception_handlers(app: FastAPI) -> None:
    for exc_cls, status_code in STATUS_CODES.items():
        app.add_exception_handler(exc_cls, _handler(status_code))
    app.add_exception_handler(IntegrityError, _integrity_error)
