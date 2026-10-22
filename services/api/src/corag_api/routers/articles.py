"""Статьи: каталог с фильтрами, карточка, разметка выпуска (admin)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response, status

from corag_api.auth.dependencies import require_admin, require_researcher
from corag_api.deps import ArticleServiceDep, PaginationDep
from corag_api.errors import AUTH_RESPONSES, ErrorOut
from corag_api.schemas.article import ArticleCreate, ArticleDetailOut, ArticleOut, ArticleUpdate
from corag_api.schemas.common import Paginated
from corag_api.services.article_service import ArticleFilters
from corag_db.models import Article

router = APIRouter(
    prefix="/articles",
    tags=["articles"],
    dependencies=[Depends(require_researcher)],
    responses=AUTH_RESPONSES,
)

NOT_FOUND = {404: {"model": ErrorOut, "description": "Статья не найдена"}}
INVALID = {422: {"description": "Диапазон страниц вне выпуска, нет выпуска или авторов"}}


@router.get("", response_model=Paginated[ArticleOut], summary="Каталог статей с фильтрами")
async def list_articles(
    service: ArticleServiceDep,
    page: PaginationDep,
    year: Annotated[int | None, Query(ge=1987, description="Год выпуска")] = None,
    author: Annotated[str | None, Query(description="Подстрока ФИО автора")] = None,
    keyword: Annotated[str | None, Query(description="Ключевое слово целиком")] = None,
    issue_id: Annotated[int | None, Query(description="Выпуск")] = None,
) -> Paginated[ArticleOut]:
    filters = ArticleFilters(year=year, author=author, keyword=keyword, issue_id=issue_id)
    items, total = await service.search(filters, page.limit, page.offset)
    return Paginated[ArticleOut].of(items, total, page.limit, page.offset)


@router.get(
    "/{article_id}",
    response_model=ArticleDetailOut,
    summary="Карточка статьи: авторы, ключевые слова, текст страниц",
    responses=NOT_FOUND,
)
async def get_article(article_id: int, service: ArticleServiceDep) -> ArticleDetailOut:
    article, pages = await service.get(article_id)
    return ArticleDetailOut.build(article, pages)


@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать статью с авторами и ключевыми словами (admin)",
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ErrorOut, "description": "DOI уже занят"}, **INVALID},
)
async def create_article(data: ArticleCreate, service: ArticleServiceDep) -> Article:
    return await service.create(data)


@router.patch(
    "/{article_id}",
    response_model=ArticleOut,
    summary="Изменить статью (admin)",
    dependencies=[Depends(require_admin)],
    responses={**NOT_FOUND, 409: {"model": ErrorOut}, **INVALID},
)
async def update_article(
    article_id: int, data: ArticleUpdate, service: ArticleServiceDep
) -> Article:
    return await service.update(article_id, data)


@router.delete(
    "/{article_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить статью (admin)",
    dependencies=[Depends(require_admin)],
    responses=NOT_FOUND,
)
async def delete_article(article_id: int, service: ArticleServiceDep) -> Response:
    await service.delete(article_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
