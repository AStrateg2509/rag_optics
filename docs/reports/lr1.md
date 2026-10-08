# Отчёт по лабораторной работе №1 «Проектирование БД и слой доступа к данным»

**Дисциплина:** Технологии сетевого программирования
**Выполнил:** Кравченко Иван Антонович, гр. 6402-010302D
**Проверил:** <ФИО преподавателя>
**Дата:** 09.10.2026
**Репозиторий:** <https://github.com/AStrateg2509/rag_optics>, ветка `lr1-db`, PR #<номер>

## 1. Цель и задание

**Цель:** спроектировать и реализовать слой приложения для работы с БД проекта CO-RAG (RAG-ассистент по архиву журнала «Компьютерная оптика») на PostgreSQL и SQLAlchemy.

**Задание:**

1. Создать и настроить БД PostgreSQL — выполнено (PostgreSQL 16 + pgvector в Docker).
2. По схеме из ЛР0 реализовать модели данных средствами SQLAlchemy; CRUD-операции для основных сущностей — выполнено.
3. Реализовать работу с БД под функционал проекта на основе моделей — выполнено (каталог, upsert страниц, векторный поиск, задачи OCR).
4. Подключиться к БД и заполнить её тестовыми данными — выполнено (идемпотентный сид).
5. Написать тестовый скрипт, который показывает работу с БД во всех сценариях функционала — выполнено (`scripts/demo_lr1.py`, 7 сценариев).
6. Настроить миграции через Alembic и показать работу с ними (доп. задание) — **выполнено** (2 ревизии, upgrade/downgrade).

## 2. Краткие теоретические сведения

Схема CO-RAG реляционная, нормализована до 3НФ. Связи 1:N (выпуск → страницы, статьи, задачи OCR; страница → чанки) задаются внешними ключами. Связи M:N (статья ↔ автор, статья ↔ ключевое слово) — таблицами-связками с составным первичным ключом. У связи статья ↔ автор есть собственный атрибут — порядок автора. Поэтому в ORM она оформлена как **association object**: отдельная модель `ArticleAuthor`, а не простая `secondary`-таблица.

SQLAlchemy реализует паттерн **Data Mapper**: модели — обычные классы, а сохранением управляет `Session`. Сессия — это **Unit of Work** (копит изменения и отправляет их при `flush` в правильном порядке) и **Identity Map** (одна строка — один объект в пределах сессии). Транзакцию в CO-RAG задаёт вызывающий код, репозитории только делают `flush`. PostgreSQL по умолчанию работает в режиме READ COMMITTED, поэтому бизнес-правила уникальности («одна активная задача OCR на выпуск») обеспечиваются ограничениями БД (частичный уникальный индекс), а не проверкой SELECT-ом.

Доступ к БД **асинхронный** (asyncpg, `AsyncSession`): сервисы проекта работают на event loop, и синхронный драйвер блокировал бы его. В async-коде ленивая загрузка связей невозможна (`MissingGreenlet`), поэтому связи грузятся явно: `selectinload`, `joinedload`, `contains_eager`. Это заодно устраняет проблему N+1. Сессии создаются с `expire_on_commit=False`.

Расширение **pgvector** добавляет тип `vector(n)` и операторы расстояния (`<=>` — косинусное). Индекс **HNSW** — многослойный граф для приближённого поиска ближайших соседей: поиск жадно спускается от редких верхних слоёв к полному нижнему. По сравнению с IVFFlat у него лучше соотношение скорость/полнота, и его можно строить на пустой таблице.

**Миграции Alembic** хранят историю схемы как цепочку ревизий с `upgrade`/`downgrade`. Autogenerate сравнивает модели с БД, но результат нужно вычитывать: расширения, удаление ENUM-типов и переименования он не обрабатывает. Подробнее — `defense/lr1.md`, раздел 2.

## 3. Ход выполнения

### 3.1. Что реализовано

Таблица 1 — Соответствие пунктов задания и реализации

| Пункт задания | Как реализовано в CO-RAG | Где в коде |
| --- | --- | --- |
| Создать и настроить БД | Контейнер `pgvector/pgvector:pg16`, том `pgdata`, healthcheck; init-скрипт включает `vector` и создаёт тестовую БД `corag_test` | `docker-compose.dev.yml`, `deploy/postgres/init.sql` |
| Модели по схеме ЛР0 | 10 таблиц, 3 ENUM, CHECK, FK с CASCADE/RESTRICT, индексы (B-tree, частичный уникальный, HNSW); `naming_convention` | `packages/corag-db/src/corag_db/models.py`, `base.py`, `enums.py` |
| Подключение | `create_async_engine` (asyncpg), `async_sessionmaker(expire_on_commit=False)`, `get_session()` как unit of work; настройки из `.env` | `corag_db/session.py`, `corag_db/settings.py` |
| CRUD | `BaseRepository[ModelT]`: `get`, `get_or_raise`, `list`, `add`, `update`, `delete` — для всех сущностей | `corag_db/repositories/base.py` |
| Работа под функционал | Каталог с фильтрами (Ф2), карточка статьи (Ф3), upsert страниц (А2), векторный поиск `nearest` (Ф4, Ф5), разметка статей (А5), пользователи (Ф1, А6), задачи OCR с конфликтом (А2) | `corag_db/repositories/*.py` |
| Тестовые данные | Идемпотентный сид: 2 пользователя, 3 выпуска, 20 страниц, 5 статей, 7 авторов, 9 ключевых слов, 40 чанков, 1 задача OCR; опционально тексты из PDF | `corag_db/seed.py`, `scripts/import_text_layer.py` |
| Тестовый скрипт | 7 сценариев, каждый в своей транзакции с откатом | `scripts/demo_lr1.py` |
| Автотесты | 22 теста репозиториев на `corag_test` + 6 тестов импорта PDF | `tests/db/`, `tests/scripts/` |
| Alembic (доп.) | Async-окружение, ревизии `0001 initial schema` и `0002 articles udc`, ruff как post-write hook | `packages/corag-db/alembic/` |

### 3.2. Схемы

```mermaid
erDiagram
    users ||--o{ issues : "created_by"
    users ||--o{ ocr_jobs : "requested_by"
    issues ||--o{ pages : "issue_id (CASCADE)"
    issues ||--o{ articles : "issue_id (CASCADE)"
    issues ||--o{ ocr_jobs : "issue_id (CASCADE)"
    pages ||--o{ chunks : "page_id (CASCADE)"
    articles ||--|{ article_authors : "article_id (CASCADE)"
    authors ||--o{ article_authors : "author_id (RESTRICT)"
    articles ||--o{ article_keywords : "article_id (CASCADE)"
    keywords ||--o{ article_keywords : "keyword_id (CASCADE)"

    users {
        bigint id PK
        varchar email UK
        varchar password_hash
        varchar full_name
        user_role role
        boolean is_active
        timestamptz created_at
    }
    issues {
        bigint id PK
        smallint year "CHECK >= 1987"
        smallint volume
        smallint number "UK (year, number)"
        text scan_path
        int page_count "CHECK > 0"
        issue_status status
        bigint created_by FK
        timestamptz created_at
    }
    pages {
        bigint id PK
        bigint issue_id FK
        int page_number "CHECK > 0, UK (issue_id, page_number)"
        text text_md
        varchar ocr_model
        timestamptz recognized_at
    }
    articles {
        bigint id PK
        bigint issue_id FK
        text title
        text abstract
        varchar doi UK
        char language
        int page_start "CHECK page_start <= page_end"
        int page_end
        varchar udc "миграция 0002"
        timestamptz created_at
    }
    authors {
        bigint id PK
        varchar full_name "ix_authors_full_name"
        varchar affiliation
        char orcid UK
    }
    article_authors {
        bigint article_id PK, FK
        bigint author_id PK, FK
        smallint author_order "CHECK > 0"
    }
    keywords {
        bigint id PK
        varchar value UK
    }
    article_keywords {
        bigint article_id PK, FK
        bigint keyword_id PK, FK
    }
    chunks {
        bigint id PK
        bigint page_id FK
        smallint chunk_index "UK (page_id, chunk_index)"
        text text
        int token_count
        vector embedding "vector(1024), HNSW cosine"
    }
    ocr_jobs {
        bigint id PK
        bigint issue_id FK "ux_ocr_jobs_active WHERE queued/running"
        bigint requested_by FK
        job_status status
        int pages_total
        int pages_done
        text error
        timestamptz created_at
        timestamptz started_at
        timestamptz finished_at
    }
```

Рисунок 1 — ER-диаграмма по фактическим моделям `corag_db/models.py` (статьи связаны со страницами диапазоном `page_start..page_end`, без FK)

```mermaid
classDiagram
    class BaseRepository~ModelT~ {
        +session: AsyncSession
        +get(id) ModelT
        +get_or_raise(id) ModelT
        +list(limit, offset)
        +add(obj) ModelT
        +update(obj, **fields) ModelT
        +delete(obj)
    }
    class UserRepository {
        +get_by_email(email)
        +create(email, password, full_name, role)
        +set_role(user, role)
        +set_active(user, is_active)
    }
    class IssueRepository {
        +list(limit, offset, year)
        +get_by_year_number(year, number)
        +set_status(issue, status)
        +get_with_counts(issue_id)
    }
    class PageRepository {
        +upsert(issue_id, page_number, text_md, ocr_model)
        +list_by_issue(issue_id)
        +get_by_number(issue_id, page_number)
    }
    class ArticleRepository {
        +filter(year, author, keyword, limit, offset)
        +get_full(article_id)
        +get_by_doi(doi)
        +create_with_relations(dto, author_ids, keywords)
        +set_authors(article, author_ids)
        +add_keywords(article, values)
        +pages_of(article)
    }
    class AuthorRepository {
        +search(q)
        +get_many(ids)
    }
    class KeywordRepository {
        +get_or_create_many(values)
    }
    class ChunkRepository {
        +replace_for_pages(page_ids, chunks)
        +nearest(query_vec, top_k, year_from, year_to)
    }
    class OcrJobRepository {
        +create_active(issue_id, user_id, pages_total)
        +mark_running(job_id)
        +inc_progress(job_id, n)
        +mark_done(job_id)
        +mark_failed(job_id, error)
    }
    BaseRepository <|-- UserRepository
    BaseRepository <|-- IssueRepository
    BaseRepository <|-- PageRepository
    BaseRepository <|-- ArticleRepository
    BaseRepository <|-- AuthorRepository
    BaseRepository <|-- KeywordRepository
    BaseRepository <|-- ChunkRepository
    BaseRepository <|-- OcrJobRepository
    ArticleRepository ..> AuthorRepository : get_many
    ArticleRepository ..> KeywordRepository : get_or_create_many
```

Рисунок 2 — Диаграмма классов репозиториев `corag_db/repositories`

### 3.3. Ключевые решения

- **Association object для авторов.** У связи есть атрибут `author_order`: порядок авторов статьи важен для цитирования. Простая `secondary`-таблица не даёт доступа к полю связи. Модель `ArticleAuthor` даёт, а `Article.set_authors` при смене порядка обновляет существующие связки на месте, без DELETE+INSERT с тем же PK.
- **Статья ↔ страницы через диапазон.** Страницы появляются после OCR, статьи размечаются администратором позже и независимо. Связь по `page_start..page_end` (составной индекс `ix_articles_issue_pages`) позволяет индексировать страницы сразу. Исправление диапазона не требует переиндексации. `nearest` находит статью LEFT JOIN-ом, у неразмеченных страниц источник пустой.
- **Асинхронный доступ.** API (FastAPI) и RAG (`grpc.aio`) работают на event loop, поэтому слой данных сразу сделан на asyncpg и `AsyncSession`. Это потребовало явной загрузки связей и `expire_on_commit=False`.
- **Правила целостности — в БД.** «Одна активная задача OCR» — частичный уникальный индекс, а не проверка в Python: так нет гонки при READ COMMITTED. Upsert страниц сделан через `ON CONFLICT`, каскадное удаление выпуска — через `ON DELETE CASCADE` (+ `passive_deletes=True` в ORM).
- **HNSW вместо IVFFlat.** HNSW строится на пустой таблице (индекс создаётся в первой миграции), лучше по полноте при той же скорости; IVFFlat требует данных для кластеризации. На 40 строках сида планировщик выбирает Seq Scan, при `enable_seqscan = off` — `Index Scan using ix_chunks_embedding` (проверено `EXPLAIN`).
- **SQLAlchemy 2.0.x, а не 2.1.** На момент работы 2.1 только вышла (24.09.2026) и меняет обработку ENUM-типов PostgreSQL. ТЗ фиксирует 2.0, поэтому версия ограничена `>=2.0.54,<2.1`. pgvector 0.5.0 больше не зависит от numpy и возвращает вектор как `list[float]`.

## 4. Демонстрация работы

Состояние контейнера:

```text
$ docker compose -f docker-compose.dev.yml ps
NAME                    IMAGE                    COMMAND                  SERVICE    CREATED         STATUS                   PORTS
rag_optics-postgres-1   pgvector/pgvector:pg16   "docker-entrypoint.s…"   postgres   7 seconds ago   Up 6 seconds (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp
```

Миграции и сид на чистой БД:

```text
$ cd packages/corag-db && uv run alembic upgrade head
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade  -> 0001, initial schema
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, articles udc

$ uv run python -m corag_db.seed
[seed] пользователи: admin@corag.local, researcher@corag.local
[seed] авторы: 7
[seed] выпуск 1998 №1: 7 стр., 14 чанков, 2 статей
[seed] выпуск 2015 №3: 7 стр., 14 чанков, 2 статей
[seed] выпуск 2024 №2: 6 стр., 12 чанков, 1 статей
[seed] задача OCR #1 для выпуска 1998: done
[seed] строк в таблицах: users=2, issues=3, pages=20, article_keywords=11, articles=5, authors=7, article_authors=9, keywords=9, chunks=40, ocr_jobs=1
```

Повторный запуск сида строк не добавляет (идемпотентность): последняя строка вывода совпадает, строки о задаче OCR нет.

Схема в psql:

```text
$ docker compose -f docker-compose.dev.yml exec postgres psql -U corag -d corag -c '\dt'
             List of relations
 Schema |       Name       | Type  | Owner
--------+------------------+-------+-------
 public | alembic_version  | table | corag
 public | article_authors  | table | corag
 public | article_keywords | table | corag
 public | articles         | table | corag
 public | authors          | table | corag
 public | chunks           | table | corag
 public | issues           | table | corag
 public | keywords         | table | corag
 public | ocr_jobs         | table | corag
 public | pages            | table | corag
 public | users            | table | corag
(11 rows)

$ docker compose -f docker-compose.dev.yml exec postgres psql -U corag -d corag -c '\d+ chunks'
                                                            Table "public.chunks"
   Column    |     Type     | Collation | Nullable |              Default               | Storage  | …
-------------+--------------+-----------+----------+------------------------------------+----------+ …
 id          | bigint       |           | not null | nextval('chunks_id_seq'::regclass) | plain    | …
 page_id     | bigint       |           | not null |                                    | plain    | …
 chunk_index | smallint     |           | not null |                                    | plain    | …
 text        | text         |           | not null |                                    | extended | …
 token_count | integer      |           |          |                                    | plain    | …
 embedding   | vector(1024) |           | not null |                                    | external | …
Indexes:
    "pk_chunks" PRIMARY KEY, btree (id)
    "ix_chunks_embedding" hnsw (embedding vector_cosine_ops) WITH (m='16', ef_construction='64')
    "uq_chunks_page_id_chunk_index" UNIQUE CONSTRAINT, btree (page_id, chunk_index)
Foreign-key constraints:
    "fk_chunks_page_id_pages" FOREIGN KEY (page_id) REFERENCES pages(id) ON DELETE CASCADE
Access method: heap
```

Частичный уникальный индекс (`\d ocr_jobs`, фрагмент):

```text
Indexes:
    "ux_ocr_jobs_active" UNIQUE, btree (issue_id) WHERE status = ANY (ARRAY['queued'::job_status, 'running'::job_status])
```

Демонстрационный скрипт (вывод сокращён):

```text
$ uv run python scripts/demo_lr1.py

==============================================================================
 1. Пользователи: создание, поиск по e-mail, смена роли, блокировка
==============================================================================
create          → id=3, role=researcher, is_active=True
get_by_email    → User(id=3, email='demo.user@corag.local', role=researcher)
create (дубль)  → Conflict: e-mail 'demo.user@corag.local' уже зарегистрирован
 id | email                  | роль       | активен
----+------------------------+------------+---------
 1  | admin@corag.local      | admin      | True
 2  | researcher@corag.local | researcher | True
 3  | demo.user@corag.local  | admin      | False

[транзакция откатана — данные БД не изменились]

==============================================================================
 2. CRUD выпуска и каскадное удаление страниц и статей
==============================================================================
create      → Issue(id=4, 2030 №1, status=created)
read        → Issue(id=4, 2030 №1, status=created): страниц=3, статей=1
set_status  → uploaded
delete      → выпуск удалён; осталось страниц=0, статей=0
              (ON DELETE CASCADE в БД, passive_deletes=True в ORM)
…
==============================================================================
 3. Статья ↔ авторы (порядок) и ключевые слова: M:N
==============================================================================

Создана: «Метаповерхности», стр. 5–6
 порядок | автор                    | организация
---------+--------------------------+---------------------------------------------------------
 1       | Кузнецов Андрей Петрович | Самарский университет
 2       | Орлова Мария Сергеевна   | ИСОИ РАН — филиал ФНИЦ «Кристаллография и фотоника» РАН
 ключевые слова: метаповерхности, поляризация

После смены порядка авторов и добавления слова: «Метаповерхности», стр. 5–6
 порядок | автор                    | организация
---------+--------------------------+---------------------------------------------------------
 1       | Орлова Мария Сергеевна   | ИСОИ РАН — филиал ФНИЦ «Кристаллография и фотоника» РАН
 2       | Кузнецов Андрей Петрович | Самарский университет
 ключевые слова: метаповерхности, нанофотоника, поляризация
 страницы статьи (по диапазону): [5, 6]
…
==============================================================================
 4. Фильтры каталога статей: год, автор, ключевое слово
==============================================================================

filter(year=2015):
 id | выпуск  | стр. | название                                 | авторы
----+---------+------+------------------------------------------+----------------------------
 3  | 2015 №3 | 1–4  | Острая фокусировка вихревых лазерных пу… | Громова, Кузнецов, Соколов
 4  | 2015 №3 | 5–7  | Быстрые алгоритмы сверточной фильтрации… | Захарова

filter(author='кузнецов'):
 id | выпуск  | стр. | название                                 | авторы
----+---------+------+------------------------------------------+----------------------------
 3  | 2015 №3 | 1–4  | Острая фокусировка вихревых лазерных пу… | Громова, Кузнецов, Соколов
 1  | 1998 №1 | 1–3  | Расчёт дифракционного оптического элеме… | Кузнецов, Орлова

filter(keyword='Дифракционная оптика'):
 id | выпуск  | стр. | название                                 | авторы
----+---------+------+------------------------------------------+------------------
 1  | 1998 №1 | 1–3  | Расчёт дифракционного оптического элеме… | Кузнецов, Орлова
 2  | 1998 №1 | 4–6  | Итеративный алгоритм расчёта фазовых пл… | Беляев
…
==============================================================================
 5. Upsert страницы: INSERT … ON CONFLICT DO UPDATE
==============================================================================
до:     id=15, страниц в выпуске=6, текст='# Классификация гиперспектральных изображений П…'
после:  id=15, страниц в выпуске=6, текст='# Повторный OCR Уточнённый текст.'
        ocr_model=deepseek-ocr, recognized_at=21:20:12
…
==============================================================================
 6. Векторный поиск nearest(): top-5 по косинусному расстоянию
==============================================================================

Случайный запрос, все годы:
 score   | выпуск  | стр. | статья                         | фрагмент
---------+---------+------+--------------------------------+------------------------------------
 +0.0636 | 2024 №2 | 4    | Нейросетевая классификация ги… | Точность классификации составила …
 +0.0621 | 2015 №3 | 5    | Быстрые алгоритмы сверточной … | Рассматриваются быстрые алгоритмы…
 +0.0599 | 1998 №1 | 4    | Итеративный алгоритм расчёта … | # Итеративный алгоритм расчёта фа…
 +0.0474 | 1998 №1 | 6    | Итеративный алгоритм расчёта … | Фазовая пластинка изготовлена мет…
 +0.0473 | 2024 №2 | 3    | Нейросетевая классификация ги… | Сеть обучалась на наборе Indian P…

Случайный запрос, year_from=2010:
 score   | выпуск  | стр. | статья                         | фрагмент
---------+---------+------+--------------------------------+------------------------------------
 +0.0636 | 2024 №2 | 4    | Нейросетевая классификация ги… | Точность классификации составила …
 +0.0621 | 2015 №3 | 5    | Быстрые алгоритмы сверточной … | Рассматриваются быстрые алгоритмы…
 +0.0473 | 2024 №2 | 3    | Нейросетевая классификация ги… | Сеть обучалась на наборе Indian P…
 +0.0467 | 2024 №2 | 1    | Нейросетевая классификация ги… | # Классификация гиперспектральных…
 +0.0187 | 2015 №3 | 3    | Острая фокусировка вихревых л… | ## Острая фокусировка
…
==============================================================================
 7. Задачи OCR: не более одной активной на выпуск
==============================================================================
create_active         → OcrJob(id=2, issue_id=2, status=queued)
create_active (2-я)   → Conflict: у выпуска 2 уже есть активная задача OCR  (API ответит 409)
mark_running + 3×inc  → status=running, 3/7
mark_done             → status=done, 7/7
create_active (снова) → OcrJob(id=4, issue_id=2, status=queued)

[транзакция откатана — данные БД не изменились]
```

Векторы чанков в сиде случайные, поэтому значения score около нуля и смысла не несут: сценарий 6 показывает механику запроса (сортировка по `<=>`, фильтр по году, источник через LEFT JOIN). Осмысленные эмбеддинги Jina появятся в ЛР3. У страницы без размеченной статьи источник выводится как «—» (тест `test_nearest_page_outside_article_has_no_source`).

Миграции Alembic (доп. задание), на БД с данными:

```text
$ cd packages/corag-db
$ uv run alembic history
0001 -> 0002 (head), articles udc
<base> -> 0001, initial schema
$ uv run alembic current
0002 (head)
$ uv run alembic downgrade -1
INFO  [alembic.runtime.migration] Running downgrade 0002 -> 0001, articles udc
$ uv run alembic current
0001
$ uv run alembic upgrade head
INFO  [alembic.runtime.migration] Running upgrade 0001 -> 0002, articles udc
$ uv run alembic current
0002 (head)
$ uv run alembic check
No new upgrade operations detected.
```

`alembic downgrade base` на чистой БД удаляет все таблицы и ENUM-типы: после него `\dT` показывает только типы расширения `vector`, `halfvec`, `sparsevec`.

## 5. Тестирование

Тесты работают на отдельной БД `corag_test`. Схема создаётся один раз за прогон (`create_schema`), каждый тест выполняется во внешней транзакции, которая откатывается (`join_transaction_mode="create_savepoint"`, `tests/db/conftest.py`).

Таблица 2 — Состав тестов

| Модуль | Что проверяет |
| --- | --- |
| `tests/db/test_base_repo.py` | CRUD через `BaseRepository` (выпуск, автор, ключевое слово), `NotFound`, неизвестное поле; пользователи (bcrypt, дубль e-mail → `Conflict`, роль, блокировка); каскадное удаление выпуска; CHECK `page_start <= page_end` и `year >= 1987` |
| `tests/db/test_m2m.py` | M:N в обе стороны: порядок авторов, смена порядка, нормализация и добавление ключевых слов, `Author.articles`, `Keyword.articles`; `ON DELETE RESTRICT` для автора со статьями |
| `tests/db/test_pages.py` | upsert страницы (один id, текст обновлён, дубля нет), выборка по выпуску и номеру |
| `tests/db/test_articles.py` | фильтры по году, автору, ключевому слову и их комбинации; пагинация; связи загружены заранее; страницы статьи по диапазону |
| `tests/db/test_chunks.py` | `nearest`: известный вектор первым со score ≈ 1, статья по диапазону, страница вне статьи без источника, фильтр по годам, `replace_for_pages` |
| `tests/db/test_ocr_jobs.py` | частичный уникальный индекс (вторая активная задача → `Conflict`, после `done`/`failed` — можно), жизненный цикл и атомарный `inc_progress` |
| `tests/scripts/test_import_text_layer.py` | извлечение текстового слоя из сгенерированного PDF, склейка переносов, диапазон и смещение страниц, запись JSON |

```text
$ uv run pytest -q tests/db
......................                                                   [100%]
22 passed in 1.05s

$ uv run pytest -q
28 passed in 1.40s

$ uv run ruff check .
All checks passed!
```

## 6. Выводы

Спроектирован и реализован слой доступа к данным CO-RAG: 10 таблиц PostgreSQL с ограничениями целостности и векторным HNSW-индексом, асинхронная сессия, репозитории с CRUD и запросами под все сценарии ТЗ, идемпотентный сид и демонстрационный скрипт. Изучены паттерны Data Mapper, Unit of Work и Identity Map, стратегии загрузки связей и особенности async-доступа (`MissingGreenlet`, одна сессия на корутину). Бизнес-правила целостности вынесены в БД: частичный уникальный индекс, `ON CONFLICT`, `ON DELETE CASCADE`/`RESTRICT`. Доп. задание выполнено: схема ведётся миграциями Alembic, upgrade и downgrade проверены. Ограничение: эмбеддинги в сиде случайные, настоящие появятся в ЛР3; тексты страниц — встроенные, импорт из PDF журнала подготовлен, но PDF пока не загружены.

## Приложение А. Листинги ключевых модулей

Листинг А.1 — `packages/corag-db/src/corag_db/models.py` (фрагмент: M:N статья ↔ автор)

```python
class Article(Base):
    ...
    # Association object: у связи есть своё поле author_order
    authors_assoc: Mapped[list["ArticleAuthor"]] = relationship(
        back_populates="article",
        order_by="ArticleAuthor.author_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    keywords: Mapped[list["Keyword"]] = relationship(
        secondary=article_keywords, back_populates="articles", order_by="Keyword.value"
    )


class ArticleAuthor(Base):
    """Связка M:N статья ↔ автор с порядком автора."""

    __tablename__ = "article_authors"
    __table_args__ = (CheckConstraint("author_order > 0", name="author_order_positive"),)

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="RESTRICT"), primary_key=True
    )
    author_order: Mapped[int] = mapped_column(SmallInteger)

    article: Mapped[Article] = relationship(back_populates="authors_assoc")
    author: Mapped[Author] = relationship(back_populates="articles_assoc")
```

Листинг А.2 — `packages/corag-db/src/corag_db/models.py` (фрагмент: чанк с вектором и HNSW, частичный индекс)

```python
class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("page_id", "chunk_index"),
        # Приближённый поиск ближайших соседей по косинусному расстоянию
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    ...
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))


# Не более одной активной задачи на выпуск (иначе API отвечает 409)
Index(
    "ux_ocr_jobs_active",
    OcrJob.issue_id,
    unique=True,
    postgresql_where=OcrJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
)
```

Листинг А.3 — `packages/corag-db/src/corag_db/repositories/chunks.py` (фрагмент `ChunkRepository.nearest`)

```python
        distance = Chunk.embedding.cosine_distance(query_vec)
        stmt = (
            select(
                Chunk.id,
                Chunk.text,
                Page.page_number,
                Issue.id,
                Issue.year,
                Issue.number,
                Article.id,
                Article.title,
                (1 - distance).label("score"),
            )
            .join(Page, Page.id == Chunk.page_id)
            .join(Issue, Issue.id == Page.issue_id)
            .outerjoin(
                Article,
                and_(
                    Article.issue_id == Page.issue_id,
                    Page.page_number.between(Article.page_start, Article.page_end),
                ),
            )
            .order_by(distance)
            .limit(top_k)
        )
        if year_from is not None:
            stmt = stmt.where(Issue.year >= year_from)
        ...
        rows = await self.session.execute(stmt)
        return [ChunkHit(*row) for row in rows]
```

Листинг А.4 — `packages/corag-db/src/corag_db/repositories/ocr_jobs.py` (фрагмент `OcrJobRepository.create_active`)

```python
        job = OcrJob(
            issue_id=issue_id,
            requested_by=user_id,
            status=JobStatus.QUEUED,
            pages_total=pages_total,
        )
        try:
            # SAVEPOINT: ошибка откатывает только эту вставку, а не всю транзакцию вызывающего
            async with self.session.begin_nested():
                self.session.add(job)
        except IntegrityError as exc:
            if ACTIVE_INDEX in str(exc.orig):
                raise Conflict(f"у выпуска {issue_id} уже есть активная задача OCR") from exc
            raise
        return job
```

## Приложение Б. Конфигурационные файлы

Листинг Б.1 — `docker-compose.dev.yml`

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${POSTGRES_PORT:-5432}:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
      - ./deploy/postgres/init.sql:/docker-entrypoint-initdb.d/init.sql:ro
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $${POSTGRES_USER} -d $${POSTGRES_DB}"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  pgdata:
```

Листинг Б.2 — `deploy/postgres/init.sql`

```sql
CREATE EXTENSION IF NOT EXISTS vector;

-- Отдельная БД для pytest
CREATE DATABASE corag_test;
\c corag_test
CREATE EXTENSION IF NOT EXISTS vector;
```

Листинг Б.3 — `.env.example` (фрагмент)

```ini
POSTGRES_USER=corag
POSTGRES_PASSWORD=corag
POSTGRES_DB=corag
POSTGRES_PORT=5432
DATABASE_URL=postgresql+asyncpg://corag:corag@localhost:5432/corag
TEST_DATABASE_URL=postgresql+asyncpg://corag:corag@localhost:5432/corag_test
ECHO_SQL=false
EMBED_DIM=1024
SEED_ADMIN_PASSWORD=admin12345
SEED_RESEARCHER_PASSWORD=researcher12345
SEED_TEXT_LAYER_DIR=data/text_layer
```

Листинг Б.4 — `packages/corag-db/alembic/versions/0001_initial_schema.py` (фрагмент; вручную добавленные строки отмечены комментариями)

```python
def upgrade() -> None:
    """Upgrade schema."""
    # Вручную: тип vector нужен до создания таблицы chunks
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # ### commands auto generated by Alembic - please adjust! ###
    ...
    op.create_index(
        "ix_chunks_embedding",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    """Downgrade schema."""
    ...
    op.drop_table("authors")
    # ### end Alembic commands ###
    # Вручную: autogenerate не удаляет ENUM-типы, созданные вместе с таблицами
    for enum_name in ("job_status", "issue_status", "user_role"):
        sa.Enum(name=enum_name).drop(op.get_bind(), checkfirst=True)
```

---

**TODO для автора перед сдачей:**

- [ ] вписать ФИО преподавателя (шапка) и проверить дату;
- [ ] вписать номер PR (шапка) после создания PR `lr1-db → main`;
- [ ] (по желанию) скриншот схемы БД из DBeaver или pgAdmin: `![Схема БД в DBeaver](img/lr1/dbeaver_schema.png)` — подключение `localhost:5432`, БД `corag`, пользователь `corag`;
- [ ] пройти репетицию по `defense/lr1.md`, раздел 1.
