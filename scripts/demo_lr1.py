"""ЛР1: консольная демонстрация работы с БД во всех сценариях функционала.

Запуск (после alembic upgrade head и сида): uv run python scripts/demo_lr1.py
Каждый сценарий — своя транзакция; изменяющие сценарии в конце откатываются,
поэтому скрипт можно запускать повторно, и данные сида не меняются.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from corag_db.enums import IssueStatus, UserRole
from corag_db.exceptions import Conflict
from corag_db.models import EMBED_DIM, Article, Issue, Page
from corag_db.repositories import (
    ArticleCreate,
    ArticleRepository,
    AuthorRepository,
    ChunkRepository,
    IssueRepository,
    OcrJobRepository,
    PageRepository,
    UserRepository,
)
from corag_db.session import create_sessionmaker, dispose_engine, get_engine


def header(n: int, title: str) -> None:
    print(f"\n{'=' * 78}\n {n}. {title}\n{'=' * 78}")


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> None:
    """Простая текстовая таблица."""
    cells = [[str(h) for h in headers]] + [["—" if v is None else str(v) for v in r] for r in rows]
    widths = [max(len(row[i]) for row in cells) for i in range(len(headers))]
    for i, row in enumerate(cells):
        print((" " + " | ".join(v.ljust(w) for v, w in zip(row, widths, strict=True))).rstrip())
        if i == 0:
            print("-" + "-+-".join("-" * w for w in widths) + "-")
    if not rows:
        print(" (пусто)")


def short(text: str | None, n: int = 48) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


@asynccontextmanager
async def scenario(n: int, title: str, rollback: bool = True) -> AsyncIterator[AsyncSession]:
    """Сценарий в отдельной транзакции; по умолчанию изменения откатываются."""
    header(n, title)
    async with create_sessionmaker(get_engine())() as session:
        yield session
        if rollback:
            await session.rollback()
            print("\n[транзакция откатана — данные БД не изменились]")
        else:
            await session.commit()


async def count(session: AsyncSession, model, *where) -> int:
    return await session.scalar(select(func.count()).select_from(model).where(*where))


async def demo_users() -> None:
    async with scenario(1, "Пользователи: создание, поиск по e-mail, смена роли, блокировка") as s:
        repo = UserRepository(s)
        user = await repo.create("demo.user@corag.local", "secret", "Демо Пользователь")
        print(f"create          → id={user.id}, role={user.role}, is_active={user.is_active}")
        found = await repo.get_by_email("DEMO.USER@corag.local")
        print(f"get_by_email    → {found}")
        await repo.set_role(user, UserRole.ADMIN)
        await repo.set_active(user, False)
        try:
            await repo.create("demo.user@corag.local", "x", "Дубль")
        except Conflict as exc:
            print(f"create (дубль)  → Conflict: {exc}")
        users = await repo.list()
        table(
            ["id", "email", "роль", "активен"],
            [(u.id, u.email, u.role, u.is_active) for u in users],
        )


async def demo_issue_crud() -> None:
    async with scenario(2, "CRUD выпуска и каскадное удаление страниц и статей") as s:
        issues, pages = IssueRepository(s), PageRepository(s)
        admin = await UserRepository(s).get_by_email("admin@corag.local")
        issue = await issues.add(Issue(year=2030, volume=54, number=1, created_by=admin.id))
        print(f"create      → {issue}")
        for n in (1, 2, 3):
            await pages.upsert(issue.id, n, f"Страница {n}", "demo")
        s.add(Article(issue_id=issue.id, title="Демо-статья", page_start=1, page_end=3))
        await s.flush()

        stats = await issues.get_with_counts(issue.id)
        print(f"read        → {stats.issue}: страниц={stats.pages}, статей={stats.articles}")
        await issues.set_status(issue, IssueStatus.UPLOADED)
        print(f"set_status  → {(await issues.get_or_raise(issue.id)).status}")

        await issues.delete(issue)
        left_pages = await count(s, Page, Page.issue_id == issue.id)
        left_articles = await count(s, Article, Article.issue_id == issue.id)
        print(f"delete      → выпуск удалён; осталось страниц={left_pages}, статей={left_articles}")
        print("              (ON DELETE CASCADE в БД, passive_deletes=True в ORM)")


async def demo_article_m2m() -> None:
    async with scenario(3, "Статья ↔ авторы (порядок) и ключевые слова: M:N") as s:
        articles, authors = ArticleRepository(s), AuthorRepository(s)
        issue = await IssueRepository(s).get_by_year_number(2024, 2)
        a1 = (await authors.search("Кузнецов"))[0]
        a2 = (await authors.search("Орлова"))[0]
        article = await articles.create_with_relations(
            ArticleCreate(issue_id=issue.id, title="Метаповерхности", page_start=5, page_end=6),
            author_ids=[a1.id, a2.id],
            keywords=["Метаповерхности", "поляризация"],
        )

        def show(label: str) -> None:
            print(f"\n{label}: «{article.title}», стр. {article.page_start}–{article.page_end}")
            table(
                ["порядок", "автор", "организация"],
                [
                    (x.author_order, x.author.full_name, x.author.affiliation)
                    for x in article.authors_assoc
                ],
            )
            print(" ключевые слова:", ", ".join(k.value for k in article.keywords))

        show("Создана")
        await articles.set_authors(article, [a2.id, a1.id])
        await articles.add_keywords(article, ["Нанофотоника"])
        s.expunge_all()  # перечитываем из БД
        article = await articles.get_full(article.id)
        show("После смены порядка авторов и добавления слова")
        pages = await articles.pages_of(article)
        print(" страницы статьи (по диапазону):", [p.page_number for p in pages])


async def demo_filters() -> None:
    async with scenario(4, "Фильтры каталога статей: год, автор, ключевое слово") as s:
        repo = ArticleRepository(s)
        for label, kwargs in [
            ("year=2015", {"year": 2015}),
            ("author='кузнецов'", {"author": "кузнецов"}),
            ("keyword='Дифракционная оптика'", {"keyword": "Дифракционная оптика"}),
        ]:
            print(f"\nfilter({label}):")
            table(
                ["id", "выпуск", "стр.", "название", "авторы"],
                [
                    (
                        a.id,
                        f"{a.issue.year} №{a.issue.number}",
                        f"{a.page_start}–{a.page_end}",
                        short(a.title, 40),
                        ", ".join(x.full_name.split()[0] for x in a.authors),
                    )
                    for a in await repo.filter(**kwargs)
                ],
            )


async def demo_upsert() -> None:
    async with scenario(5, "Upsert страницы: INSERT … ON CONFLICT DO UPDATE") as s:
        repo = PageRepository(s)
        issue = await IssueRepository(s).get_by_year_number(2024, 2)
        before = await repo.get_by_number(issue.id, 1)
        total = await count(s, Page, Page.issue_id == issue.id)
        print(f"до:     id={before.id}, страниц в выпуске={total}, текст={short(before.text_md)!r}")
        page = await repo.upsert(
            issue.id, 1, "# Повторный OCR\n\nУточнённый текст.", "deepseek-ocr"
        )
        total = await count(s, Page, Page.issue_id == issue.id)
        print(f"после:  id={page.id}, страниц в выпуске={total}, текст={short(page.text_md)!r}")
        print(f"        ocr_model={page.ocr_model}, recognized_at={page.recognized_at:%H:%M:%S}")


async def demo_nearest() -> None:
    async with scenario(6, "Векторный поиск nearest(): top-5 по косинусному расстоянию") as s:
        repo = ChunkRepository(s)
        rng = np.random.default_rng(2024)  # фиксированный запрос — воспроизводимый вывод
        query = rng.normal(size=EMBED_DIM)
        query = (query / np.linalg.norm(query)).tolist()
        for label, kwargs in [("все годы", {}), ("year_from=2010", {"year_from": 2010})]:
            print(f"\nСлучайный запрос, {label}:")
            hits = await repo.nearest(query, top_k=5, **kwargs)
            table(
                ["score", "выпуск", "стр.", "статья", "фрагмент"],
                [
                    (
                        f"{h.score:+.4f}",
                        f"{h.year} №{h.number}",
                        h.page_number,
                        short(h.article_title, 30) if h.article_title else None,
                        short(h.text, 34),
                    )
                    for h in hits
                ],
            )


async def demo_ocr_conflict() -> None:
    async with scenario(7, "Задачи OCR: не более одной активной на выпуск") as s:
        jobs = OcrJobRepository(s)
        issue = await IssueRepository(s).get_by_year_number(2015, 3)
        admin = await UserRepository(s).get_by_email("admin@corag.local")
        job = await jobs.create_active(issue.id, admin.id, pages_total=issue.page_count)
        print(f"create_active         → {job}")
        try:
            await jobs.create_active(issue.id, admin.id)
        except Conflict as exc:
            print(f"create_active (2-я)   → Conflict: {exc}  (API ответит 409)")
        await jobs.mark_running(job.id)
        for _ in range(3):
            job = await jobs.inc_progress(job.id)
        print(f"mark_running + 3×inc  → status={job.status}, {job.pages_done}/{job.pages_total}")
        await jobs.mark_done(job.id)
        print(f"mark_done             → status={job.status}, {job.pages_done}/{job.pages_total}")
        again = await jobs.create_active(issue.id, admin.id)
        print(f"create_active (снова) → {again}")


async def main() -> None:
    try:
        await demo_users()
        await demo_issue_crud()
        await demo_article_m2m()
        await demo_filters()
        await demo_upsert()
        await demo_nearest()
        await demo_ocr_conflict()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
