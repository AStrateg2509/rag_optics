"""Запуск OCR выпуска и статус задачи."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from corag_api.publishers import JobPublisher
from corag_contracts.events import OcrJobMessage
from corag_db.enums import IssueStatus
from corag_db.exceptions import Conflict
from corag_db.models import OcrJob, User
from corag_db.repositories import IssueRepository, OcrJobRepository

log = logging.getLogger("corag_api")

# Из каких статусов выпуска можно (пере)запустить распознавание
STARTABLE = frozenset({IssueStatus.UPLOADED, IssueStatus.OCR_DONE, IssueStatus.FAILED})


class OcrService:
    def __init__(
        self,
        session: AsyncSession,
        issues: IssueRepository,
        jobs: OcrJobRepository,
        publisher: JobPublisher,
    ) -> None:
        self.session = session
        self.issues = issues
        self.jobs = jobs
        self.publisher = publisher

    async def start(self, issue_id: int, user: User) -> OcrJob:
        """Поставить задачу OCR в очередь. Ответ API — 202: работа выполнится асинхронно.

        Порядок важен: сначала commit задачи и статуса выпуска, потом публикация в брокер.
        Если опубликовать раньше, воркер может взять задачу, которой ещё нет в БД
        (транзакция не завершена или вовсе откатится).
        """
        issue = await self.issues.get_or_raise(issue_id)
        if issue.status not in STARTABLE:
            raise Conflict(
                f"выпуск {issue_id} в статусе {issue.status.value}: "
                "OCR запускается из uploaded, ocr_done или failed"
            )
        job = await self.jobs.create_active(issue.id, user.id, pages_total=issue.page_count)
        await self.issues.set_status(issue, IssueStatus.OCR_IN_PROGRESS)
        await self.session.commit()
        log.info("[api] ocr job_id=%d issue_id=%d: queued, commit выполнен", job.id, issue.id)

        assert issue.scan_path is not None  # статус uploaded и выше означает, что скан загружен
        await self.publisher.publish_job(
            OcrJobMessage(
                job_id=job.id, issue_id=issue.id, scan_path=issue.scan_path, requested_by=user.id
            )
        )
        return job

    async def get_job(self, job_id: int) -> OcrJob:
        return await self.jobs.get_or_raise(job_id)
