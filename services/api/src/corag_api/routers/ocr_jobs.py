"""Задачи OCR: запуск распознавания выпуска (202) и статус задачи. Только admin."""

from fastapi import APIRouter, Depends, status

from corag_api.auth.dependencies import AdminUser, require_admin
from corag_api.deps import OcrServiceDep
from corag_api.errors import AUTH_RESPONSES, ErrorOut
from corag_api.schemas.ocr_job import OcrJobOut
from corag_db.models import OcrJob

router = APIRouter(
    tags=["ocr"],
    dependencies=[Depends(require_admin)],
    responses=AUTH_RESPONSES,
)


@router.post(
    "/issues/{issue_id}/ocr",
    response_model=OcrJobOut,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить OCR выпуска: задача ставится в очередь",
    responses={
        404: {"model": ErrorOut, "description": "Выпуск не найден"},
        409: {"model": ErrorOut, "description": "Нет скана, идёт OCR или есть активная задача"},
    },
)
async def start_ocr(issue_id: int, admin: AdminUser, service: OcrServiceDep) -> OcrJob:
    """202 Accepted: запрос принят, распознавание выполнит OCR Worker асинхронно."""
    return await service.start(issue_id, admin)


@router.get(
    "/ocr-jobs/{job_id}",
    response_model=OcrJobOut,
    summary="Статус задачи OCR",
    responses={404: {"model": ErrorOut}},
)
async def get_job(job_id: int, service: OcrServiceDep) -> OcrJob:
    return await service.get_job(job_id)
