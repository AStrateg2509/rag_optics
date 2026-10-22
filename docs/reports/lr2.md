# Отчёт по лабораторной работе №2 «Разработка REST API»

**Дисциплина:** Технологии сетевого программирования
**Выполнил:** Кравченко Иван Антонович, гр. 6402-010302D
**Проверил:** Кашапов Артем Ильясович
**Дата:** 22.10.2026
**Репозиторий:** <https://github.com/AStrateg2509/rag_optics>, ветка `lr2-rest-api`, PR #<номер>

## 1. Цель и задание

**Цель:** реализовать REST API проекта CO-RAG (RAG-ассистент по архиву журнала «Компьютерная оптика»), через который вся работа с БД ведётся HTTP-запросами.

**Задание:**

1. Регистрация и аутентификация пользователей — выполнено (`/auth/register`, `/auth/login`).
2. CRUD над объектами БД через REST — выполнено (выпуски, статьи с авторами и ключевыми словами, авторы, ключевые слова, пользователи).
3. Прочие запросы к БД по функционалу проекта — выполнено (фильтры каталога, текст страницы, загрузка PDF-скана, запуск OCR, статус задачи).
4. Ответы в формате JSON — выполнено, включая ошибки (`{"detail": ...}`).
5. API Service на FastAPI; сервисы и репозитории подключаются через Dependency Injection (`Depends`) — выполнено.
6. Модели HTTP (DTO) отделены от моделей слоя данных (ORM) — выполнено.
7. Nginx как reverse proxy: отдаёт статику и проксирует запросы на бэкенд (доп. задание №1) — **выполнено**.
8. Аутентификация и авторизация через JWT (доп. задание №2) — **выполнено**.

## 2. Краткие теоретические сведения

**REST** — архитектурный стиль, в котором сервер предоставляет ресурсы по URI (`/issues/12`, `/articles?author=Орлова`), а клиент работает с ними единым набором методов HTTP: GET читает, POST создаёт, PATCH частично изменяет, DELETE удаляет. Сервер **не хранит состояние** клиента: каждый запрос несёт JWT в заголовке `Authorization`. **Идемпотентный** метод при повторе оставляет сервер в том же состоянии; это GET, PUT и DELETE, но не POST. Поэтому повторный `POST /issues/{id}/ocr` получает 409. Код ответа передаёт смысл результата:

- 201 — ресурс создан;
- 202 — запрос принят, работа будет выполнена позже (OCR);
- 401 — клиент не аутентифицирован (с заголовком `WWW-Authenticate`);
- 403 — недостаточно прав;
- 409 — конфликт с текущим состоянием;
- 413 — тело слишком большое;
- 422 — данные недопустимы.

**WSGI** — синхронный интерфейс Python-приложений: запрос занимает поток до ответа. **ASGI** — асинхронный: один процесс на event loop обслуживает множество запросов, пока они ждут БД. FastAPI — ASGI-приложение. Uvicorn — ASGI-сервер. Gunicorn — менеджер процессов, который запускает несколько воркеров `UvicornWorker` и перезапускает упавшие. uWSGI из методички — WSGI-сервер, асинхронное приложение он не запустит.

**Dependency Injection в FastAPI**: параметр `Depends(f)` объявляет, что перед обработчиком нужно вызвать `f`. Зависимости образуют граф (сервис → репозиторий → сессия) и кешируются на время запроса, поэтому все репозитории одного запроса работают в одной транзакции. Зависимость с `yield` (`get_session`) открывает сессию и после обработчика делает commit или rollback. **Pydantic** валидирует вход по аннотациям DTO, ошибка → 422. **DTO отделены от ORM**: наружу уходят только объявленные поля (`UserOut` без `password_hash`). FastAPI строит из аннотаций схему OpenAPI (`/docs`).

**Аутентификация** проверяет личность (пароль сверяется с bcrypt-хешем), **авторизация** — права (роль). **JWT** состоит из трёх частей base64url — header, payload (`sub`, `role`, `iat`, `exp`) и подписи HMAC-SHA256 (HS256) общим секретом. Payload читается кем угодно, но изменить его без секрета нельзя. В отличие от HS256, RS256 подписывает закрытым ключом и проверяет открытым. JWT не отзывается до истечения срока, поэтому срок короткий (30 мин), а блокировка проверяется по БД при каждом запросе.

**Reverse proxy** (Nginx) принимает запросы от имени бэкендов: раздаёт статику, проксирует `/api/` на API, ограничивает размер тела (`client_max_body_size`) и передаёт адрес клиента в `X-Forwarded-For`. Подробнее — `defense/lr2.md`, раздел 2.

## 3. Ход выполнения

### 3.1. Что реализовано

Таблица 1 — Соответствие пунктов задания и реализации

| Пункт задания | Как реализовано в CO-RAG | Где в коде |
| --- | --- | --- |
| Регистрация и аутентификация | Регистрация с ролью `researcher`; вход по OAuth2 Password (form `username`/`password`) → JWT; bcrypt из `corag-db` | `services/api/src/corag_api/routers/auth.py`, `services/auth_service.py` |
| Авторизация (роли) | `get_current_user` (Bearer → `decode_token` → пользователь из БД → `is_active`), фабрика `require_role`: admin ⊇ researcher | `corag_api/auth/dependencies.py` |
| CRUD через REST | Выпуски, статьи (авторы по порядку, ключевые слова), авторы, ключевые слова, пользователи (admin: роль, блокировка); `Paginated[T]` для списков | `corag_api/routers/*.py`, `corag_api/services/*.py` |
| Прочие запросы | Фильтры каталога (`year`, `author`, `keyword`, `issue_id`), текст страницы, загрузка PDF-скана, запуск OCR (202), статус задачи, `/health` | `routers/articles.py`, `routers/issues.py`, `routers/pages.py`, `routers/ocr_jobs.py` |
| JSON | Pydantic-DTO в ответах; ошибки — единый формат `{"detail": ...}` через exception handlers | `corag_api/schemas/`, `corag_api/errors.py` |
| FastAPI + DI | `create_app()` с lifespan (engine и sessionmaker в `app.state`); `Depends`: сессия → репозитории → сервисы | `corag_api/main.py`, `corag_api/deps.py` |
| DTO ≠ ORM | Create/Update/Out-схемы; `PatchModel` для PATCH; `from_attributes` для ответов | `corag_api/schemas/*.py` |
| Gunicorn | `gunicorn 'corag_api.main:create_app()' -k uvicorn_worker.UvicornWorker -w 2` | `corag_api/main.py` (docstring) |
| Nginx (доп.) | Статика на `/`, прокси `/api/` → API в WSL, `client_max_body_size 210m`, заготовка `/ws` | `deploy/nginx/nginx.conf`, `docker-compose.dev.yml` |
| JWT (доп.) | HS256, `sub`/`role`/`iat`/`exp`, 30 мин; общий модуль для API и Notification Service (ЛР4) | `packages/corag-contracts/src/corag_contracts/auth.py` |
| Демонстрация | Сценарий curl + jq; Postman-коллекция (папки Auth/Issues/Articles/OCR) | `scripts/demo_lr2.sh`, `docs/postman/co-rag.postman_collection.json` |

Таблица 2 — Эндпоинты API (по `/openapi.json`; коды — объявленные в схеме)

| Метод и путь | Назначение | Доступ | Коды ответов |
| --- | --- | --- | --- |
| `POST /auth/register` | Регистрация (роль researcher) | все | 201, 409, 422 |
| `POST /auth/login` | Вход: e-mail (username) и пароль → JWT | все | 200, 401, 403, 422 |
| `GET /users/me` | Текущий пользователь | авторизован | 200, 401, 403 |
| `GET /users` | Список пользователей | admin | 200, 401, 403, 422 |
| `PATCH /users/{user_id}` | Роль и блокировка пользователя | admin | 200, 401, 403, 404, 409, 422 |
| `GET /issues` | Каталог выпусков (`year`, `limit`, `offset`) | researcher+ | 200, 401, 403, 422 |
| `POST /issues` | Создать выпуск | admin | 201, 401, 403, 409, 422 |
| `GET /issues/{issue_id}` | Карточка выпуска с числом страниц, статей и чанков | researcher+ | 200, 401, 403, 404, 422 |
| `PATCH /issues/{issue_id}` | Изменить выпуск | admin | 200, 401, 403, 404, 409, 422 |
| `DELETE /issues/{issue_id}` | Удалить выпуск со страницами и статьями | admin | 204, 401, 403, 404, 409, 422 |
| `POST /issues/{issue_id}/scan` | Загрузить PDF-скан (multipart) | admin | 200, 401, 403, 404, 409, 413, 422 |
| `GET /issues/{issue_id}/pages/{page_number}` | Текст страницы выпуска | researcher+ | 200, 401, 403, 404, 422 |
| `POST /issues/{issue_id}/ocr` | Запустить OCR: задача ставится в очередь | admin | 202, 401, 403, 404, 409, 422 |
| `GET /ocr-jobs/{job_id}` | Статус задачи OCR | admin | 200, 401, 403, 404, 422 |
| `GET /articles` | Каталог статей (`year`, `author`, `keyword`, `issue_id`) | researcher+ | 200, 401, 403, 422 |
| `POST /articles` | Создать статью с авторами и ключевыми словами | admin | 201, 401, 403, 409, 422 |
| `GET /articles/{article_id}` | Карточка статьи: авторы, ключевые слова, текст страниц | researcher+ | 200, 401, 403, 404, 422 |
| `PATCH /articles/{article_id}` | Изменить статью | admin | 200, 401, 403, 404, 409, 422 |
| `DELETE /articles/{article_id}` | Удалить статью | admin | 204, 401, 403, 404, 422 |
| `GET /authors` | Поиск авторов по ФИО (`q`) | researcher+ | 200, 401, 403, 422 |
| `POST /authors` | Создать автора | admin | 201, 401, 403, 409, 422 |
| `GET /authors/{author_id}` | Автор | researcher+ | 200, 401, 403, 404, 422 |
| `PATCH /authors/{author_id}` | Изменить автора | admin | 200, 401, 403, 404, 409, 422 |
| `DELETE /authors/{author_id}` | Удалить автора (409, если указан в статьях) | admin | 204, 401, 403, 404, 409, 422 |
| `GET /keywords` | Ключевые слова (`q`) | researcher+ | 200, 401, 403, 422 |
| `POST /keywords` | Создать ключевое слово | admin | 201, 401, 403, 409, 422 |
| `DELETE /keywords/{keyword_id}` | Удалить ключевое слово | admin | 204, 401, 403, 404, 422 |
| `GET /health` | Живость сервиса и доступность БД | все | 200, 503 |

### 3.2. Схемы

```mermaid
classDiagram
    direction LR
    class ArticlesRouter {
        +list_articles(filters, page) Paginated~ArticleOut~
        +get_article(article_id) ArticleDetailOut
        +create_article(ArticleCreate) ArticleOut
        +update_article(article_id, ArticleUpdate) ArticleOut
        +delete_article(article_id)
    }
    class ArticleService {
        +search(filters, limit, offset)
        +get(article_id)
        +create(ArticleCreate) Article
        +update(article_id, ArticleUpdate) Article
        +delete(article_id)
        -_check_range(issue, start, end)
    }
    class ArticleRepository {
        +filter(year, author, keyword, issue_id)
        +count_filtered(year, author, keyword, issue_id)
        +get_full(article_id)
        +create_with_relations(fields, author_ids, keywords)
        +set_authors(article, author_ids)
        +set_keywords(article, values)
    }
    class IssueRepository {
        +get(issue_id)
    }
    class AsyncSession {
        <<сессия запроса get_session>>
        +commit()
        +rollback()
    }
    class Article {
        <<ORM>>
        id, issue_id, title, page_start, page_end
    }
    class ArticleCreate {
        <<Pydantic DTO>>
        issue_id, title, page_start, page_end, author_ids, keywords
    }
    class ArticleOut {
        <<Pydantic DTO>>
        id, title, issue, authors, keywords
    }
    ArticlesRouter --> ArticleService : Depends
    ArticleService --> ArticleRepository : Depends
    ArticleService --> IssueRepository : Depends
    ArticleRepository --> AsyncSession : Depends
    IssueRepository --> AsyncSession : Depends
    ArticleRepository --> Article
    ArticlesRouter ..> ArticleCreate
    ArticlesRouter ..> ArticleOut
```

Рисунок 1 — Слои API на примере статей: функции роутера `routers/articles.py` → `ArticleService` → репозитории `corag-db` → ORM; зависимости собираются через `Depends` в `deps.py`, сессия запроса — `get_session` (`scope="function"`)

```mermaid
sequenceDiagram
    participant C as Клиент (Postman / curl)
    participant R as Роутер FastAPI
    participant D as get_current_user / require_role
    participant A as AuthService
    participant DB as PostgreSQL
    C->>R: POST /auth/login (username, password)
    R->>A: login(email, password)
    A->>DB: SELECT users WHERE email = …
    A->>A: bcrypt.checkpw, is_active
    A-->>C: 200 {access_token: JWT(sub, role, iat, exp), expires_in: 1800}
    C->>R: POST /issues, Authorization: Bearer JWT
    R->>D: зависимости маршрута
    D->>D: decode_token: подпись HS256, exp
    alt токен подделан или истёк
        D-->>C: 401 + WWW-Authenticate: Bearer
    end
    D->>DB: SELECT users WHERE id = sub
    alt is_active = false или роль ниже admin
        D-->>C: 403 {"detail": …}
    end
    D->>R: пользователь admin
    R->>R: IssueService.create → flush → commit (get_session)
    R-->>C: 201 IssueOut
```

Рисунок 2 — Вход, запрос с токеном и проверка роли

```mermaid
sequenceDiagram
    participant C as Администратор
    participant S as OcrService.start
    participant DB as PostgreSQL
    participant P as JobPublisher (LoggingPublisher → AMQP в ЛР4)
    C->>S: POST /issues/{id}/ocr
    S->>DB: статус выпуска ∈ {uploaded, ocr_done, failed}?
    S->>DB: INSERT ocr_jobs (queued) — SAVEPOINT, частичный уникальный индекс
    S->>DB: UPDATE issues SET status = ocr_in_progress
    S->>DB: COMMIT
    S->>P: publish ocr.jobs {job_id, issue_id, scan_path, requested_by}
    S-->>C: 202 OcrJobOut (queued)
```

Рисунок 3 — Запуск OCR: публикация задачи строго после commit

```mermaid
flowchart LR
    B[Браузер / curl / Postman] -->|":80"| N["Nginx (контейнер)<br/>client_max_body_size 210m"]
    N -->|"/ → static/index.html"| ST[(deploy/nginx/static)]
    N -->|"/api/… → host.docker.internal:8000/…"| G["Gunicorn (WSL)<br/>2 × UvicornWorker"]
    G --> API["FastAPI create_app()"]
    API -->|asyncpg| PG[("PostgreSQL 16 + pgvector<br/>(контейнер)")]
    N -.->|"/ws — ЛР4"| WS[Notification Service]
```

Рисунок 4 — Nginx как reverse proxy перед API (доп. задание)

### 3.3. Ключевые решения

- **Публикация задачи после commit.** БД и брокер не имеют общей транзакции. Если опубликовать задачу до commit, OCR Worker может её получить, а транзакция ещё не закончена или откатится. `OcrService.start` делает явный `session.commit()` и только потом `publisher.publish_job(...)` (рис. 3), порядок закреплён тестом `test_publish_after_commit`. Брокер пока заменён `LoggingPublisher`, в ЛР4 его место займёт AMQP-публикатор с тем же протоколом `JobPublisher`.
- **202 для OCR.** Распознавание выпуска — минуты работы GPU. API лишь ставит задачу `queued` и сразу отвечает `202 Accepted` с телом задачи. Статус — `GET /ocr-jobs/{id}`, в ЛР4 — события по WebSocket. Вторая активная задача невозможна: её не допускает частичный уникальный индекс из ЛР1 (→ 409).
- **Потоковая запись скана.** `UploadFile` целиком в память не читается. Сначала проверяются сигнатура `%PDF-` и заявленный размер, затем файл копируется порциями по 1 МиБ через `anyio.open_file` в `*.part` с подсчётом байт (больше `MAX_SCAN_MB` → 413). PyMuPDF считает страницы в пуле потоков, после чего файл атомарно переименовывается в `SCANS_DIR/{год}-{номер}.pdf`. `scan_path` хранится относительно `SCANS_DIR` (общий том с OCR Worker), так же переделан сид ЛР1.
- **Commit до отправки ответа.** В FastAPI 0.160 код после `yield` у зависимости по умолчанию выполняется после отправки ответа. Тогда клиент получил бы 201 раньше фиксации и не узнал бы об ошибке commit. Поэтому `Depends(get_session, scope="function")`.
- **Формат ошибок.** Сервисы бросают доменные исключения (`NotFound`, `Conflict` из `corag-db`; `AuthFailed`, `Forbidden`, `ValidationFailed`, `PayloadTooLarge` из `errors.py`), а exception handlers переводят их в 401/403/404/409/413/422 с телом `{"detail": "..."}`. 401 всегда несёт `WWW-Authenticate: Bearer`. Ошибки Pydantic остаются стандартными 422 со списком полей. `IntegrityError`, проскочивший мимо проверок (гонка запросов), превращается в 409.
- **Роль — из БД, а не из токена.** `get_current_user` после проверки подписи загружает пользователя. Блокировка и смена роли действуют сразу, без ожидания истечения токена (тест `test_blocked_user_403`).
- **JWT в общем пакете.** `create_access_token`/`decode_token` лежат в `corag_contracts.auth`: в ЛР4 Notification Service проверит токен WebSocket тем же кодом, не завися от пакета API. Алгоритм при проверке задан жёстко (`algorithms=["HS256"]`), `alg=none` отвергается.
- **Отличия от плана.** Реализация `auth/basic.py` (HTTP Basic) не делалась: при включённом JWT она была бы вторым, неиспользуемым путём. Модуль `auth/passwords.py` не нужен, используется готовый `corag_db.passwords`. Тип страницы списка назван `Paginated[T]`, чтобы не путать с ORM-моделью `Page`. Вместо устаревшего `uvicorn.workers.UvicornWorker` используется пакет `uvicorn-worker`. У Gunicorn 26 нет флага `--factory`, фабрика вызывается синтаксисом `'corag_api.main:create_app()'`.

## 4. Демонстрация работы

Инфраструктура и запуск API под Gunicorn (терминал 1):

```text
$ docker compose -f docker-compose.dev.yml ps
NAME                    IMAGE                    COMMAND                  SERVICE    CREATED          STATUS                    PORTS
rag_optics-nginx-1      nginx:stable-alpine      "/docker-entrypoint.…"   nginx      10 minutes ago   Up 10 minutes             0.0.0.0:80->80/tcp, [::]:80->80/tcp
rag_optics-postgres-1   pgvector/pgvector:pg16   "docker-entrypoint.s…"   postgres   13 days ago      Up 22 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp

$ uv run --package corag-api gunicorn 'corag_api.main:create_app()' \
    -k uvicorn_worker.UvicornWorker -w 2 -b 0.0.0.0:8000 --access-logfile -
[2026-10-22 21:58:21 +0400] [31263] [INFO] Starting gunicorn 26.2.0
[2026-10-22 21:58:21 +0400] [31263] [INFO] Listening at: http://0.0.0.0:8000 (31263)
[2026-10-22 21:58:21 +0400] [31263] [INFO] Using worker: uvicorn_worker.UvicornWorker
[2026-10-22 21:58:21 +0400] [31268] [INFO] Booting worker with pid: 31268
[2026-10-22 21:58:21 +0400] [31281] [INFO] Booting worker with pid: 31281
[2026-10-22 21:58:22 +0400] [31268] [INFO] Started server process [31268]
[2026-10-22 21:58:22 +0400] [31268] [INFO] Waiting for application startup.
2026-10-22 21:58:22,325 INFO corag_api [api] старт: БД postgresql+asyncpg://corag:***@localhost:5432/corag
[2026-10-22 21:58:22 +0400] [31268] [INFO] Application startup complete.
[2026-10-22 21:58:22 +0400] [31281] [INFO] Started server process [31281]
[2026-10-22 21:58:22 +0400] [31281] [INFO] Waiting for application startup.
2026-10-22 21:58:22,346 INFO corag_api [api] старт: БД postgresql+asyncpg://corag:***@localhost:5432/corag
[2026-10-22 21:58:22 +0400] [31281] [INFO] Application startup complete.
```

Сценарий `bash scripts/demo_lr2.sh` (терминал 2; 18 шагов, 1,3 с, вывод сокращён):

```text
== 1. Регистрация исследователя (роль назначается сервером) и повтор → 409
→ POST /auth/register
← 201
{
  "id": 16,
  "email": "demo.1792691902@example.com",
  "full_name": "Демо Исследователь",
  "role": "researcher",
  "is_active": true,
  "created_at": "2026-10-22T17:58:22.466379Z"
}
→ POST /auth/register
← 409
{
  "detail": "e-mail 'demo.1792691902@example.com' уже зарегистрирован"
}

== 2. Вход (OAuth2 Password, form-urlencoded) → JWT
→ POST /auth/login
← 200
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxNiIsInJvbGUiOiJyZXNlYXJjaGVyIiwiaWF0IjoxNzkyNjkxOTAyLCJleHAiOjE3OTI2OTM3MDJ9.jxWJNxIkS_uERW2ZxtKJXAPg35sWBbYLvXkifo3MpV0",
  "token_type": "bearer",
  "expires_in": 1800
}
payload: {"sub":"16","role":"researcher","iat":1792691902,"exp":1792693702}

== 4. Без токена → 401 + WWW-Authenticate
→ GET /issues
← 401
{
  "detail": "требуется аутентификация: Authorization: Bearer <token>"
}
www-authenticate: Bearer

== 5. Исследователь создаёт выпуск → 403
→ POST /issues
← 403
{
  "detail": "недостаточно прав: нужна роль admin"
}
…
== 9. Загрузка PDF-скана (multipart) → статус uploaded, число страниц
/home/dockhub/rag_optics/data/demo/scan.pdf: 8 стр., 4 КБ
→ POST /issues/19/scan
← 200
{
  "id": 19,
  "year": 2026,
  "volume": 50,
  "number": 104,
  "scan_path": "2026-104.pdf",
  "page_count": 8,
  "status": "uploaded",
  …
}
не PDF:
→ POST /issues/19/scan
← 422
{
  "detail": "файл не является PDF (нет сигнатуры %PDF-)"
}
…
== 11. Статья с авторами (порядок) и ключевыми словами → 201
→ POST /articles
← 201
{
  "id": 26,
  "issue_id": 19,
  "title": "Демо: расчёт дифракционного элемента",
  "page_start": 2,
  "page_end": 5,
  …
  "authors": [
    {
      "id": 8,
      "full_name": "Демо Автор Петрович"
    },
    {
      "id": 2,
      "full_name": "Орлова Мария Сергеевна"
    }
  ],
  "keywords": [
    "дифракция",
    "доэ"
  ]
}

== 12. Диапазон страниц за пределами выпуска (8 стр.) → 422
→ POST /articles
← 422
{
  "detail": "page_end (20) больше числа страниц выпуска (8)"
}

== 13. Фильтры каталога: автор + ключевое слово, выпуск
→ GET /articles?author=%D0%9E%D1%80%D0%BB%D0%BE%D0%B2%D0%B0&keyword=%D0%B4%D0%BE%D1%8D
← 200
…
найдено: [{"id":26,"title":"Демо: расчёт дифракционного элемента","authors":["Демо Автор Петрович","Орлова Мария Сергеевна"]}]

== 14. Исправление статьи (PATCH) → 200
…
keywords: ["голография","доэ"], page_end: 6

== 15. Запуск OCR → 202 Accepted (задача в очереди), повтор → 409
→ POST /issues/19/ocr
← 202
{
  "id": 29,
  "issue_id": 19,
  "requested_by": 1,
  "status": "queued",
  "pages_done": 0,
  "pages_total": 8,
  …
}
→ POST /issues/19/ocr
← 409
{
  "detail": "выпуск 19 в статусе ocr_in_progress: OCR запускается из uploaded, ocr_done или failed"
}

== 16. Статус задачи OCR; исследователю — 403
→ GET /ocr-jobs/29
← 200
…
→ GET /ocr-jobs/29
← 403
…
== 18. Удаление статьи → 204, затем 404
→ DELETE /articles/26
← 204
→ GET /articles/26
← 404
{
  "detail": "Article 26 не найден"
}

Демо ЛР2 пройдено: выпуск 19 (2026 №104), задача OCR 29
```

Строки лога API (терминал 1) за этот же прогон. Видно, что задача сначала фиксируется в БД, а затем публикуется:

```text
2026-10-22 21:58:22,664 INFO corag_api [api] register user_id=16 email=demo.1792691902@example.com role=researcher
2026-10-22 21:58:22,684 INFO corag_api [api] POST /auth/register → 409: e-mail 'demo.1792691902@example.com' уже зарегистрирован
2026-10-22 21:58:22,883 INFO corag_api [api] login user_id=16 role=researcher → JWT на 30 мин
2026-10-22 21:58:22,918 INFO corag_api [api] GET /issues → 401: требуется аутентификация: Authorization: Bearer <token>
…
2026-10-22 21:58:22,939 INFO corag_api [api] POST /issues → 403: недостаточно прав: нужна роль admin
2026-10-22 21:58:23,198 INFO corag_api [api] login user_id=1 role=admin → JWT на 30 мин
2026-10-22 21:58:23,231 INFO corag_api [api] issue_id=19 создан: 2026 №104
2026-10-22 21:58:23,407 INFO corag_api [api] scan issue_id=19 → data/scans/2026-104.pdf: 4 КБ, 8 стр., статус uploaded
2026-10-22 21:58:23,424 INFO corag_api [api] POST /issues/19/scan → 422: файл не является PDF (нет сигнатуры %PDF-)
2026-10-22 21:58:23,487 INFO corag_api [api] article_id=26 создана: issue_id=19, стр. 2–5, авторы [8, 2], слова ['дифракция', 'доэ']
2026-10-22 21:58:23,509 INFO corag_api [api] POST /articles → 422: page_end (20) больше числа страниц выпуска (8)
2026-10-22 21:58:23,574 INFO corag_api [api] article_id=26 изменена: ['keywords', 'page_end']
2026-10-22 21:58:23,608 INFO corag_api [api] ocr job_id=29 issue_id=19: queued, commit выполнен
2026-10-22 21:58:23,608 INFO corag_api [api] → publish ocr.jobs {"job_id":29,"issue_id":19,"scan_path":"2026-104.pdf","requested_by":1} (LoggingPublisher, брокер — в ЛР4)
127.0.0.1:49016 - "POST /issues/19/ocr HTTP/1.1" 202
2026-10-22 21:58:23,627 INFO corag_api [api] POST /issues/19/ocr → 409: выпуск 19 в статусе ocr_in_progress: OCR запускается из uploaded, ocr_done или failed
127.0.0.1:49026 - "POST /issues/19/ocr HTTP/1.1" 409
…
2026-10-22 21:58:23,693 INFO corag_api [api] article_id=26 удалена
```

Nginx (доп. задание): статика, API через прокси и загрузка большого скана:

```text
$ curl -s localhost/ | grep -o '<title>.*</title>'
<title>CO-RAG API</title>
$ curl -s localhost/api/health
{"status":"ok","db":"ok"}

$ uv run python scripts/make_test_pdf.py data/demo/big.pdf --pages 2 --pad-mb 195
data/demo/big.pdf: 2 стр., 195.0 МБ
$ curl -s -X POST localhost/api/issues/18/scan -H "Authorization: Bearer $TOKEN" \
    -F "file=@data/demo/big.pdf;type=application/pdf" -w '\nHTTP %{http_code}, %{time_total} s\n'
{"id":18,"year":2026,"volume":null,"number":200,"scan_path":"2026-200.pdf","page_count":2,"status":"uploaded","created_by":1,"created_at":"2026-10-22T17:57:23.330123Z"}
HTTP 200, 1.275344 s

$ uv run python scripts/make_test_pdf.py data/demo/huge.pdf --pages 1 --pad-mb 215
data/demo/huge.pdf: 1 стр., 215.0 МБ
$ curl -s -X POST localhost/api/issues/18/scan -H "Authorization: Bearer $TOKEN" \
    -F "file=@data/demo/huge.pdf;type=application/pdf" -w 'HTTP %{http_code}\n' | tail -4
<hr><center>nginx/1.30.6</center>
</body>
</html>
HTTP 413

$ docker compose -f docker-compose.dev.yml logs nginx | tail -3
nginx-1  | 172.18.0.1 "POST /api/issues/18/scan HTTP/1.1" 200 193 upstream=192.168.65.254:8000 rt=1.272
nginx-1  | 2026/10/22 17:57:34 [error] 32#32: *85 client intended to send too large body: 225445606 bytes, client: 172.18.0.1, server: _, request: "POST /api/issues/18/scan HTTP/1.1", host: "localhost"
nginx-1  | 172.18.0.1 "POST /api/issues/18/scan HTTP/1.1" 413 183 upstream=- rt=0.000
```

Файл 215 МБ отсекает сам Nginx (`upstream=-`, до API запрос не дошёл). Файл от 200 до 210 МБ Nginx пропускает, и тогда 413 в JSON отвечает уже API: на 205 МБ получен `{"detail":"скан больше 200 МБ"}`. При запуске API с `API_ROOT_PATH=/api` Swagger открывается по `http://localhost/api/docs`: страница ссылается на `/api/openapi.json`, в схеме `servers: [{"url": "/api"}]`.

Postman: коллекция `docs/postman/co-rag.postman_collection.json` содержит 23 запроса в папках Auth, Issues, Articles и OCR. Test-скрипты сохраняют `token`, `admin_token`, `issue_id`, `article_id` и `job_id`. Те же запросы (URL, тела, авторизация, ожидаемые коды) выполнены по порядку через Nginx (`base_url = http://localhost/api`) скриптом-эмулятором: все 23 ответа пришли с ожидаемыми кодами. Прогон в самом Postman — см. TODO.

![Swagger UI: группы эндпоинтов](img/lr2/swagger.png)

Рисунок 5 — Документация API `/docs` (Swagger UI)

![Postman: коллекция и запрос POST /articles](img/lr2/postman.png)

Рисунок 6 — Postman: коллекция CO-RAG и ответ на создание статьи

![Статическая страница Nginx](img/lr2/nginx.png)

Рисунок 7 — Страница `http://localhost/`, отданная Nginx

## 5. Тестирование

Тесты API работают на той же тестовой БД `corag_test`, что и тесты ЛР1. Фикстуры `engine` и `session` вынесены в `tests/conftest.py`.

Приложение поднимается в памяти (`httpx.AsyncClient` + `ASGITransport`, без сети), зависимости подменяются через `app.dependency_overrides`:

- `get_session` — тестовая сессия во внешней транзакции, которая откатывается после теста;
- `get_settings` — `SCANS_DIR` во временном каталоге, `MAX_SCAN_MB=1`;
- `get_publisher` — публикатор-шпион.

Токены фикстуры получают настоящим `POST /auth/login`. bcrypt в тестах работает с минимальной стоимостью.

Таблица 3 — Состав тестов

| Модуль | Тестов | Что проверяет |
| --- | --- | --- |
| `tests/api/test_auth.py` | 11 | регистрация (нет `password_hash`, роль researcher), дубль e-mail в другом регистре → 409, 422 на короткий пароль, пароль > 72 байт и неверный e-mail; login → JWT (claims, срок 30 мин); неверный пароль → 401 + `WWW-Authenticate`; блокировка → 403 и для старого токена; без токена, с истёкшим, подделанным, «мусорным» токеном, с подменённой ролью и токеном удалённого пользователя → 401 |
| `tests/api/test_users.py` | 5 | список пользователей только admin (403), пагинация; повышение роли действует сразу; нельзя понизить или заблокировать себя (409); 404; `null` для роли → 422 |
| `tests/api/test_issues.py` | 14 | researcher на `POST /issues` → 403; создание, дубль → 409, год < 1987 → 422; фильтр по году и `total`; счётчики карточки; PATCH и DELETE (204 → 404); загрузка PDF (`page_count`, файл на диске, без `*.part`); не PDF и битый PDF → 422; больше лимита → 413; во время OCR загрузка и удаление → 409; текст страницы и 404; данные видны сразу после 201 |
| `tests/api/test_articles.py` | 10 | CRUD статьи: порядок авторов, нормализация ключевых слов, замена при PATCH; страницы в карточке; 422: `page_end` больше выпуска, `page_start > page_end`, неизвестный автор, повторы в `author_ids`, нет выпуска; PATCH проверяет диапазон по объединённым значениям; дубль DOI → 409; фильтры `author`, `keyword`, `year`, `issue_id` и `total` |
| `tests/api/test_authors_keywords.py` | 4 | CRUD и поиск авторов; ORCID: дубль → 409, неверный формат → 422; пустое ФИО → 422; удаление автора со статьями → 409; ключевые слова: нормализация, дубль → 409, поиск, удаление; researcher на запись → 403 |
| `tests/api/test_ocr.py` | 5 | без скана → 409; запуск → 202 `queued`, выпуск `ocr_in_progress`, ровно одно сообщение с верными полями; повтор → 409; вторая активная задача при сброшенном статусе → 409 (индекс БД); порядок `commit → publish`; статус задачи: admin 200, researcher 403, 404 |
| `tests/api/test_health.py` | 2 | 200; БД недоступна → 503 |
| `tests/contracts/test_auth_tokens.py` | 6 | round-trip claims; истёкший; чужой секрет; подмена payload; `alg=none`; нет обязательных claims |
| `tests/db/…` (новое в ЛР2) | +4 | `count`, фильтр `issue_id`, `count_filtered`, `set_keywords`, поиск и `has_articles` у авторов, поиск ключевых слов |

```text
$ uv run pytest -q tests/api
...................................................                      [100%]
51 passed in 3.87s

$ uv run pytest -q
89 passed in 4.89s

$ uv run ruff check .
All checks passed!

$ uv run ruff format --check .
97 files already formatted
```

## 6. Выводы

Реализован API Service CO-RAG на FastAPI: 28 операций REST (27 по функционалу и `/health`) над всеми сущностями БД с регистрацией, входом и разграничением ролей. Код разделён на слои router → service → repository → ORM, связанные через `Depends`, а DTO отделены от ORM-моделей. Изучены семантика HTTP-методов и кодов (201/202/401/403/409/413/422), принципы REST, разница WSGI и ASGI и связка Gunicorn + UvicornWorker. Разобраны тонкости DI в FastAPI (момент commit у yield-зависимостей) и потоковая обработка multipart-загрузок. Оба доп. задания выполнены: JWT (HS256, общий модуль для будущего Notification Service) и Nginx (статика, прокси `/api/`, загрузка скана 195 МБ). Ограничение: брокера пока нет. Задача OCR сохраняется в БД и только логируется `LoggingPublisher`, поэтому выпуск остаётся в `ocr_in_progress` до появления OCR Worker в ЛР4.

## Приложение А. Листинги ключевых модулей

Листинг А.1 — `services/api/src/corag_api/routers/articles.py` (фрагмент)

```python
@router.get("", response_model=Paginated[ArticleOut], summary="Каталог статей с фильтрами")
async def list_articles(
    service: ArticleServiceDep,
    page: PaginationDep,
    year: Annotated[int | None, Query(ge=1987, description="Год выпуска")] = None,
    author: Annotated[str | None, Query(description="Подстрока ФИО автора")] = None,
    keyword: Annotated[str | None, Query(description="Ключевое слово целиком")] = None,
    issue_id: Annotated[int | None, Query(description="Выпуск")] = None,
) -> Paginated[ArticleOut]:
    filters = ArticleFilters(year=year, author=author, keyword=keyword, issue_id=issue_id)
    items, total = await service.search(filters, page.limit, page.offset)
    return Paginated[ArticleOut].of(items, total, page.limit, page.offset)


@router.post(
    "",
    response_model=ArticleOut,
    status_code=status.HTTP_201_CREATED,
    summary="Создать статью с авторами и ключевыми словами (admin)",
    dependencies=[Depends(require_admin)],
    responses={409: {"model": ErrorOut, "description": "DOI уже занят"}, **INVALID},
)
async def create_article(data: ArticleCreate, service: ArticleServiceDep) -> Article:
    return await service.create(data)
```

Листинг А.2 — `services/api/src/corag_api/services/article_service.py` (фрагмент `ArticleService`)

```python
    async def create(self, data: ArticleCreate) -> Article:
        issue = await self.issues.get(data.issue_id)
        if issue is None:
            raise ValidationFailed(f"выпуск {data.issue_id} не существует")
        self._check_range(issue, data.page_start, data.page_end)
        await self._ensure_doi_free(data.doi)
        fields = ArticleFields(
            **data.model_dump(exclude={"author_ids", "keywords"}),
        )
        try:
            article = await self.articles.create_with_relations(
                fields, data.author_ids, data.keywords
            )
        except NotFound as exc:
            raise ValidationFailed(f"авторы не найдены: {exc.key}") from exc
        log.info(...)  # [api] article_id=… создана: …
        return article

    @staticmethod
    def _check_range(issue: Issue, start: int, end: int) -> None:
        """Диапазон страниц статьи должен лежать внутри выпуска (если число страниц известно)."""
        if issue.page_count is not None and end > issue.page_count:
            raise ValidationFailed(
                f"page_end ({end}) больше числа страниц выпуска ({issue.page_count})"
            )
```

Листинг А.3 — `services/api/src/corag_api/deps.py` (фрагмент: сессия на запрос и сборка сервиса)

```python
async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Одна сессия = одна транзакция на запрос: commit при успехе, rollback при исключении."""
    async with request.app.state.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# scope="function": код после yield (commit) выполняется до отправки ответа.
# По умолчанию FastAPI завершает yield-зависимость после ответа, и клиент получил бы 201
# раньше, чем данные зафиксированы (а ошибку commit уже не увидел бы)
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]


def get_article_repository(session: SessionDep) -> ArticleRepository:
    return ArticleRepository(session)


def get_article_service(articles: ArticleRepoDep, issues: IssueRepoDep) -> ArticleService:
    return ArticleService(articles, issues)


ArticleServiceDep = Annotated[ArticleService, Depends(get_article_service)]
```

Листинг А.4 — `services/api/src/corag_api/auth/dependencies.py` (фрагмент, без docstring)

```python
async def get_current_user(
    token: Annotated[str | None, Depends(oauth2_scheme)],
    users: UserRepoDep,
    settings: SettingsDep,
) -> User:
    if not token:
        raise AuthFailed("требуется аутентификация: Authorization: Bearer <token>")
    try:
        claims = read_token(token, settings)
    except InvalidToken as exc:
        raise AuthFailed(f"недействительный токен: {exc}") from exc
    user = await users.get(claims.user_id)
    if user is None:
        raise AuthFailed("пользователь токена не найден")
    if not user.is_active:
        raise Forbidden("пользователь заблокирован")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_role(role: UserRole) -> Callable[..., Awaitable[User]]:
    async def dependency(user: CurrentUser) -> User:
        if ROLE_LEVEL[user.role] < ROLE_LEVEL[role]:
            raise Forbidden(f"недостаточно прав: нужна роль {role.value}")
        return user

    dependency.__name__ = f"require_{role.value}"
    return dependency
```

Листинг А.5 — `packages/corag-contracts/src/corag_contracts/auth.py` (фрагмент, доп. задание JWT)

```python
ALGORITHM = "HS256"
REQUIRED_CLAIMS = ["sub", "role", "iat", "exp"]


def create_access_token(user_id: int, role: str, secret: str, ttl: timedelta) -> str:
    """Подписанный токен со сроком жизни ttl."""
    now = datetime.now(UTC)
    payload = {"sub": str(user_id), "role": role, "iat": now, "exp": now + ttl}
    return jwt.encode(payload, secret, algorithm=ALGORITHM)


def decode_token(token: str, secret: str) -> TokenClaims:
    try:
        payload = jwt.decode(
            token, secret, algorithms=[ALGORITHM], options={"require": REQUIRED_CLAIMS}
        )
        return TokenClaims(
            user_id=int(payload["sub"]),
            role=str(payload["role"]),
            issued_at=datetime.fromtimestamp(payload["iat"], UTC),
            expires_at=datetime.fromtimestamp(payload["exp"], UTC),
        )
    except (jwt.InvalidTokenError, ValueError, TypeError) as exc:
        raise InvalidToken(str(exc)) from exc
```

Листинг А.6 — `services/api/src/corag_api/errors.py` (фрагмент: исключения → HTTP)

```python
STATUS_CODES: dict[type[Exception], int] = {
    AuthFailed: 401,
    Forbidden: 403,
    NotFound: 404,
    Conflict: 409,
    PayloadTooLarge: 413,
    ValidationFailed: 422,
}


def _handler(status_code: int) -> Callable[[Request, Exception], Awaitable[JSONResponse]]:
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        log.info("[api] %s %s → %d: %s", request.method, request.url.path, status_code, exc)
        # RFC 9110: ответ 401 обязан сообщить схему аутентификации
        headers = {"WWW-Authenticate": "Bearer"} if status_code == 401 else None
        return JSONResponse({"detail": str(exc)}, status_code=status_code, headers=headers)

    return handle


def register_exception_handlers(app: FastAPI) -> None:
    for exc_cls, status_code in STATUS_CODES.items():
        app.add_exception_handler(exc_cls, _handler(status_code))
    app.add_exception_handler(IntegrityError, _integrity_error)
```

Листинг А.7 — `services/api/src/corag_api/services/ocr_service.py` (фрагмент `OcrService.start`, без docstring)

```python
    async def start(self, issue_id: int, user: User) -> OcrJob:
        issue = await self.issues.get_or_raise(issue_id)
        if issue.status not in STARTABLE:
            raise Conflict(
                f"выпуск {issue_id} в статусе {issue.status.value}: "
                "OCR запускается из uploaded, ocr_done или failed"
            )
        job = await self.jobs.create_active(issue.id, user.id, pages_total=issue.page_count)
        await self.issues.set_status(issue, IssueStatus.OCR_IN_PROGRESS)
        await self.session.commit()
        log.info("[api] ocr job_id=%d issue_id=%d: queued, commit выполнен", job.id, issue.id)

        assert issue.scan_path is not None  # статус uploaded и выше означает, что скан загружен
        await self.publisher.publish_job(
            OcrJobMessage(
                job_id=job.id, issue_id=issue.id, scan_path=issue.scan_path, requested_by=user.id
            )
        )
        return job
```

## Приложение Б. Конфигурационные файлы

Листинг Б.1 — `.env.example` (секции API и Nginx)

```bash
# --- API Service (services/api, ApiSettings) ---
# Каталог PDF-сканов; scan_path выпуска хранится относительно него (общий том с OCR Worker в ЛР4–5)
SCANS_DIR=data/scans
# Максимальный размер скана, МБ (больше → 413; у Nginx client_max_body_size с запасом)
MAX_SCAN_MB=200
# Секрет подписи JWT (HS256), не короче 32 символов; общий с Notification Service (ЛР4).
# Сгенерировать: python -c "import secrets; print(secrets.token_hex(32))"
JWT_SECRET=change-me-0123456789abcdef0123456789abcdef
# Срок жизни access-токена, минуты
JWT_TTL_MIN=30
# Префикс API за reverse proxy: пусто при прямом доступе, /api — за Nginx (для /docs)
API_ROOT_PATH=
# Уровень логов API: DEBUG, INFO, WARNING
LOG_LEVEL=INFO

# --- Nginx (docker-compose.dev.yml) ---
# Порт на хосте: http://localhost:${NGINX_PORT}/ — статика, /api/ — API
NGINX_PORT=80
```

Листинг Б.2 — `deploy/nginx/nginx.conf` (фрагмент: блок `server`)

```nginx
    upstream api {
        server host.docker.internal:8000;
    }

    server {
        listen 80;
        server_name _;

        # Скан выпуска до 200 МБ (MAX_SCAN_MB) + накладные расходы multipart.
        # Без этой строки Nginx отвечает 413 уже на 1 МБ (значение по умолчанию)
        client_max_body_size 210m;

        location / {
            root  /usr/share/nginx/html;
            index index.html;
        }

        location /api/ {
            proxy_pass http://api/;
            proxy_http_version 1.1;
            proxy_set_header Host              $host;
            proxy_set_header X-Real-IP         $remote_addr;
            proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto $scheme;
            # Тело запроса передаётся API сразу, без буферизации на диске Nginx
            proxy_request_buffering off;
            proxy_read_timeout 300s;
        }
```

Листинг Б.3 — `docker-compose.dev.yml` (фрагмент: сервис `nginx`)

```yaml
  # Reverse proxy (доп. задание ЛР2): статика на /, API на /api/
  nginx:
    image: nginx:stable-alpine
    ports:
      - "${NGINX_PORT:-80}:80"
    volumes:
      - ./deploy/nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./deploy/nginx/static:/usr/share/nginx/html:ro
    # API пока запущен в WSL, а не в контейнере: проксируем на хост
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

---

**TODO для автора перед сдачей:**

- [ ] вписать номер PR в шапку после создания PR `lr2-rest-api → main`;
- [ ] установить `jq` (`sudo apt install -y jq`): без него `scripts/demo_lr2.sh` не запустится;
- [ ] скриншот Swagger UI `http://localhost:8000/docs` с раскрытыми группами → `docs/reports/img/lr2/swagger.png`;
- [ ] импортировать коллекцию в Postman, выбрать файл `data/demo/scan.pdf` в запросе «Загрузить PDF-скан», пройти Run collection (все тесты зелёные). Скриншот коллекции и ответа на `POST /articles` → `docs/reports/img/lr2/postman.png`;
- [ ] скриншот `http://localhost/` (страница Nginx) → `docs/reports/img/lr2/nginx.png`;
- [ ] пройти репетицию по `defense/lr2.md`, раздел 1.
