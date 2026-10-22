"""Выпуски журнала: каталог, карточка, CRUD и загрузка PDF-скана."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Query, Response, UploadFile, status

from corag_api.auth.dependencies import AdminUser, require_admin, require_researcher
from corag_api.deps import IssueServiceDep, PaginationDep
from corag_api.errors import ErrorOut
from corag_api.schemas.common import Paginated
from corag_api.schemas.issue import IssueCreate, IssueDetailOut, IssueOut, IssueUpdate
from corag_db.models import Issue

router = APIRouter(prefix="/issues", tags=["issues"], dependencies=[Depends(require_researcher)])

NOT_FOUND = {404: {"model": ErrorOut, "description": "Выпуск не найден"}}


@router.get("", response_model=Paginated[IssueOut], summary="Каталог выпусков")
async def list_issues(
    service: IssueServiceDep,
    page: PaginationDep,
    year: Annotated[int | None, Query(ge=1987, description="Год выпуска")] = None,
) -> Paginated[IssueOut]:
    items, total = await service.list(year, page.limit, page.offset)
    return Paginated[IssueOut].of(items, total, page.limit, page.offset)


@router.get(
    "/{issue_id}",
    response_model=IssueDetailOut,
    summary="Карточка выпуска с числом страниц, статей и чанков",
    responses=NOT_FOUND,
)
async def get_issue(issue_id: int, service: IssueServiceDep) -> IssueDetailOut:
    return IssueDetailOut.from_counts(await service.get(issue_id))


@router.post(
    "",
    response_model=IssueOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать выпуск (admin)",
    responses={409: {"model": ErrorOut, "description": "Выпуск с таким годом и номером есть"}},
)
async def create_issue(data: IssueCreate, admin: AdminUser, service: IssueServiceDep) -> Issue:
    return await service.create(data, admin)


@router.patch(
    "/{issue_id}",
    response_model=IssueOut,
    summary="Изменить выпуск (admin)",
    dependencies=[Depends(require_admin)],
    responses={**NOT_FOUND, 409: {"model": ErrorOut}},
)
async def update_issue(issue_id: int, data: IssueUpdate, service: IssueServiceDep) -> Issue:
    return await service.update(issue_id, data)


@router.delete(
    "/{issue_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить выпуск со страницами и статьями (admin)",
    dependencies=[Depends(require_admin)],
    responses={**NOT_FOUND, 409: {"model": ErrorOut, "description": "Идёт OCR"}},
)
async def delete_issue(issue_id: int, service: IssueServiceDep) -> Response:
    await service.delete(issue_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{issue_id}/scan",
    response_model=IssueOut,
    summary="Загрузить PDF-скан выпуска (admin)",
    dependencies=[Depends(require_admin)],
    responses={
        **NOT_FOUND,
        409: {"model": ErrorOut, "description": "Идёт OCR"},
        413: {"model": ErrorOut, "description": "Файл больше MAX_SCAN_MB"},
        422: {"model": ErrorOut, "description": "Не PDF или PDF не читается"},
    },
)
async def upload_scan(
    issue_id: int,
    service: IssueServiceDep,
    file: Annotated[UploadFile, File(description="PDF-скан выпуска")],
) -> Issue:
    return await service.upload_scan(issue_id, file)
