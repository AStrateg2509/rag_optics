"""Заполнение БД тестовыми данными. Идемпотентно: повторный запуск ничего не дублирует.

Запуск: uv run python -m corag_db.seed (схема — после alembic upgrade head).
Все выпуски, статьи и авторы вымышленные; DOI — с тестовым префиксом 10.5555.
Эмбеддинги чанков — случайные нормированные векторы (настоящие появятся в ЛР3).
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from pydantic_settings import BaseSettings
from sqlalchemy import func, select

from corag_db.base import Base
from corag_db.enums import IssueStatus, UserRole
from corag_db.models import EMBED_DIM, Author, Issue, OcrJob
from corag_db.repositories import (
    ArticleCreate,
    ArticleRepository,
    ChunkIn,
    ChunkRepository,
    IssueRepository,
    OcrJobRepository,
    PageRepository,
    UserRepository,
)
from corag_db.session import dispose_engine, get_session
from corag_db.settings import DbSettings

log = logging.getLogger("corag_db.seed")


class SeedSettings(BaseSettings):
    model_config = DbSettings.model_config

    SEED_ADMIN_PASSWORD: str = "admin12345"
    SEED_RESEARCHER_PASSWORD: str = "researcher12345"
    SEED_TEXT_LAYER_DIR: Path = Path("data/text_layer")


@dataclass
class SeedArticle:
    title: str
    pages: tuple[int, int]
    authors: list[str]  # ORCID авторов по порядку
    keywords: list[str]
    abstract: str
    doi: str


@dataclass
class SeedIssue:
    year: int
    volume: int
    number: int
    pages: list[str]  # Markdown страниц 1..N
    articles: list[SeedArticle] = field(default_factory=list)


SSAU = "Самарский университет"
IPSI = "ИСОИ РАН — филиал ФНИЦ «Кристаллография и фотоника» РАН"
IAE = "Институт автоматики и электрометрии СО РАН"

AUTHORS = {
    "0000-0000-0000-0001": ("Кузнецов Андрей Петрович", SSAU),
    "0000-0000-0000-0002": ("Орлова Мария Сергеевна", IPSI),
    "0000-0000-0000-0003": ("Беляев Дмитрий Олегович", SSAU),
    "0000-0000-0000-0004": ("Громова Елена Викторовна", IPSI),
    "0000-0000-0000-0005": ("Соколов Илья Андреевич", SSAU),
    "0000-0000-0000-0006": ("Захарова Анна Игоревна", SSAU),
    "0000-0000-0000-0007": ("Фёдоров Павел Николаевич", IAE),
}

ISSUES = [
    SeedIssue(
        year=1998,
        volume=18,
        number=1,
        pages=[
            "# Расчёт ДОЭ для фокусировки в кольцо\n\nРассматривается задача расчёта "
            "дифракционного оптического элемента (ДОЭ), фокусирующего лазерное излучение "
            "в кольцо заданного радиуса.",
            "## Постановка задачи\n\nФаза элемента ищется в приближении Френеля. Интенсивность "
            "в фокальной плоскости выражается через преобразование Ханкеля функции пропускания.",
            "## Результаты моделирования\n\nЭффективность фокусировки составила 86 %. "
            "Среднеквадратичное отклонение интенсивности вдоль кольца не превышает 7 %.",
            "# Итеративный алгоритм расчёта фазовых пластинок\n\nПредложена модификация "
            "алгоритма Герчберга–Сэкстона с адаптивным выбором весов.",
            "## Сходимость алгоритма\n\nПоказано, что ошибка убывает монотонно; за 30 итераций "
            "достигается энергетическая эффективность выше 90 %.",
            "## Эксперимент\n\nФазовая пластинка изготовлена методом фотолитографии с "
            "восемью уровнями квантования рельефа.",
            "# Хроника\n\nВ 1997 году в Самаре прошла школа-семинар по компьютерной оптике.",
        ],
        articles=[
            SeedArticle(
                "Расчёт дифракционного оптического элемента для фокусировки в кольцо",
                (1, 3),
                ["0000-0000-0000-0001", "0000-0000-0000-0002"],
                ["Дифракционная оптика", "фокусатор"],
                "Метод расчёта ДОЭ, фокусирующего излучение в кольцо.",
                "10.5555/corag.1998.18.1.1",
            ),
            SeedArticle(
                "Итеративный алгоритм расчёта фазовых пластинок",
                (4, 6),
                ["0000-0000-0000-0003"],
                ["дифракционная оптика", "итеративные алгоритмы", "фазовая пластинка"],
                "Модификация алгоритма Герчберга–Сэкстона с адаптивными весами.",
                "10.5555/corag.1998.18.1.2",
            ),
        ],
    ),
    SeedIssue(
        year=2015,
        volume=39,
        number=3,
        pages=[
            "# Вихревые лазерные пучки\n\nИсследуются пучки с орбитальным угловым моментом, "
            "формируемые спиральной фазовой пластинкой.",
            "## Топологический заряд\n\nТопологический заряд пучка определяется числом "
            "оборотов фазы вокруг оптической оси.",
            "## Острая фокусировка\n\nПри острой фокусировке вихревого пучка в фокусе "
            "появляется продольная составляющая электрического поля.",
            "## Эксперимент\n\nПучки формировались с помощью пространственного модулятора "
            "света с разрешением 1920×1080.",
            "# Обработка изображений сверточными фильтрами\n\nРассматриваются быстрые "
            "алгоритмы сверточной фильтрации изображений.",
            "## Сложность алгоритма\n\nРекурсивная реализация снижает вычислительную "
            "сложность до O(N) на пиксель независимо от размера окна.",
            "## Применение\n\nМетод применён для выделения контуров на спутниковых снимках.",
        ],
        articles=[
            SeedArticle(
                "Острая фокусировка вихревых лазерных пучков",
                (1, 4),
                ["0000-0000-0000-0004", "0000-0000-0000-0001", "0000-0000-0000-0005"],
                ["вихревые пучки", "острая фокусировка"],
                "Продольная составляющая поля при острой фокусировке вихревых пучков.",
                "10.5555/corag.2015.39.3.1",
            ),
            SeedArticle(
                "Быстрые алгоритмы сверточной фильтрации изображений",
                (5, 7),
                ["0000-0000-0000-0006"],
                ["обработка изображений"],
                "Рекурсивные алгоритмы фильтрации со сложностью O(N).",
                "10.5555/corag.2015.39.3.2",
            ),
        ],
    ),
    SeedIssue(
        year=2024,
        volume=48,
        number=2,
        pages=[
            "# Классификация гиперспектральных изображений\n\nПредложена нейросетевая "
            "архитектура для классификации гиперспектральных данных.",
            "## Архитектура сети\n\nСпектральные признаки извлекаются одномерными свертками, "
            "пространственные — двумерными.",
            "## Обучение\n\nСеть обучалась на наборе Indian Pines; использовалась аугментация "
            "спектральным шумом.",
            "## Результаты\n\nТочность классификации составила 97,8 %, что выше базовых методов "
            "на 2–3 %.",
            "# Метаповерхности\n\nКраткий обзор метаповерхностей для управления поляризацией "
            "света (статья не размечена).",
            "## Перспективы\n\nМетаповерхности позволяют заменить объёмные оптические элементы "
            "плоскими структурами.",
        ],
        articles=[
            SeedArticle(
                "Нейросетевая классификация гиперспектральных изображений",
                (1, 4),
                ["0000-0000-0000-0007", "0000-0000-0000-0006"],
                ["нейронные сети", "гиперспектральные изображения", "обработка изображений"],
                "Архитектура со спектральными и пространственными свертками.",
                "10.5555/corag.2024.48.2.1",
            ),
        ],
    ),
]

OCR_MODEL = "seed"


def load_text_layer(directory: Path, issue: SeedIssue) -> list[tuple[int, str, str]]:
    """(номер, текст, модель) страниц: из JSON scripts/import_text_layer.py или встроенные."""
    path = directory / f"{issue.year}-{issue.number}.json"
    if path.is_file():
        data = json.loads(path.read_text(encoding="utf-8"))
        log.info("[seed] выпуск %s №%s: тексты из %s", issue.year, issue.number, path)
        return [(p["page_number"], p["text_md"], "pymupdf-text-layer") for p in data["pages"]]
    return [(n, text, OCR_MODEL) for n, text in enumerate(issue.pages, start=1)]


def split_chunks(text: str) -> list[str]:
    """Простейший чанкинг для сида: абзацы страницы. Настоящий чанкер — в ЛР3."""
    return [p.strip() for p in text.split("\n\n") if p.strip()]


def random_unit_vectors(rng: np.random.Generator, n: int) -> np.ndarray:
    v = rng.normal(size=(n, EMBED_DIM)).astype(np.float32)
    return v / np.linalg.norm(v, axis=1, keepdims=True)


async def table_counts() -> dict[str, int]:
    async with get_session() as session:
        return {
            name: await session.scalar(select(func.count()).select_from(table))
            for name, table in Base.metadata.tables.items()
        }


async def seed() -> None:
    settings = SeedSettings()
    rng = np.random.default_rng(42)  # фиксированный seed: одинаковые векторы при каждом запуске

    async with get_session() as session:
        users = UserRepository(session)
        admin = await users.get_by_email("admin@corag.local") or await users.create(
            "admin@corag.local", settings.SEED_ADMIN_PASSWORD, "Администратор", UserRole.ADMIN
        )
        if await users.get_by_email("researcher@corag.local") is None:
            await users.create(
                "researcher@corag.local", settings.SEED_RESEARCHER_PASSWORD, "Исследователь"
            )
        log.info("[seed] пользователи: admin@corag.local, researcher@corag.local")

        author_ids: dict[str, int] = {}
        for orcid, (name, affiliation) in AUTHORS.items():
            author = await session.scalar(select(Author).where(Author.orcid == orcid))
            if author is None:
                author = Author(full_name=name, affiliation=affiliation, orcid=orcid)
                session.add(author)
                await session.flush()
            author_ids[orcid] = author.id
        log.info("[seed] авторы: %d", len(author_ids))

        issues, pages_repo = IssueRepository(session), PageRepository(session)
        articles, chunks = ArticleRepository(session), ChunkRepository(session)
        for spec in ISSUES:
            issue = await issues.get_by_year_number(spec.year, spec.number)
            if issue is None:
                issue = await issues.add(
                    Issue(
                        year=spec.year,
                        volume=spec.volume,
                        number=spec.number,
                        scan_path=f"scans/{spec.year}-{spec.number}.pdf",
                        created_by=admin.id,
                    )
                )
            page_texts = load_text_layer(settings.SEED_TEXT_LAYER_DIR, spec)
            for n, text, model in page_texts:
                await pages_repo.upsert(issue.id, n, text, model)
            # Все страницы выпуска в БД (их может быть больше, чем в текущем источнике текстов)
            pages = await pages_repo.list_by_issue(issue.id)
            await issues.update(issue, page_count=len(pages), status=IssueStatus.INDEXED)

            new_chunks = []
            for page in pages:
                parts = split_chunks(page.text_md or "")
                vectors = random_unit_vectors(rng, len(parts))
                new_chunks += [
                    ChunkIn(page.id, i, part, vec.tolist(), token_count=len(part.split()))
                    for i, (part, vec) in enumerate(zip(parts, vectors, strict=True))
                ]
            await chunks.replace_for_pages([p.id for p in pages], new_chunks)

            for a in spec.articles:
                if await articles.get_by_doi(a.doi) is None:
                    await articles.create_with_relations(
                        ArticleCreate(
                            issue_id=issue.id,
                            title=a.title,
                            page_start=a.pages[0],
                            page_end=a.pages[1],
                            abstract=a.abstract,
                            doi=a.doi,
                        ),
                        [author_ids[o] for o in a.authors],
                        a.keywords,
                    )
            log.info(
                "[seed] выпуск %d №%d: %d стр., %d чанков, %d статей",
                spec.year,
                spec.number,
                len(pages),
                len(new_chunks),
                len(spec.articles),
            )

        # Одна завершённая задача OCR (для первого выпуска)
        first = await issues.get_by_year_number(ISSUES[0].year, ISSUES[0].number)
        has_jobs = await session.scalar(select(OcrJob.id).where(OcrJob.issue_id == first.id))
        if has_jobs is None:
            jobs = OcrJobRepository(session)
            job = await jobs.create_active(first.id, admin.id, pages_total=first.page_count)
            await jobs.mark_running(job.id)
            await jobs.mark_done(job.id)
            log.info("[seed] задача OCR #%d для выпуска %d: done", job.id, first.year)

    counts = await table_counts()
    log.info("[seed] строк в таблицах: %s", ", ".join(f"{k}={v}" for k, v in counts.items()))


async def main() -> None:
    try:
        await seed()
    finally:
        await dispose_engine()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    asyncio.run(main())
