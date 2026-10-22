"""Ключевые слова: список (researcher+), создание и удаление (admin)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from corag_api.auth.dependencies import require_admin, require_researcher
from corag_api.deps import KeywordServiceDep, PaginationDep
from corag_api.errors import ErrorOut
from corag_api.schemas.common import Paginated
from corag_api.schemas.keyword import KeywordCreate, KeywordOut
from corag_db.models import Keyword

router = APIRouter(
    prefix="/keywords", tags=["keywords"], dependencies=[Depends(require_researcher)]
)


@router.get("", response_model=Paginated[KeywordOut], summary="Ключевые слова")
async def list_keywords(
    service: KeywordServiceDep,
    page: PaginationDep,
    q: Annotated[str | None, Query(description="Подстрока")] = None,
) -> Paginated[KeywordOut]:
    items, total = await service.search(q, page.limit, page.offset)
    return Paginated[KeywordOut].of(items, total, page.limit, page.offset)


@router.post(
    "",
    response_model=KeywordOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать ключевое слово (admin)",
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ErrorOut, "description": "Такое слово уже есть"}},
)
async def create_keyword(data: KeywordCreate, service: KeywordServiceDep) -> Keyword:
    return await service.create(data)


@router.delete(
    "/{keyword_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить ключевое слово (admin)",
    dependencies=[Depends(require_admin)],
    responses={404: {"model": ErrorOut}},
)
async def delete_keyword(keyword_id: int, service: KeywordServiceDep) -> Response:
    await service.delete(keyword_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
