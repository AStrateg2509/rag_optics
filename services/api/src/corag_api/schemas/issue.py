"""DTO выпусков журнала."""

from datetime import datetime
from typing import Self

from pydantic import BaseModel, ConfigDict, Field

from corag_api.schemas.common import PatchModel
from corag_db.enums import IssueStatus
from corag_db.repositories import IssueWithCounts


class IssueCreate(BaseModel):
    year: int = Field(ge=1987, le=2100)
    volume: int | None = Field(None, ge=1, le=32767)
    number: int = Field(ge=1, le=32767)


class IssueUpdate(PatchModel):
    nullable = frozenset({"volume"})

    year: int | None = Field(None, ge=1987, le=2100)
    volume: int | None = Field(None, ge=1, le=32767)
    number: int | None = Field(None, ge=1, le=32767)


class IssueShort(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    year: int
    volume: int | None
    number: int


class IssueOut(IssueShort):
    scan_path: str | None
    page_count: int | None
    status: IssueStatus
    created_by: int
    created_at: datetime


class IssueDetailOut(IssueOut):
    """Карточка выпуска со счётчиками распознанных страниц, статей и чанков."""

    pages_count: int
    articles_count: int
    chunks_count: int

    @classmethod
    def from_counts(cls, data: IssueWithCounts) -> Self:
        return cls(
            **IssueOut.model_validate(data.issue).model_dump(),
            pages_count=data.pages,
            articles_count=data.articles,
            chunks_count=data.chunks,
        )
