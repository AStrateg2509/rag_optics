"""Регистрация и вход (JWT)."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from corag_api.deps import AuthServiceDep
from corag_api.errors import ErrorOut
from corag_api.schemas.user import RegisterIn, TokenOut, UserOut
from corag_db.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Регистрация (роль researcher)",
    responses={409: {"model": ErrorOut, "description": "E-mail уже занят"}},
)
async def register(data: RegisterIn, service: AuthServiceDep) -> User:
    return await service.register(data)


@router.post(
    "/login",
    response_model=TokenOut,
    summary="Вход: e-mail (username) и пароль → JWT",
    responses={
        401: {"model": ErrorOut, "description": "Неверный e-mail или пароль"},
        403: {"model": ErrorOut, "description": "Пользователь заблокирован"},
    },
)
async def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()], service: AuthServiceDep
) -> TokenOut:
    """Форма application/x-www-form-urlencoded по OAuth2 Password: поля username и password."""
    return await service.login(form.username, form.password)
