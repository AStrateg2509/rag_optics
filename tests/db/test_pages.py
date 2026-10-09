"""Upsert страниц: повторное сохранение обновляет текст и не создаёт дубль."""

from sqlalchemy import func, select

from corag_db.models import Page
from corag_db.repositories import PageRepository


async def test_upsert_page(session, issue):
    repo = PageRepository(session)
    first = await repo.upsert(issue.id, 1, "черновик", "deepseek-ocr")
    second = await repo.upsert(issue.id, 1, "# Итоговый текст", "deepseek-ocr-v2")

    assert second.id == first.id
    assert (second.text_md, second.ocr_model) == ("# Итоговый текст", "deepseek-ocr-v2")
    assert second.recognized_at is not None
    count = await session.scalar(select(func.count(Page.id)).where(Page.issue_id == issue.id))
    assert count == 1


async def test_list_and_get_by_number(session, issue, pages):
    repo = PageRepository(session)
    assert [p.page_number for p in await repo.list_by_issue(issue.id)] == [1, 2, 3, 4, 5, 6]
    page = await repo.get_by_number(issue.id, 3)
    assert page is not None and page.text_md == "Страница 3"
    assert await repo.get_by_number(issue.id, 99) is None
