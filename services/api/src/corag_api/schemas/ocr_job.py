"""DTO задач OCR."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from corag_db.enums import JobStatus


class OcrJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int
    requested_by: int
    status: JobStatus
    pages_done: int
    pages_total: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
