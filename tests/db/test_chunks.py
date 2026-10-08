"""Векторный поиск nearest: ближайший чанк первым, источник по диапазону страниц."""

import numpy as np
import pytest

from corag_db.models import EMBED_DIM, Article, Issue, Page
from corag_db.repositories import ChunkIn, ChunkRepository


def unit(v: np.ndarray) -> list[float]:
    return (v / np.linalg.norm(v)).tolist()


@pytest.fixture
async def indexed(session, user, issue, pages):
    """Чанки на страницах 1–6 выпуска 2020 (статья на стр. 1–3) и на стр. 1 выпуска 1999."""
    rng = np.random.default_rng(0)
    session.add(Article(issue_id=issue.id, title="Статья 1–3", page_start=1, page_end=3))
    old = Issue(year=1999, number=1, created_by=user.id)
    session.add(old)
    await session.flush()
    old_page = Page(issue_id=old.id, page_number=1, text_md="старый текст")
    session.add(old_page)
    await session.flush()

    vectors = {p.id: unit(rng.normal(size=EMBED_DIM)) for p in [*pages, old_page]}
    chunks = [
        ChunkIn(page_id=pid, chunk_index=0, text=f"чанк {pid}", embedding=v)
        for pid, v in vectors.items()
    ]
    repo = ChunkRepository(session)
    assert await repo.replace_for_pages(list(vectors), chunks) == len(chunks)
    return vectors, old_page


async def test_nearest_returns_known_vector_first(session, pages, indexed):
    vectors, _ = indexed
    target = pages[1]  # страница 2 — внутри статьи 1–3
    hits = await ChunkRepository(session).nearest(vectors[target.id], top_k=5)

    assert len(hits) == 5
    assert hits[0].page_number == 2
    assert hits[0].score == pytest.approx(1.0, abs=1e-5)
    assert hits[0].article_title == "Статья 1–3"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


async def test_nearest_page_outside_article_has_no_source(session, pages, indexed):
    vectors, _ = indexed
    hit = (await ChunkRepository(session).nearest(vectors[pages[4].id], top_k=1))[0]
    assert (hit.page_number, hit.article_id) == (5, None)


async def test_nearest_year_filter(session, indexed):
    vectors, old_page = indexed
    repo = ChunkRepository(session)
    hits = await repo.nearest(vectors[old_page.id], top_k=10, year_from=2000)
    assert all(h.year >= 2000 for h in hits) and len(hits) == 6
    hits = await repo.nearest(vectors[old_page.id], top_k=10, year_to=1999)
    assert [h.year for h in hits] == [1999]


async def test_replace_for_pages_replaces(session, pages, indexed):
    vectors, _ = indexed
    repo = ChunkRepository(session)
    page = pages[0]
    new = [
        ChunkIn(page_id=page.id, chunk_index=i, text=f"новый {i}", embedding=vectors[page.id])
        for i in range(3)
    ]
    await repo.replace_for_pages([page.id], new)
    hits = await repo.nearest(vectors[page.id], top_k=3)
    assert sorted(h.text for h in hits) == ["новый 0", "новый 1", "новый 2"]
