"""Страницы выпуска: распознанный текст (источник для ссылок RAG)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from corag_api.auth.dependencies import require_researcher
from corag_api.deps import IssueServiceDep
from corag_api.errors import ErrorOut
from corag_api.schemas.page import PageOut
from corag_db.models import Page

router = APIRouter(tags=["issues"], dependencies=[Depends(require_researcher)])


@router.get(
    "/issues/{issue_id}/pages/{page_number}",
    response_model=PageOut,
    summary="Текст страницы выпуска",
    responses={404: {"model": ErrorOut, "description": "Нет выпуска или страницы"}},
)
async def get_page(
    issue_id: int, page_number: Annotated[int, Path(ge=1)], service: IssueServiceDep
) -> Page:
    return await service.get_page(issue_id, page_number)
