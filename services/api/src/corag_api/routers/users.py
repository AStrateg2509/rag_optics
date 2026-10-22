"""Пользователи: свой профиль; список, смена роли и блокировка — для администратора."""

from fastapi import APIRouter

from corag_api.auth.dependencies import AdminUser, CurrentUser
from corag_api.deps import PaginationDep, UserServiceDep
from corag_api.errors import ErrorOut
from corag_api.schemas.common import Paginated
from corag_api.schemas.user import UserAdminUpdate, UserOut
from corag_db.models import User

router = APIRouter(prefix="/users", tags=["users"])


@router.get("/me", response_model=UserOut, summary="Текущий пользователь")
async def me(user: CurrentUser) -> User:
    return user


@router.get("", response_model=Paginated[UserOut], summary="Список пользователей (admin)")
async def list_users(
    _: AdminUser, page: PaginationDep, service: UserServiceDep
) -> Paginated[UserOut]:
    items, total = await service.list(page.limit, page.offset)
    return Paginated[UserOut].of(items, total, page.limit, page.offset)


@router.patch(
    "/{user_id}",
    response_model=UserOut,
    summary="Роль и блокировка пользователя (admin)",
    responses={
        404: {"model": ErrorOut},
        409: {"model": ErrorOut, "description": "Действие над самим собой"},
    },
)
async def update_user(
    user_id: int, data: UserAdminUpdate, admin: AdminUser, service: UserServiceDep
) -> User:
    return await service.update(user_id, data, actor=admin)
