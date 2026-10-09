# ЛР1 — шпаргалка

## Тезисы

1. **10 таблиц** (8 сущностей + 2 связки M:N), 3 ENUM-типа, CHECK, частичный и HNSW-индексы — точно по DDL ТЗ §5.2.
2. **M:N статья↔автор** — association object `ArticleAuthor` с `author_order`; **статья↔слово** — `secondary=article_keywords`.
3. **Статья ↔ страницы — диапазоном** `page_start..page_end`, а не FK: индексация не ждёт разметки.
4. **Репозитории не коммитят** — транзакцию задаёт вызывающий (`get_session()`); `flush` — чтобы получить id и увидеть ошибки ограничений.
5. **`expire_on_commit=False`** + явная загрузка связей (`selectinload`, `contains_eager`) → нет N+1 и `MissingGreenlet`.
6. **Upsert страниц** — `INSERT … ON CONFLICT (issue_id, page_number) DO UPDATE`: повторный OCR не создаёт дубль.
7. **Одна активная задача OCR** — частичный уникальный индекс `ux_ocr_jobs_active`; `IntegrityError` → `Conflict` (→ 409), вставка в SAVEPOINT.
8. **`nearest()`** — `cosine_distance` (`<=>`) + HNSW, LEFT JOIN статьи по диапазону страниц.

## Команды

```bash
docker compose -f docker-compose.dev.yml up -d        # PostgreSQL 16 + pgvector
docker compose -f docker-compose.dev.yml ps           # STATUS: healthy
uv sync
cd packages/corag-db && uv run alembic upgrade head && cd ../..
uv run python -m corag_db.seed                        # идемпотентный сид
uv run python scripts/demo_lr1.py                     # 7 сценариев
uv run pytest -q tests/db                             # 22 passed
uv run ruff check .
docker compose -f docker-compose.dev.yml exec postgres psql -U corag -d corag -c '\dt' -c '\d+ chunks'
cd packages/corag-db && uv run alembic history        # 0001 → 0002 (head)
uv run alembic downgrade -1 && uv run alembic upgrade head && uv run alembic current
```

## Где в коде (`packages/corag-db/src/corag_db/…`)

| Понятие | Файл | Функция / класс |
| --- | --- | --- |
| Модели, M:N, индексы | `models.py` | `Article`, `ArticleAuthor`, `article_keywords`, `Chunk`, `ux_ocr_jobs_active` |
| Имена constraint'ов | `base.py` | `NAMING_CONVENTION`, `Base` |
| ENUM по значениям | `enums.py` | `pg_enum` |
| Движок, сессия, схема | `session.py` | `create_engine`, `create_sessionmaker`, `get_session`, `create_schema` |
| CRUD | `repositories/base.py` | `BaseRepository` |
| Каталог, N+1 | `repositories/articles.py` | `ArticleRepository.filter`, `get_full`, `create_with_relations` |
| Upsert | `repositories/pages.py` | `PageRepository.upsert` |
| Векторный поиск | `repositories/chunks.py` | `ChunkRepository.nearest` |
| Конфликт OCR | `repositories/ocr_jobs.py` | `OcrJobRepository.create_active` |
| Сид | `seed.py` | `seed` |
| Миграции | `packages/corag-db/alembic/versions/` | `0001_initial_schema.py`, `0002_articles_udc.py` |
| Тесты с откатом | `tests/db/conftest.py` | фикстура `session` |

## Частые ошибки

- **`MissingGreenlet`** — ленивая загрузка в async-коде или истёкшие атрибуты после commit. Лечится `selectinload` и `expire_on_commit=False`.
- **N+1** — связь читается в цикле без eager-загрузки. Видно по `ECHO_SQL=true`.
- **Одна `AsyncSession` в `asyncio.gather`** — `Session is already flushing` / `IllegalStateChangeError`. Нужна сессия на задачу.
- **Безымянные constraint'ы** — без `naming_convention` autogenerate даёт `None`-имена, `downgrade` не удаляет constraint. Плюс autogenerate не удаляет ENUM-типы — дописано вручную в 0001.
