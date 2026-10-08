# ЛР №1. Проектирование БД и слой доступа к данным

**Ветка:** `lr1-db` · **Технологии:** PostgreSQL 16 (+ pgvector), SQLAlchemy 2.0 (async), **[ОПЦИОНАЛЬНО]** Alembic
**Зависит от:** ЛР0 (схема БД, раздел 5 ТЗ) · **Готовит почву для:** ЛР2–ЛР5 (пакет `corag-db` используют все сервисы)
**Отчёт:** `docs/reports/lr1.md` (шаблон `docs/reports/_template.md`) · **Подготовка к сдаче:** `defense/lr1.md` + `defense/lr1-cheatsheet.md` (составляет Claude Code, раздел 8)

## 1. Задание из методички

Реализовать слой приложения для работы с БД:

- [ ] создать и настроить БД PostgreSQL;
- [ ] по схеме из ЛР0 реализовать модели данных средствами SQLAlchemy; CRUD-операции для основных сущностей;
- [ ] реализовать работу с БД под функционал проекта на основе этих моделей;
- [ ] подключиться к БД и заполнить её тестовыми данными;
- [ ] написать тестовый скрипт, который показывает работу с БД во всех сценариях функционала.
- [ ] **[ОПЦИОНАЛЬНО — доп. задание]** настроить миграции через Alembic и показать работу с ними.

**Сдача:** работающее приложение, результаты запросов выводятся в консоль. UI не нужен.

## 2. Результат лабы

- uv workspace, настроенные ruff и pytest.
- Пакет `packages/corag-db`:
    - ORM-модели всех 10 таблиц из ТЗ;
    - асинхронная сессия;
    - репозитории с CRUD и запросами под функционал;
    - сид-скрипт.
- `docker-compose.dev.yml` с PostgreSQL + pgvector.
- `scripts/demo_lr1.py` — консольная демонстрация всех сценариев.
- Тесты репозиториев на отдельной тестовой БД.

## 3. Структура файлов

```
pyproject.toml                       # [tool.uv.workspace] members = ["packages/*", "services/*", "tools/*"]
.env.example                         # POSTGRES_*, DATABASE_URL, TEST_DATABASE_URL
docker-compose.dev.yml               # postgres: pgvector/pgvector:pg16, том pgdata, порт 5432
deploy/postgres/init.sql             # CREATE EXTENSION IF NOT EXISTS vector; CREATE DATABASE corag_test;
packages/corag-db/
  pyproject.toml                     # sqlalchemy[asyncio], asyncpg, pgvector, pydantic-settings, bcrypt
  src/corag_db/
    __init__.py
    settings.py                      # DbSettings(DATABASE_URL, ECHO_SQL)
    base.py                          # DeclarativeBase, naming_convention для constraint'ов
    enums.py                         # UserRole, IssueStatus, JobStatus (Python Enum → PG ENUM)
    models.py                        # User, Issue, Page, Article, Author, ArticleAuthor, Keyword, article_keywords, Chunk, OcrJob
    session.py                       # create_engine(), async_sessionmaker, get_session() как async-контекст
    exceptions.py                    # NotFound, Conflict
    repositories/
      base.py                        # BaseRepository[ModelT]: get, get_or_raise, list, add, update, delete
      users.py  issues.py  pages.py  articles.py  authors.py  keywords.py  chunks.py  ocr_jobs.py
    seed.py                          # заполнение тестовыми данными, запуск: uv run python -m corag_db.seed
  alembic.ini                        # [ОПЦИОНАЛЬНО]
  alembic/                           # [ОПЦИОНАЛЬНО] env.py (async), versions/
scripts/demo_lr1.py
tests/db/conftest.py  tests/db/test_*.py
```

## 4. Шаги реализации

### Шаг 1. Каркас репозитория

1. `uv init` корня как workspace. Python 3.12. В корневом `pyproject.toml`:
    - настройки ruff (line-length 100, правила `E,F,I,UP,B,SIM,ASYNC`);
    - настройки pytest (`asyncio_mode = "auto"`).
2. Dev-зависимости в корне: `pytest`, `pytest-asyncio`, `ruff`.
3. `.gitignore`: `.venv`, `.env`, `__pycache__`, `models/`, `data/`.
4. `docker-compose.dev.yml`: сервис `postgres` на образе `pgvector/pgvector:pg16`.
    - Переменные берутся из `.env`.
    - Том `pgdata` для данных.
    - `deploy/postgres/init.sql` монтируется в `/docker-entrypoint-initdb.d/`.
    - Healthcheck через `pg_isready`.
5. **Проверка:** `docker compose -f docker-compose.dev.yml up -d` → `psql` показывает расширение `vector` в обеих БД.

### Шаг 2. ORM-модели (`models.py`)

Стиль SQLAlchemy 2.0: `Mapped[...]`, `mapped_column(...)`. Поля и ограничения в точности по DDL из ТЗ (раздел 5.2).

- **Перечисления.** `UserRole`, `IssueStatus`, `JobStatus` — `sqlalchemy.Enum(..., name="user_role", ...)`, значения в нижнем регистре.
- **M:N статья ↔ автор.** Паттерн *association object*: модель `ArticleAuthor`.
    - Составной PK `(article_id, author_id)` и поле `author_order`.
    - `Article.authors_assoc` → `ArticleAuthor` (`order_by=ArticleAuthor.author_order`, `cascade="all, delete-orphan"`).
    - Удобное свойство `Article.authors` (association_proxy или property).
- **M:N статья ↔ ключевое слово.** Обычная таблица `article_keywords`, связь `relationship(secondary=article_keywords)`.
- **Каскады.** `Issue` → `pages`, `articles`, `ocr_jobs` с `ON DELETE CASCADE` на уровне БД (`ondelete="CASCADE"`) и `passive_deletes=True`. `Page` → `chunks` так же.
- **Chunk.embedding.** `Vector(1024)` из `pgvector.sqlalchemy`. Размерность берётся из настройки `EMBED_DIM` (по умолчанию 1024).
- **Индексы в `__table_args__`:**
    - `Index("ix_chunks_embedding", Chunk.embedding, postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 64}, postgresql_ops={"embedding": "vector_cosine_ops"})`;
    - `ix_articles_issue_pages (issue_id, page_start, page_end)`;
    - `ix_authors_full_name`;
    - частичный уникальный индекс `ux_ocr_jobs_active` на `ocr_jobs(issue_id)` с `postgresql_where=status.in_(["queued","running"])`.
- **CHECK-ограничения:**
    - `year >= 1987`;
    - `page_start <= page_end`;
    - `page_number > 0`;
    - `author_order > 0`.
- В `base.py` задать `naming_convention` для constraint'ов: тогда имена стабильные, и Alembic генерирует аккуратные миграции.

### Шаг 3. Сессия и настройки

- В `session.py`: `create_async_engine(DATABASE_URL)`, `async_sessionmaker(expire_on_commit=False)`.
- Функция `create_schema(engine)`: выполняет `CREATE EXTENSION IF NOT EXISTS vector` и `Base.metadata.create_all`. Нужна, когда Alembic не используется.

### Шаг 4. Репозитории

`BaseRepository[ModelT]` принимает `AsyncSession` и даёт `get`, `get_or_raise` (→ `NotFound`), `list(limit, offset)`, `add`, `update(obj, **fields)`, `delete`.
Методы не коммитят: транзакцией управляет вызывающий (unit of work). Специализированные методы:

| Репозиторий | Методы | Сценарий ТЗ |
| --- | --- | --- |
| `UserRepository` | `get_by_email`, `set_role`, `set_active` | Ф1, А6 |
| `IssueRepository` | `list(year=…)`, `set_status`, `get_with_counts` | Ф2, А1 |
| `PageRepository` | `upsert(issue_id, page_number, text_md, ocr_model)` через `INSERT … ON CONFLICT (issue_id, page_number) DO UPDATE`, `list_by_issue`, `get(issue_id, n)` | А2, Ф3 |
| `ArticleRepository` | `filter(year, author, keyword, limit, offset)` с `selectinload` авторов и слов, `get_full(id)`, `create_with_relations(dto, author_ids, keywords)`, `pages_of(article)` | Ф2, Ф3, А5 |
| `AuthorRepository` | `search(q)` (ILIKE), `get_many(ids)` | А5 |
| `KeywordRepository` | `get_or_create_many(values)` (нормализация: trim + lower) | А5 |
| `ChunkRepository` | `replace_for_pages(page_ids, chunks)`, `nearest(query_vec, top_k, year_from, year_to)` — запрос из раздела 5.3 ТЗ, с `LEFT JOIN articles` по диапазону страниц; `cosine_distance` из pgvector | Ф4, Ф5, А3 |
| `OcrJobRepository` | `create_active(issue_id, user_id)` (`IntegrityError` по `ux_ocr_jobs_active` → `Conflict`), `mark_running`, `inc_progress`, `mark_done`, `mark_failed(error)` | А2 |

### Шаг 5. Сид-данные (`seed.py`)

Скрипт идемпотентный: повторный запуск ничего не дублирует. Что он создаёт:

- Пользователей `admin@corag.local` (admin) и `researcher@corag.local` (researcher). Пароли из `.env`, хеш bcrypt.
- 3 выпуска разных лет.
- По 6–10 страниц на выпуск с правдоподобным научным текстом в Markdown. Удобный вариант — `scripts/import_text_layer.py`: вытаскивает текстовый слой из 1–2 современных PDF журнала через PyMuPDF. Иначе — короткие тексты вручную.
- 4–6 статей с диапазонами страниц, 5–8 авторов (у части статей несколько авторов с порядком), 8–10 ключевых слов.
- Чанки со **случайными нормированными векторами** `numpy` — только чтобы показать `nearest()`. Настоящие эмбеддинги появятся в ЛР3 через `IndexIssue`.
- Одну задачу OCR в статусе `done`.

### Шаг 6. Демо-скрипт `scripts/demo_lr1.py`

Пронумерованные сценарии; перед каждым — заголовок, результат — читаемой таблицей в консоли:

1. Создание пользователя, поиск по e-mail, смена роли, блокировка.
2. CRUD выпуска: создать → прочитать → изменить статус → удалить. Показать, что удаление каскадом сносит страницы и статьи.
3. Статья с двумя авторами и ключевыми словами: создание, чтение с авторами по порядку. Изменение — сменить порядок авторов, добавить слово.
4. Фильтры каталога: по году, по автору, по ключевому слову.
5. Upsert страницы: второй вызов обновляет текст и не создаёт дубль.
6. `nearest()` по случайному вектору: top-5 чанков с указанием статьи и страницы.
7. Конфликт OCR: вторая активная задача на тот же выпуск → `Conflict`. Затем `mark_done` — и новую задачу снова можно создать.

Каждый сценарий работает в своей транзакции. Изменения сценариев 1–3 откатываются или удаляются, чтобы скрипт можно было запускать повторно.

### Шаг 7. Тесты

- `conftest.py`:
    - движок на `TEST_DATABASE_URL`;
    - `create_schema` один раз за сессию;
    - на каждый тест — соединение + транзакция с откатом (паттерн `join_transaction_mode="create_savepoint"`).
- Тесты:
    - CRUD через `BaseRepository`;
    - M:N в обе стороны;
    - `upsert` страницы;
    - фильтры статей;
    - `nearest` — ближайший к известному вектору чанк идёт первым;
    - частичный уникальный индекс OCR-задач.

### Шаг 8. [ОПЦИОНАЛЬНО — доп. задание] Миграции Alembic

1. `alembic init -t async alembic` внутри `packages/corag-db`.
    - В `env.py`: `target_metadata = Base.metadata`, URL из `DbSettings`.
    - В шаблон миграций добавить `import pgvector.sqlalchemy`.
2. Первая ревизия: `alembic revision --autogenerate -m "initial schema"`.
    - В начало `upgrade()` вручную добавить `op.execute("CREATE EXTENSION IF NOT EXISTS vector")`.
    - Проверить, что ENUM-типы и HNSW-индекс созданы, а в `downgrade()` типы удаляются.
3. Вторая ревизия для демонстрации — небольшое реальное изменение. Например, `articles.udc VARCHAR(50)` (УДК статьи).
4. Демонстрация: `alembic upgrade head` → `alembic current` → `alembic downgrade -1` → `alembic history`.
5. После этого `create_schema` используется только в тестах. Сид и демо работают поверх `alembic upgrade head`.

## 5. Как проверить и показать

```bash
docker compose -f docker-compose.dev.yml up -d
uv sync
uv run python -m corag_db.seed      # или сначала: cd packages/corag-db && uv run alembic upgrade head
uv run python scripts/demo_lr1.py
uv run pytest -q tests/db
uv run ruff check .
```

## 6. Критерии готовности

- [ ] Все 10 таблиц, ENUM-типы, CHECK-ограничения и индексы совпадают с DDL из ТЗ (проверка через `\d+` в psql).
- [ ] Обе связи M:N работают; порядок авторов сохраняется.
- [ ] CRUD есть для `users`, `issues`, `articles`, `authors`, `keywords`.
- [ ] Специальные запросы работают: фильтр каталога, upsert страниц, `nearest`, конфликт активной задачи OCR.
- [ ] Сид идемпотентен; демо-скрипт показывает все 7 сценариев и запускается повторно без ошибок.
- [ ] `pytest` и `ruff` проходят.
- [ ] **[ОПЦИОНАЛЬНО]** Alembic: 2 ревизии, upgrade и downgrade работают.
- [ ] Подготовка к сдаче составлена по разделу 8: `defense/lr1.md` (сценарий 5–7 мин, теория, вопросы с ответами) и `defense/lr1-cheatsheet.md`; команды проверены запуском.
- [ ] Отчёт `docs/reports/lr1.md` составлен по шаблону: настоящий вывод консоли, схемы, листинги; список TODO (скриншоты) закрыт.
- [ ] Пройден сценарий сдачи из `defense/lr1.md` (репетиция).
- [ ] PR `lr1-db` → `main` с чек-листом.

## 7. Подводные камни

- Нельзя использовать один `AsyncSession` в параллельных корутинах.
- `expire_on_commit=False`, иначе после commit обращение к атрибутам вызывает ленивую загрузку и падает с `MissingGreenlet`.
- Без `naming_convention` autogenerate Alembic даёт безымянные constraint'ы, и `downgrade` ломается.
- HNSW-индекс на пустой таблице создаётся мгновенно. Пересоздавать его после сида не нужно.

## 8. Подготовка к сдаче (составляет Claude Code)

Делается **до отчёта**: раздел 2 «Теория» из `defense/lr1.md` используется в отчёте (раздел 9).
Порядок закрытия лабы:

1. Пройти разделы 5–6.
2. Подготовка к сдаче (этот раздел).
3. Отчёт (раздел 9).
4. Репетиция сдачи.
5. PR.

Общие правила — в CLAUDE.md, раздел «Документация по лабе». Промпт — 10.2.

### 8.1. `defense/lr1.md` — сценарий сдачи, теория, вопросы

#### Раздел 1. Сценарий сдачи (5–7 мин)

**Подготовка:**

- `docker compose -f docker-compose.dev.yml up -d`, сид выполнен;
- открыты терминал с демо, psql (`\dt`), `models.py` в VS Code;
- крупный шрифт терминала.

| Время | Говорю | Показываю |
| --- | --- | --- |
| 0:00–0:40 | Цель ЛР1, состав БД (8 сущностей, 10 таблиц, 2 связи M:N) | ER-диаграмма из `report.md` |
| 0:40–1:50 | Как устроены модели: M:N через association object, вектор и HNSW, ограничения | `models.py`: `Article`, `ArticleAuthor`, `Chunk`; psql `\d+ chunks` |
| 1:50–4:20 | Сценарии работы с БД | `uv run python scripts/demo_lr1.py`; прокомментировать сценарии 2 (каскад), 3 (M:N), 6 (`nearest`), 7 (конфликт OCR) |
| 4:20–5:30 | Тесты; **[ОПЦИОНАЛЬНО]** миграции | `pytest -q tests/db`; `alembic history`, `downgrade -1`, `upgrade head` |
| 5:30–7:00 | Резерв на вопросы | — |

#### Раздел 2. Минимальная теория (2–3 стр.)

- Реляционная модель: ключи, связи 1:N и M:N, нормализация до 3НФ на примере таблиц CO-RAG.
- Транзакции и ACID; `READ COMMITTED` в PostgreSQL.
- ORM: паттерны Data Mapper (SQLAlchemy) и Active Record; Unit of Work, Identity Map; Engine и пул соединений, Session.
- Загрузка связей (lazy, selectin, joined) и N+1.
- Асинхронный доступ к БД: зачем он в асинхронном веб-приложении.
- Индексы: B-tree и векторный HNSW (идея приближённого поиска ближайших соседей).
- **[ОПЦИОНАЛЬНО]** Миграции: версии схемы, `upgrade` и `downgrade`, autogenerate.

#### Раздел 3. Вопросы преподавателя с ответами (12–15)

На каждую тему — формулировка вопроса и ответ в 2–4 предложения. Где уместно, ответ ссылается на файл и функцию.

- ORM против SQL;
- `Session` как unit of work и identity map;
- commit и rollback, кто управляет транзакцией;
- реализация M:N и зачем association object;
- ленивая загрузка, проблема N+1, `selectinload`;
- `AsyncSession` и `MissingGreenlet`;
- `ON DELETE CASCADE` в БД против `cascade` в ORM;
- ACID, уровень изоляции PostgreSQL по умолчанию;
- нормальные формы схемы;
- зачем pgvector, HNSW против IVFFlat;
- частичный уникальный индекс;
- `INSERT … ON CONFLICT`;
- **[ОПЦИОНАЛЬНО]** миграции и ограничения autogenerate.

### 8.2. `defense/lr1-cheatsheet.md` — шпаргалка (1 стр.)

- **Тезисы:** 10 таблиц; 2 связи M:N; репозитории без commit; `expire_on_commit=False`; upsert страниц; конфликт активной задачи OCR через частичный индекс.
- **Команды:** запуск БД, сид, демо, тесты, `psql` (`\dt`, `\d+`), **[ОПЦИОНАЛЬНО]** alembic.
- **Где в коде:** модели, M:N, сессия, `BaseRepository`, `nearest`, `upsert`, `create_active`, сид.
- **Частые ошибки:** `MissingGreenlet`, N+1, общая сессия в параллельных корутинах, безымянные constraint'ы.

## 9. Отчёт по лабе

Отчёт пишется **после** того, как пройдены разделы 5 и 6 и составлена подготовка к сдаче (раздел 8). Шаблон и правила — `docs/reports/_template.md`.
Коротко: вывод консоли только настоящий, скриншоты — заглушки со списком TODO, теория — по `defense/lr1.md`, не больше страницы.

| Раздел отчёта | Содержание для ЛР1 |
| --- | --- |
| 1. Цель и задание | Пункты методички; Alembic — «(доп. задание)», выполнено или нет |
| 2. Теория | ORM и Session (unit of work), связи 1:N и M:N, association object, асинхронный доступ, pgvector (тип и HNSW-индекс), миграции |
| 3.1 Реализовано | Таблица: пункт задания → модуль (`models.py`, `repositories/*`, `seed.py`, `demo_lr1.py`) |
| 3.2 Схемы | ER-диаграмма по фактическим моделям (`erDiagram` с атрибутами); диаграмма классов «BaseRepository → репозитории» |
| 3.3 Решения | Почему association object для авторов; почему статья ↔ страницы через диапазон; почему async; HNSW против IVFFlat |
| 4. Демонстрация | `docker compose ps`, `\dt` и `\d+ chunks` из psql, полный (сокращённый) вывод `demo_lr1.py` по 7 сценариям; [опц.] `alembic history`, `upgrade head`, `downgrade -1` |
| 5. Тестирование | Перечень тестов и вывод `pytest -q tests/db`, `ruff check` |
| Приложение А | Фрагменты `models.py` (Article, ArticleAuthor, Chunk), `repositories/articles.py`, `repositories/chunks.py` (`nearest`) |
| Приложение Б | `docker-compose.dev.yml`, `deploy/postgres/init.sql`, `.env.example`; [опц.] первая миграция Alembic |
| Скриншоты (TODO) | Схема БД из DBeaver или pgAdmin (по желанию) |

## 10. Промпты для Claude Code

### 10.1. Планирование (режим планирования)

```
Прочитай CLAUDE.md и plans/lr1.md. Мы в ветке lr1-db, репозиторий пустой (кроме CLAUDE.md, plans/, docs/).
Составь пошаговый план реализации ЛР1 строго по plans/lr1.md: структура файлов, порядок шагов,
что проверяем после каждого шага. Опциональную часть с Alembic ВКЛЮЧИ / НЕ включай (выбери).
Перед планом проверь актуальные версии sqlalchemy, asyncpg, pgvector (python) и их API для Vector и HNSW-индекса.
Код не пиши, пока я не одобрю план.
```

### 10.2. Подготовка к сдаче (после реализации, обычный режим)

```
Лаба 1 реализована и проверена (разделы 5–6 plans/lr1.md пройдены). Составь подготовку к сдаче по разделу 8 plans/lr1.md
и правилам CLAUDE.md («Документация по лабе»): defense/lr1.md (раздел 1 — сценарий сдачи на 5–7 мин с подготовкой и таймингом,
раздел 2 — минимальная теория на 2–3 стр., раздел 3 — вопросы преподавателя с ответами) и defense/lr1-cheatsheet.md
(1 стр.: тезисы, команды, таблица «Где в коде», частые ошибки).
Все команды из сценария и шпаргалки выполни сам; примеры вывода — только настоящие. Ссылки на код — реальные пути и функции.
Закоммить: docs(lr1): подготовка к сдаче.
```

### 10.3. Отчёт (после реализации, обычный режим)

```
Лаба 1 реализована и проверена. Составь отчёт docs/reports/lr1.md по шаблону docs/reports/_template.md
и разделу 9 plans/lr1.md. Теорию возьми из defense/lr1.md (раздел 2) и перескажи своими словами,
не больше страницы. Все команды из раздела «Демонстрация» запусти сам и вставь настоящий вывод (длинный сокращай с «…»);
что запустить нельзя — пометь TODO. Схемы — mermaid по фактическому коду. В конце — список TODO для меня (скриншоты и т. п.).
Закоммить: docs(lr1): отчёт.
```
