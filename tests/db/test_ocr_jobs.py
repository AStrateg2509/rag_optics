"""Задачи OCR: частичный уникальный индекс ux_ocr_jobs_active и жизненный цикл."""

import pytest

from corag_db.enums import JobStatus
from corag_db.exceptions import Conflict
from corag_db.repositories import OcrJobRepository


async def test_second_active_job_conflicts(session, issue, user):
    repo = OcrJobRepository(session)
    job = await repo.create_active(issue.id, user.id, pages_total=6)
    assert job.status is JobStatus.QUEUED

    with pytest.raises(Conflict):
        await repo.create_active(issue.id, user.id)

    # Транзакция вызывающего не сломана (откатился только SAVEPOINT)
    await repo.mark_running(job.id)
    with pytest.raises(Conflict):
        await repo.create_active(issue.id, user.id)

    await repo.mark_done(job.id)
    second = await repo.create_active(issue.id, user.id)
    assert second.id != job.id


async def test_job_lifecycle(session, issue, user):
    repo = OcrJobRepository(session)
    job = await repo.create_active(issue.id, user.id)
    await repo.mark_running(job.id, pages_total=6)
    assert job.started_at is not None and job.pages_total == 6

    await repo.inc_progress(job.id)
    updated = await repo.inc_progress(job.id, 2)
    assert updated is job and job.pages_done == 3

    await repo.mark_failed(job.id, "CUDA out of memory")
    assert (job.status, job.error) == (JobStatus.FAILED, "CUDA out of memory")
    assert job.finished_at is not None
    # После failed задача неактивна — можно поставить новую
    await repo.create_active(issue.id, user.id)
