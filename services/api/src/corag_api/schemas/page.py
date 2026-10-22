"""DTO страниц выпуска."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict


class PageOut(BaseModel):
    """Страница выпуска и её текст (Markdown после OCR)."""

    model_config = ConfigDict(from_attributes=True)

    issue_id: int
    page_number: int
    text_md: str | None
    ocr_model: str | None
    recognized_at: datetime | None
