"""Задачи OCR: не более одной активной на выпуск, жизненный цикл задачи (А2)."""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from corag_db.enums import JobStatus
from corag_db.exceptions import Conflict
from corag_db.models import OcrJob
from corag_db.repositories.base import BaseRepository

ACTIVE_INDEX = "ux_ocr_jobs_active"


class OcrJobRepository(BaseRepository[OcrJob]):
    model = OcrJob

    async def create_active(
        self, issue_id: int, user_id: int, pages_total: int | None = None
    ) -> OcrJob:
        """Поставить задачу в очередь. Если активная (queued/running) уже есть → Conflict.

        Проверку делает БД (частичный уникальный индекс), а не SELECT перед INSERT:
        так не будет гонки двух одновременных запросов.
        """
        job = OcrJob(
            issue_id=issue_id,
            requested_by=user_id,
            status=JobStatus.QUEUED,
            pages_total=pages_total,
        )
        try:
            # SAVEPOINT: ошибка откатывает только эту вставку, а не всю транзакцию вызывающего
            async with self.session.begin_nested():
                self.session.add(job)
        except IntegrityError as exc:
            if ACTIVE_INDEX in str(exc.orig):
                raise Conflict(f"у выпуска {issue_id} уже есть активная задача OCR") from exc
            raise
        return job

    async def mark_running(self, job_id: int, pages_total: int | None = None) -> OcrJob:
        job = await self.get_or_raise(job_id)
        fields = {"status": JobStatus.RUNNING, "started_at": datetime.now(UTC)}
        if pages_total is not None:
            fields["pages_total"] = pages_total
        return await self.update(job, **fields)

    async def inc_progress(self, job_id: int, n: int = 1) -> OcrJob:
        """Атомарно увеличить pages_done (UPDATE … SET pages_done = pages_done + n)."""
        stmt = (
            update(OcrJob)
            .where(OcrJob.id == job_id)
            .values(pages_done=OcrJob.pages_done + n)
            .returning(OcrJob)
        )
        result = await self.session.scalars(
            stmt, execution_options={"populate_existing": True, "synchronize_session": False}
        )
        return result.one()

    async def mark_done(self, job_id: int) -> OcrJob:
        job = await self.get_or_raise(job_id)
        fields = {"status": JobStatus.DONE, "finished_at": datetime.now(UTC)}
        if job.pages_total is not None:
            fields["pages_done"] = job.pages_total
        return await self.update(job, **fields)

    async def mark_failed(self, job_id: int, error: str) -> OcrJob:
        job = await self.get_or_raise(job_id)
        return await self.update(
            job, status=JobStatus.FAILED, error=error, finished_at=datetime.now(UTC)
        )
