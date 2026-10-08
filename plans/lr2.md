# ЛР №2. Разработка REST API

**Ветка:** `lr2-rest-api` · **Технологии:** FastAPI, Pydantic v2, Gunicorn + Uvicorn, **[ОПЦИОНАЛЬНО]** JWT, **[ОПЦИОНАЛЬНО]** Nginx
**Зависит от:** ЛР1 (`corag-db`) · **Готовит почву для:** ЛР3 (`/rag/*`), ЛР4 (публикация задач OCR), ЛР6
**Отчёт:** `docs/reports/lr2.md` (шаблон `docs/reports/_template.md`) · **Подготовка к сдаче:** `defense/lr2.md` + `defense/lr2-cheatsheet.md` (составляет Claude Code, раздел 8)

## 1. Задание из методички

Реализовать REST API для работы с БД по HTTP. API должен обеспечивать:

- [ ] регистрацию и аутентификацию пользователей;
- [ ] CRUD над объектами БД через REST;
- [ ] прочие запросы к БД по функционалу проекта;
- [ ] ответы в формате JSON;
- [ ] API Service на FastAPI; сервисы и репозитории подключаются через Dependency Injection (`Depends`);
- [ ] модели для HTTP (DTO) отделены от моделей слоя данных (ORM).
- [ ] **[ОПЦИОНАЛЬНО — доп. задание №1]** Nginx как reverse proxy: отдаёт статику или проксирует запрос на бэкенд.
- [ ] **[ОПЦИОНАЛЬНО — доп. задание №2]** аутентификация и авторизация через JWT.

**Сдача:** взаимодействие с БД только через HTTP-запросы (Postman или cURL).

> По ТЗ роли проверяются через JWT, а WebSocket в ЛР4 тоже аутентифицируется токеном. Поэтому JWT формально опционален, но выполнить его очень желательно.
> Без JWT базовая аутентификация делается через HTTP Basic (см. шаг 4). Зависимость `get_current_user` одна и та же, меняется только её реализация.

## 2. Результат лабы

- `services/api`: приложение FastAPI со слоями router → service → repository.
- Эндпоинты всех функций ТЗ, кроме `/rag/*` (они появятся в ЛР3).
- Загрузка PDF-скана; создание задачи OCR с ответом `202`. Отправка задачи в брокер пока заглушка (`LoggingPublisher`), в ЛР4 её заменит RabbitMQ.
- Postman-коллекция и `scripts/demo_lr2.sh` (cURL) для демонстрации.

## 3. Структура файлов

```
services/api/
  pyproject.toml          # fastapi, uvicorn[standard], gunicorn, python-multipart, pymupdf, corag-db (workspace),
                          # [ОПЦИОНАЛЬНО] pyjwt
  src/corag_api/
    main.py               # create_app(): lifespan (engine), роутеры, exception handlers, CORS выкл.
    settings.py           # ApiSettings: DATABASE_URL, SCANS_DIR, MAX_SCAN_MB=200, JWT_SECRET, JWT_TTL_MIN=30
    deps.py               # get_session (commit/rollback на запрос), get_*_repository, get_*_service, get_publisher
    errors.py             # доменные исключения → HTTP (404/409/403/422) + единый формат {"detail": ...}
    auth/
      passwords.py        # bcrypt hash/verify
      dependencies.py     # get_current_user, require_role("admin")
      basic.py            # реализация через HTTPBasic (базовая)
      jwt.py              # [ОПЦИОНАЛЬНО] create_access_token / decode_token, OAuth2PasswordBearer
    schemas/              # DTO: user.py issue.py page.py article.py author.py keyword.py ocr_job.py common.py (Page[T], пагинация)
    services/             # auth_service.py user_service.py issue_service.py article_service.py ocr_service.py
    publishers.py         # протокол JobPublisher + LoggingPublisher (ЛР4 добавит AmqpPublisher)
    routers/              # auth.py users.py issues.py pages.py articles.py authors.py keywords.py ocr_jobs.py
deploy/nginx/nginx.conf   # [ОПЦИОНАЛЬНО]
deploy/nginx/static/index.html  # [ОПЦИОНАЛЬНО] простая страница «CO-RAG API» со ссылкой на /docs
docs/postman/co-rag.postman_collection.json
scripts/demo_lr2.sh
tests/api/
```

## 4. Шаги реализации

### Шаг 1. Каркас приложения

- `create_app()`:
    - `lifespan` создаёт движок и `async_sessionmaker` и кладёт их в `app.state`;
    - при остановке движок закрывается (`dispose`).
- `get_session`: открывает сессию на запрос. Если обработчик завершился успешно — `commit`, при исключении — `rollback`.
- Health-эндпоинт `GET /health` (проверка `SELECT 1`). Понадобится в ЛР5.

### Шаг 2. DTO (Pydantic)

Для каждой сущности свои схемы: `XCreate`, `XUpdate` (все поля `Optional`, для PATCH) и `XOut` (`model_config = ConfigDict(from_attributes=True)`).

- `UserOut` без `password_hash`.
- `RegisterIn`:
    - `email: EmailStr`;
    - `password` от 8 символов;
    - `full_name`.
- `ArticleCreate`:
    - `issue_id`, `title`;
    - `page_start`, `page_end` (`ge=1`);
    - `author_ids: list[int]` — порядок в списке задаёт `author_order`;
    - `keywords: list[str]`, `doi`, `language`, `abstract`.
- `ArticleOut` содержит вложенные `authors: list[AuthorShort]` (по порядку), `keywords: list[str]`, `issue: IssueShort`.
- `Page[T]` для списков: `items`, `total`, `limit`, `offset`.
- `OcrJobOut`: `id`, `status`, `pages_done`, `pages_total`, `error`, временные метки.

### Шаг 3. Сервисный слой

Сервисы получают репозитории через конструктор и выбрасывают доменные исключения:

- `AuthService.register`:
    - e-mail уже занят → `Conflict`;
    - роль всегда `researcher`.
- `AuthService.authenticate`:
    - неверный пароль → 401;
    - `is_active = false` → 403.
- `IssueService`:
    - CRUD выпусков;
    - `upload_scan(issue_id, file)`:
        - проверяет сигнатуру `%PDF` и размер;
        - пишет файл потоково в `SCANS_DIR/{year}-{number}.pdf`;
        - считает страницы через PyMuPDF;
        - ставит статус `uploaded`.
- `ArticleService`:
    - проверяет, что `page_end <= issue.page_count` (если число страниц известно), иначе 422;
    - проверяет, что все `author_ids` существуют, иначе 422;
    - ключевые слова обрабатывает через `get_or_create_many`.
- `OcrService.start(issue_id, user)`:
    - выпуск должен быть в статусе `uploaded`, `ocr_done` или `failed`, иначе 409;
    - `OcrJobRepository.create_active` (если уже есть активная задача → 409);
    - статус выпуска → `ocr_in_progress`;
    - `publisher.publish_job(...)` вызывается **после commit** (через колбэк или явный commit в сервисе). Иначе воркер увидит задачу раньше, чем она появится в БД.

### Шаг 4. Аутентификация и роли

- **Базовая часть (без JWT):**
    - `POST /auth/register`;
    - `POST /auth/login` (проверяет пароль, возвращает `UserOut`);
    - `get_current_user` на `fastapi.security.HTTPBasic`.
- `require_role("admin")` — фабрика зависимостей, возвращает 403.
- **[ОПЦИОНАЛЬНО — JWT]:**
    - `POST /auth/login` принимает `OAuth2PasswordRequestForm` и возвращает `{access_token, token_type: "bearer", expires_in}`;
    - HS256, claims: `sub` (id), `role`, `iat`, `exp` (30 мин);
    - `get_current_user` через `OAuth2PasswordBearer`: декодирует токен, затем проверяет пользователя в БД и `is_active` (заблокированный → 403);
    - `JWT_SECRET` берётся из `.env`;
    - функция `decode_token` выносится так, чтобы её мог импортировать Notification Service в ЛР4. Вариант — модуль `corag_contracts.auth`.

### Шаг 5. Роутеры и эндпоинты

| Метод и путь | Доступ | Ответ |
| --- | --- | --- |
| `POST /auth/register` | все | 201 `UserOut` / 409 |
| `POST /auth/login` | все | 200 токен (JWT) или `UserOut` (Basic) / 401 / 403 |
| `GET /users/me` | авторизован | `UserOut` |
| `GET /users`, `PATCH /users/{id}` (role, is_active) | admin | `Page[UserOut]`, `UserOut` |
| `GET /issues?year=&limit=&offset=`, `GET /issues/{id}` | researcher+ | список / карточка с числом страниц и статей |
| `POST /issues`, `PATCH /issues/{id}`, `DELETE /issues/{id}` | admin | 201 / 200 / 204 |
| `POST /issues/{id}/scan` (multipart, PDF) | admin | 200 `IssueOut` (status `uploaded`, `page_count`) / 422 |
| `GET /issues/{id}/pages/{n}` | researcher+ | текст страницы / 404 |
| `POST /issues/{id}/ocr` | admin | **202** `OcrJobOut` / 409 |
| `GET /ocr-jobs/{id}` | admin | `OcrJobOut` |
| `GET /articles?year=&author=&keyword=&issue_id=` | researcher+ | `Page[ArticleOut]` |
| `GET /articles/{id}` | researcher+ | `ArticleOut` + список страниц статьи |
| `POST /articles`, `PATCH /articles/{id}`, `DELETE /articles/{id}` | admin | 201 / 200 / 204 |
| `GET /authors?q=`, `POST /authors`, `PATCH /authors/{id}`, `DELETE /authors/{id}` | чтение — researcher+, запись — admin | — |
| `GET /keywords`, `POST /keywords`, `DELETE /keywords/{id}` | то же | — |

У каждого эндпоинта задать `response_model`, `status_code`, `summary` и `tags`. Тогда документация `/docs` выглядит аккуратно.

### Шаг 6. Запуск через Gunicorn

```bash
uv run --package corag-api gunicorn corag_api.main:create_app --factory \
  -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:8000
```

Если в используемой версии Gunicorn нет флага `--factory`, нужен модуль `corag_api.asgi` с `app = create_app()`.
uWSGI из методички — WSGI-сервер, для асинхронного FastAPI он не подходит. Это стоит сказать на защите.

### Шаг 7. [ОПЦИОНАЛЬНО — доп. задание №1] Nginx

- В `docker-compose.dev.yml` добавить сервис `nginx:stable`:
    - монтируются `deploy/nginx/nginx.conf` и `static/`;
    - порт 80;
    - `extra_hosts: ["host.docker.internal:host-gateway"]` — API пока запущен в WSL, а не в контейнере.
- `nginx.conf`:
    - `location /` — статика (`index.html`);
    - `location /api/` → `proxy_pass http://host.docker.internal:8000/;` с заголовками `Host`, `X-Real-IP`, `X-Forwarded-For`;
    - `client_max_body_size 200m;` — без этого загрузка скана падает с 413.
- FastAPI запускается с `root_path="/api"`, чтобы `/docs` работал за прокси.
- Блок `location /ws` (`Upgrade` / `Connection`) заранее закомментировать. Он понадобится в ЛР4.

### Шаг 8. Тесты

- `httpx.AsyncClient(transport=ASGITransport(app))`.
- Переопределение `get_session` на тестовую БД с откатом. Фикстуры с токенами (или Basic-заголовками) admin и researcher.
- Обязательные кейсы:
    - регистрация и повторная регистрация (409);
    - 401 без авторизации;
    - 403 у researcher на `POST /issues`;
    - CRUD статьи с авторами;
    - 422 при неверном диапазоне страниц;
    - загрузка маленького PDF (генерируется PyMuPDF в фикстуре);
    - `POST /ocr` → 202, повторный → 409.

### Шаг 9. Материалы для демонстрации

- Postman-коллекция: папки Auth / Issues / Articles / OCR.
    - Переменные `{{base_url}}`, `{{token}}`.
    - Test-скрипт на login, который сохраняет токен.
- `scripts/demo_lr2.sh`: тот же сценарий через `curl` и `jq`. Последовательность: регистрация → логин → каталог → создание выпуска → загрузка PDF → статья → фильтры → запуск OCR (202) → статус задачи.

## 5. Как проверить и показать

```bash
docker compose -f docker-compose.dev.yml up -d
uv run python -m corag_db.seed
uv run --package corag-api uvicorn corag_api.main:create_app --factory --reload   # разработка
bash scripts/demo_lr2.sh                                                           # или Postman
open http://localhost:8000/docs
uv run pytest -q tests/api && uv run ruff check .
```

## 6. Критерии готовности

- [ ] Регистрация и аутентификация работают; роли проверяются (401/403 отдаются корректно).
- [ ] CRUD через REST: выпуски, статьи (с авторами и ключевыми словами), авторы, ключевые слова, пользователи (admin).
- [ ] Прочие запросы: фильтры каталога, страница выпуска, загрузка скана, запуск OCR (202), статус задачи.
- [ ] Все ответы в JSON; DTO отделены от ORM; сервисы и репозитории внедряются через `Depends`.
- [ ] Запуск через Gunicorn + UvicornWorker.
- [ ] Postman-коллекция и `demo_lr2.sh` проходят от начала до конца.
- [ ] `pytest` и `ruff` проходят.
- [ ] **[ОПЦИОНАЛЬНО]** JWT: токен выдаётся и проверяется, истёкший или подделанный → 401.
- [ ] **[ОПЦИОНАЛЬНО]** Nginx: статика на `/`, API на `/api/`, загрузка 200 МБ проходит.
- [ ] Подготовка к сдаче составлена по разделу 8: `defense/lr2.md` (сценарий 5–7 мин, теория, вопросы с ответами) и `defense/lr2-cheatsheet.md`; команды проверены запуском.
- [ ] Отчёт `docs/reports/lr2.md` составлен по шаблону: настоящий вывод консоли, схемы, листинги; список TODO (скриншоты) закрыт.
- [ ] Пройден сценарий сдачи из `defense/lr2.md` (репетиция).
- [ ] PR `lr2-rest-api` → `main`.

## 7. Подводные камни

- `UploadFile` нельзя целиком читать в память — писать чанками в файл.
- Не возвращать ORM-объекты с незагруженными связями: `ArticleOut` с авторами требует `selectinload`, иначе `MissingGreenlet`.
- 401 и 403 — разные вещи. Для 401 добавить заголовок `WWW-Authenticate`.
- Публикация в брокер (ЛР4) — строго после commit транзакции.

## 8. Подготовка к сдаче (составляет Claude Code)

Делается **до отчёта**: раздел 2 «Теория» из `defense/lr2.md` используется в отчёте (раздел 9).
Порядок закрытия лабы:

1. Пройти разделы 5–6.
2. Подготовка к сдаче (этот раздел).
3. Отчёт (раздел 9).
4. Репетиция сдачи.
5. PR.

Общие правила — в CLAUDE.md, раздел «Документация по лабе». Промпт — 10.2.

### 8.1. `defense/lr2.md` — сценарий сдачи, теория, вопросы

#### Раздел 1. Сценарий сдачи (5–7 мин)

**Подготовка:**

- БД и сид, API запущен через Gunicorn;
- Postman с коллекцией и переменными, тестовый PDF под рукой;
- **[ОПЦИОНАЛЬНО]** Nginx поднят.

| Время | Говорю | Показываю |
| --- | --- | --- |
| 0:00–0:40 | Что сделано, слои API | Схема слоёв из `report.md` |
| 0:40–1:20 | Документация API | `http://localhost:8000/docs` — группы эндпоинтов |
| 1:20–4:20 | Сценарий в Postman | register → login (токен) → запрос без токена: 401 → researcher на `POST /issues`: 403 → admin: выпуск, загрузка PDF (`page_count`), статья с авторами, фильтр по автору → `POST /ocr`: 202 → повтор: 409 |
| 4:20–5:20 | Код | Роутер → сервис → репозиторий; `Depends`; DTO; **[ОПЦИОНАЛЬНО]** JWT на jwt.io; `curl localhost/api/health` через Nginx |
| 5:20–7:00 | Резерв на вопросы | — |

#### Раздел 2. Минимальная теория (2–3 стр.)

- HTTP: структура запроса и ответа, методы, заголовки, классы кодов.
- REST: ресурсы, единообразный интерфейс, stateless, идемпотентность; JSON как формат.
- ASGI и WSGI, серверы приложений, воркеры.
- FastAPI: маршрутизация, DI, OpenAPI; Pydantic-валидация.
- Аутентификация и авторизация; Basic, токены; **[ОПЦИОНАЛЬНО]** JWT.
- **[ОПЦИОНАЛЬНО]** Reverse proxy: функции Nginx.

#### Раздел 3. Вопросы преподавателя с ответами (12–15)

На каждую тему — формулировка вопроса и ответ в 2–4 предложения. Где уместно, ответ ссылается на файл и функцию.

- принципы REST, stateless;
- идемпотентность методов, PUT против PATCH;
- коды 201, 202, 401, 403, 409, 422;
- DI в FastAPI;
- зачем DTO отдельно от ORM;
- как Pydantic валидирует;
- ASGI против WSGI, Uvicorn и Gunicorn, почему не uWSGI;
- async/await и event loop;
- multipart-загрузка файлов;
- хранение паролей (bcrypt);
- **[ОПЦИОНАЛЬНО]** структура JWT, подпись, HS256 против RS256, отзыв токена;
- **[ОПЦИОНАЛЬНО]** reverse proxy, `client_max_body_size`;
- что такое CORS.

### 8.2. `defense/lr2-cheatsheet.md` — шпаргалка (1 стр.)

- **Тезисы:** слои; DTO ≠ ORM; 401 ≠ 403; 202 для OCR; публикация в брокер только после commit.
- **Команды:** uvicorn для разработки, gunicorn, `demo_lr2.sh`, curl для логина и запроса с `Authorization: Bearer`, pytest.
- **Таблица эндпоинтов** (сжатая) и **Где в коде:** `create_app`, `get_session`, `get_current_user`, `require_role`, `ArticleService`, exception handlers, `upload_scan`.
- **Частые ошибки:** чтение `UploadFile` целиком, `MissingGreenlet` в DTO, 413 за Nginx.

## 9. Отчёт по лабе

Отчёт пишется **после** того, как пройдены разделы 5 и 6 и составлена подготовка к сдаче (раздел 8). Шаблон и правила — `docs/reports/_template.md`.
Коротко: вывод консоли только настоящий, скриншоты — заглушки со списком TODO, теория — по `defense/lr2.md`, не больше страницы.

| Раздел отчёта | Содержание для ЛР2 |
| --- | --- |
| 1. Цель и задание | Пункты методички; JWT и Nginx — «(доп. задание)», отметка о выполнении |
| 2. Теория | HTTP и REST (методы, идемпотентность, коды), ASGI/WSGI, Gunicorn + Uvicorn, DI в FastAPI, DTO и ORM, аутентификация и авторизация, [опц.] JWT, reverse proxy |
| 3.1 Реализовано | Таблица всех эндпоинтов из `/openapi.json`: метод, путь, роль, коды ответов |
| 3.2 Схемы | Диаграмма классов слоёв (router → service → repository) для статей; sequence «логин → запрос с токеном → проверка роли → ответ»; [опц.] схема Nginx → API |
| 3.3 Решения | Публикация задачи после commit; 202 для OCR; потоковая запись скана; формат ошибок |
| 4. Демонстрация | Вывод `scripts/demo_lr2.sh` (запрос + ответ JSON, сокращённо): регистрация, логин, 401, 403, CRUD статьи, фильтры, загрузка PDF, 202 и 409 на OCR; строка запуска Gunicorn и её лог |
| 5. Тестирование | Вывод `pytest -q tests/api`, `ruff check` |
| Приложение А | `routers/articles.py`, `services/article_service.py`, `deps.py`, `auth/dependencies.py`, [опц.] `auth/jwt.py`, `errors.py` |
| Приложение Б | `.env.example` (секция API); [опц.] `deploy/nginx/nginx.conf` |
| Скриншоты (TODO) | Swagger `/docs`; Postman: коллекция и один запрос с ответом; [опц.] страница Nginx |

## 10. Промпты для Claude Code

### 10.1. Планирование (режим планирования)

```
Прочитай CLAUDE.md и plans/lr2.md, изучи текущий код packages/corag-db (ЛР1 уже в main).
Ветка lr2-rest-api. Составь план реализации REST API по plans/lr2.md.
Опционально: JWT — ВКЛЮЧИТЬ / НЕ включать; Nginx — ВКЛЮЧИТЬ / НЕ включать (выбери).
В плане укажи порядок шагов, список файлов, список эндпоинтов с кодами ответов и какие тесты пишем.
Не пиши код до одобрения плана.
```

### 10.2. Подготовка к сдаче (после реализации, обычный режим)

```
Лаба 2 реализована и проверена (разделы 5–6 plans/lr2.md пройдены). Составь подготовку к сдаче по разделу 8 plans/lr2.md
и правилам CLAUDE.md («Документация по лабе»): defense/lr2.md (раздел 1 — сценарий сдачи на 5–7 мин с подготовкой и таймингом,
раздел 2 — минимальная теория на 2–3 стр., раздел 3 — вопросы преподавателя с ответами) и defense/lr2-cheatsheet.md
(1 стр.: тезисы, команды, таблица «Где в коде», частые ошибки).
Все команды из сценария и шпаргалки выполни сам; примеры вывода — только настоящие. Ссылки на код — реальные пути и функции.
Закоммить: docs(lr2): подготовка к сдаче.
```

### 10.3. Отчёт (после реализации, обычный режим)

```
Лаба 2 реализована и проверена. Составь отчёт docs/reports/lr2.md по шаблону docs/reports/_template.md
и разделу 9 plans/lr2.md. Теорию возьми из defense/lr2.md (раздел 2) и перескажи своими словами,
не больше страницы. Все команды из раздела «Демонстрация» запусти сам и вставь настоящий вывод (длинный сокращай с «…»);
что запустить нельзя — пометь TODO. Схемы — mermaid по фактическому коду. В конце — список TODO для меня (скриншоты и т. п.).
Закоммить: docs(lr2): отчёт.
```
