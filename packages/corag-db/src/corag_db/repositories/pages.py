"""Страницы выпуска: сохранение результата OCR и чтение текста (А2, Ф3)."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from corag_db.models import Page
from corag_db.repositories.base import BaseRepository


class PageRepository(BaseRepository[Page]):
    model = Page

    async def upsert(
        self, issue_id: int, page_number: int, text_md: str | None, ocr_model: str | None
    ) -> Page:
        """INSERT … ON CONFLICT (issue_id, page_number) DO UPDATE.

        Повторный OCR страницы обновляет текст и не создаёт дубль.
        """
        stmt = pg_insert(Page).values(
            issue_id=issue_id,
            page_number=page_number,
            text_md=text_md,
            ocr_model=ocr_model,
            recognized_at=func.now(),
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Page.issue_id, Page.page_number],
            set_={
                "text_md": stmt.excluded.text_md,
                "ocr_model": stmt.excluded.ocr_model,
                "recognized_at": stmt.excluded.recognized_at,
            },
        ).returning(Page)
        # populate_existing: если страница уже в identity map, обновить её атрибуты
        result = await self.session.scalars(stmt, execution_options={"populate_existing": True})
        return result.one()

    async def list_by_issue(self, issue_id: int) -> Sequence[Page]:
        stmt = select(Page).where(Page.issue_id == issue_id).order_by(Page.page_number)
        return (await self.session.scalars(stmt)).all()

    async def get_by_number(self, issue_id: int, page_number: int) -> Page | None:
        stmt = select(Page).where(Page.issue_id == issue_id, Page.page_number == page_number)
        return await self.session.scalar(stmt)
