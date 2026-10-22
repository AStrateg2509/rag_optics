"""Авторы: поиск (researcher+), CRUD (admin)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from corag_api.auth.dependencies import require_admin, require_researcher
from corag_api.deps import AuthorServiceDep, PaginationDep
from corag_api.errors import ErrorOut
from corag_api.schemas.author import AuthorCreate, AuthorOut, AuthorUpdate
from corag_api.schemas.common import Paginated
from corag_db.models import Author

router = APIRouter(prefix="/authors", tags=["authors"], dependencies=[Depends(require_researcher)])

NOT_FOUND = {404: {"model": ErrorOut, "description": "Автор не найден"}}


@router.get("", response_model=Paginated[AuthorOut], summary="Поиск авторов по ФИО")
async def list_authors(
    service: AuthorServiceDep,
    page: PaginationDep,
    q: Annotated[str | None, Query(description="Подстрока ФИО")] = None,
) -> Paginated[AuthorOut]:
    items, total = await service.search(q, page.limit, page.offset)
    return Paginated[AuthorOut].of(items, total, page.limit, page.offset)


@router.get("/{author_id}", response_model=AuthorOut, summary="Автор", responses=NOT_FOUND)
async def get_author(author_id: int, service: AuthorServiceDep) -> Author:
    return await service.get(author_id)


@router.post(
    "",
    response_model=AuthorOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать автора (admin)",
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ErrorOut, "description": "ORCID уже занят"}},
)
async def create_author(data: AuthorCreate, service: AuthorServiceDep) -> Author:
    return await service.create(data)


@router.patch(
    "/{author_id}",
    response_model=AuthorOut,
    summary="Изменить автора (admin)",
    dependencies=[Depends(require_admin)],
    responses={**NOT_FOUND, 409: {"model": ErrorOut}},
)
async def update_author(author_id: int, data: AuthorUpdate, service: AuthorServiceDep) -> Author:
    return await service.update(author_id, data)


@router.delete(
    "/{author_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить автора (admin)",
    dependencies=[Depends(require_admin)],
    responses={**NOT_FOUND, 409: {"model": ErrorOut, "description": "Автор указан в статьях"}},
)
async def delete_author(author_id: int, service: AuthorServiceDep) -> Response:
    await service.delete(author_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
