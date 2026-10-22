# ЛР2. Разработка REST API — подготовка к сдаче

Ветка `lr2-rest-api`. Сервис `services/api` (FastAPI, Pydantic v2) поверх `corag-db`: 28 операций REST (27 по функционалу ТЗ и `/health`).
Слои router → service → repository → ORM, DI через `Depends`, DTO отдельно от ORM.
Доп. задания: JWT (пакет `corag-contracts`) и Nginx как reverse proxy. Запуск — Gunicorn + UvicornWorker.
89 тестов (51 на API), демо `scripts/demo_lr2.sh` и Postman-коллекция `docs/postman/co-rag.postman_collection.json`.

## 1. Сценарий сдачи (5–7 мин)

### Подготовка до сдачи

1. Запустить Docker Desktop. Если `docker` в WSL пишет «could not be found in this WSL 2 distro», значит, он не стартовал: запустить из меню «Пуск» и подождать ~10 с.
2. Поднять PostgreSQL и Nginx, применить миграции и сид (всё заранее, на сдаче только показ):

   ```bash
   docker compose -f docker-compose.dev.yml up -d          # postgres + nginx
   uv sync
   (cd packages/corag-db && uv run alembic upgrade head)
   uv run python -m corag_db.seed
   uv run python scripts/make_test_pdf.py data/demo/scan.pdf --pages 8
   ```

3. **Терминал 1** (крупный шрифт): запустить API под Gunicorn. Этот терминал на сдаче показывает логи `[api] …`.

   ```bash
   uv run --package corag-api gunicorn 'corag_api.main:create_app()' \
     -k uvicorn_worker.UvicornWorker -w 2 -b 0.0.0.0:8000 --access-logfile -
   ```

   Gunicorn готов за ~0,8 с. Если сразу после (пере)запуска API `http://localhost/api/…` отвечает 502, значит, Docker Desktop ещё не пробросил порт из WSL: подождать 2–3 с.
4. **Терминал 2**: один раз выполнить `bash scripts/demo_lr2.sh` для прогрева. Нужен `jq` (`sudo apt install -y jq`). Скрипт повторяемый: e-mail и номер выпуска каждый раз новые, созданная статья в конце удаляется.
   На репетиции: демо — 1,3 с (299 строк, удобно `bash scripts/demo_lr2.sh | less -R`), `uv run pytest -q tests/api` — 5 с, остальные команды показа — меньше 0,2 с. Время уходит только на рассказ.
5. **Postman**: импортировать `docs/postman/co-rag.postman_collection.json`. Переменная `base_url` = `http://localhost:8000`. В запросе «Загрузить PDF-скан» выбрать файл `data/demo/scan.pdf`. Один раз пройти Run collection.
6. **Браузер**: вкладки `http://localhost:8000/docs` и `http://localhost/` (статика Nginx).
7. **VS Code**: вкладки `routers/articles.py` (`create_article`), `services/article_service.py` (`ArticleService.create`), `deps.py` (`get_session`), `auth/dependencies.py` (`get_current_user`, `require_role`), `services/ocr_service.py` (`OcrService.start`). Схема слоёв — раздел 3.2 отчёта `docs/reports/lr2.md` (превью Markdown).

### Тайминг

| Время | Говорю | Показываю (окно / команда) |
| --- | --- | --- |
| 0:00–0:40 | Цель ЛР2: доступ к БД только по HTTP. API Service на FastAPI, слои router → service → repository → ORM. Сервисы и репозитории внедряются через `Depends`, DTO (Pydantic) отделены от ORM. Доп. задания: JWT и Nginx | Схема слоёв (рис. 1 отчёта) |
| 0:40–1:20 | Документация генерируется из кода: OpenAPI 3.1, группы по тегам, у каждого эндпоинта `summary`, модель ответа и коды ошибок. Кнопка Authorize — OAuth2 Password → JWT | Браузер: `/docs`, раскрыть `POST /articles` (201, 401, 403, 409, 422) |
| 1:20–4:20 | Сценарий в Postman. Регистрация → роль `researcher` назначает сервер; повтор → 409. Login → токен сохраняется в переменную. Без токена → 401 с `WWW-Authenticate: Bearer`; researcher на `POST /issues` → 403. Admin: выпуск 201 → загрузка PDF (`page_count`=8, статус `uploaded`) → статья с двумя авторами (порядок) и ключевыми словами (нормализованы) → 422 при `page_end` больше числа страниц → фильтр по автору и слову → `POST /ocr` **202** → повтор **409** → статус задачи `queued`. Параллельно — логи в терминале 1: `commit выполнен`, затем `→ publish ocr.jobs` | Postman: запросы по порядку (или Run collection); терминал 1. Запасной вариант — `bash scripts/demo_lr2.sh` в терминале 2 |
| 4:20–5:20 | Код: роутер только принимает DTO и вызывает сервис; сервис проверяет правила и бросает доменные исключения → 404/409/422; `get_session` — сессия на запрос, commit до ответа (`scope="function"`). `get_current_user`: токен → БД → `is_active`; `require_role` — 403. Payload JWT: `sub`, `role`, `iat`, `exp`. Nginx: `/` — статика, `/api/` — прокси | VS Code: `create_article` → `ArticleService.create` → `ArticleRepository.create_with_relations`; `deps.py`; терминал 2: `curl -s localhost/api/health`; браузер: `http://localhost/` |
| 5:20–7:00 | Резерв на вопросы | — |

## 2. Минимальная теория

### 2.1. HTTP: запрос, ответ, методы, коды

HTTP — текстовый протокол «запрос — ответ» поверх TCP. Запрос состоит из стартовой строки (метод, путь, версия), заголовков, пустой строки и необязательного тела. Ответ — из статусной строки, заголовков и тела. Настоящий обмен с CO-RAG (`curl -v localhost:8000/health`):

```text
> GET /health HTTP/1.1
> Host: localhost:8000
> Accept: */*
>
< HTTP/1.1 200 OK
< server: uvicorn
< content-length: 25
< content-type: application/json
<
{"status":"ok","db":"ok"}
```

Заголовки несут метаданные: `Content-Type` (формат тела — `application/json`, `multipart/form-data`), `Authorization` (учётные данные), `WWW-Authenticate` (какой способ входа ждёт сервер). Коды ответа делятся на классы: 2xx — успех, 3xx — перенаправление, 4xx — ошибка клиента, 5xx — ошибка сервера. В CO-RAG используются:

- **200** — успех;
- **201 Created** — создан ресурс (`POST /issues`);
- **202 Accepted** — запрос принят, работа будет сделана позже (`POST /issues/{id}/ocr`);
- **204 No Content** — успех без тела (`DELETE`);
- **401** — клиент не аутентифицирован;
- **403** — аутентифицирован, но прав нет;
- **404** — ресурса нет;
- **409 Conflict** — запрос противоречит текущему состоянию (e-mail занят, уже идёт OCR);
- **413 Content Too Large** — тело больше лимита;
- **422 Unprocessable Content** — запрос синтаксически верен, но данные недопустимы (диапазон страниц больше выпуска);
- **503** — БД недоступна (`/health`).

### 2.2. REST

REST — архитектурный стиль, который Р. Филдинг описал для веба. Его принципы:

- **Ресурсы и URI.** Существительные, а не действия: `/issues/12`, `/articles?author=Орлова`. Для нересурсных операций берётся подресурс: `POST /issues/12/ocr` создаёт задачу.
- **Единообразный интерфейс.** Одни и те же методы для всех ресурсов: GET — прочитать, POST — создать, PATCH — частично изменить, PUT — заменить целиком, DELETE — удалить. Представление ресурса передаётся в JSON.
- **Stateless.** Сервер не хранит сессию клиента. Каждый запрос самодостаточен и несёт токен в `Authorization`, поэтому любой из двух воркеров Gunicorn может обработать любой запрос.
- **Кешируемость и многоуровневость.** Между клиентом и сервером может стоять прокси (Nginx), клиент этого не замечает.

**Безопасный** метод не меняет состояние (GET, HEAD, OPTIONS). **Идемпотентный** даёт тот же результат при повторе (безопасные плюс PUT и DELETE). POST не идемпотентен: два `POST /issues/12/ocr` — это две попытки создать задачу, поэтому вторая получает 409. PATCH в общем случае не идемпотентен (например, «увеличить счётчик»), хотя наши PATCH «установить поле» фактически идемпотентны. Повторный DELETE возвращает 404 вместо 204, но состояние сервера то же, и идемпотентность определяется именно по нему.

### 2.3. ASGI, WSGI и серверы приложений

**WSGI** (PEP 3333) — синхронный интерфейс Python-веб-приложения: функция `app(environ, start_response)` обрабатывает один запрос и держит поток, пока не ответит. **ASGI** — его асинхронный преемник: `async app(scope, receive, send)`. Он поддерживает долгие соединения (WebSocket, понадобится в ЛР4) и тысячи одновременных запросов в одном потоке на event loop. Пока один запрос ждёт PostgreSQL (`await` в asyncpg), цикл обслуживает другие.

FastAPI — ASGI-приложение. **Uvicorn** — ASGI-сервер (uvloop + httptools). **Gunicorn** — менеджер процессов модели pre-fork: мастер запускает `-w 2` воркеров, перезапускает упавшие и распределяет соединения. Воркер класса `uvicorn_worker.UvicornWorker` запускает внутри процесса Uvicorn с event loop. В итоге многоядерность дают процессы, а конкурентность внутри процесса — асинхронность. Класс `uvicorn.workers.UvicornWorker` в Uvicorn 0.54 объявлен устаревшим, поэтому стоит пакет `uvicorn-worker`. **uWSGI** из методички — WSGI-сервер: асинхронное ASGI-приложение он не запустит, к тому же проект находится в режиме поддержки.

### 2.4. FastAPI: маршрутизация, DI, OpenAPI, Pydantic

Маршрут — декоратор `@router.post("/articles", status_code=201, response_model=ArticleOut)`. Параметры функции FastAPI берёт из пути, query, тела или заголовков по их аннотациям.

**Dependency Injection**: параметр `service: ArticleServiceDep` (`Annotated[ArticleService, Depends(get_article_service)]`) означает «перед вызовом построй сервис». Зависимости образуют граф: `get_article_service` → `get_article_repository` → `get_session`. В пределах запроса каждая вызывается один раз (кеш), поэтому все репозитории получают одну сессию, то есть одну транзакцию. Зависимость с `yield` (`deps.py` → `get_session`) — это контекст: код до `yield` открывает сессию, код после — `commit` или `rollback`. По умолчанию FastAPI 0.160 выполняет код после `yield` **после отправки ответа**, поэтому у нас `Depends(get_session, scope="function")`: commit проходит до ответа, и клиент не получит 201 раньше фиксации. В тестах зависимость подменяется через `app.dependency_overrides`.

**Pydantic v2** валидирует входные данные по аннотациям: `EmailStr`, `Field(min_length=8)`, `ge=1`, `pattern` для ORCID и собственные валидаторы (`page_start <= page_end`, без повторов в `author_ids`). При ошибке FastAPI сам отвечает 422 со списком нарушений. **DTO отделены от ORM**: `UserOut` просто не содержит `password_hash`, поэтому утечь ему некуда. Схема API может меняться независимо от таблиц: `ArticleOut.keywords` — список строк, хотя в БД это M:N. Ответ собирается из ORM-объекта по атрибутам (`from_attributes=True`), а связи заранее загружены `selectinload`, иначе в async-коде будет `MissingGreenlet`. Из всех аннотаций FastAPI строит **OpenAPI**-схему, которую показывают `/docs` (Swagger UI) и `/openapi.json`.

### 2.5. Аутентификация и авторизация, JWT

**Аутентификация** отвечает на вопрос «кто ты»: пароль проверяется по bcrypt-хешу. **Авторизация** — «что тебе можно»: проверка роли. Отсюда 401 и 403. Пароли хранятся только как **bcrypt**-хеш: соль плюс намеренно медленная функция с параметром стоимости (12 раундов), поэтому перебор по утёкшей базе дорог. bcrypt учитывает не больше 72 байт пароля, это проверяется в DTO.

**HTTP Basic** передаёт `login:password` в base64 в каждом запросе, и сервер каждый раз сверяет bcrypt. **Токен** выдаётся один раз при входе и дальше передаётся как `Authorization: Bearer <token>` (RFC 6750). **JWT** (RFC 7519) — это `base64url(header).base64url(payload).base64url(signature)`. В CO-RAG header — `{"alg":"HS256"}`, payload — `{"sub":"10","role":"researcher","iat":…,"exp":…}` (30 минут). Payload **не шифруется**, а только подписывается: прочитать его может любой, подделать без секрета нельзя. **HS256** — HMAC-SHA256 с общим секретом `JWT_SECRET`: им и подписывают, и проверяют. Это удобно, когда проверяющих мало: API и Notification Service в ЛР4, поэтому `decode_token` вынесен в `corag_contracts.auth`. **RS256** подписывает закрытым ключом, а проверяет открытым, так что проверяющим не нужен секрет. Алгоритм при проверке зафиксирован (`algorithms=["HS256"]`), поэтому токен с `alg: none` отвергается.

JWT нельзя «отозвать» до `exp`: сервер его не хранит. Смягчения — короткий срок жизни, список отозванных `jti`, версия токена в БД. В CO-RAG `get_current_user` после проверки подписи читает пользователя из БД. Блокировка и смена роли действуют сразу, а роль для авторизации берётся из БД, не из токена.

### 2.6. Reverse proxy (Nginx)

Reverse proxy принимает запросы клиентов от имени бэкендов. Его задачи: единая точка входа, раздача статики, балансировка, TLS, лимиты, буферизация медленных клиентов. В CO-RAG (`deploy/nginx/nginx.conf`) `location /` отдаёт статическую страницу, а `location /api/` проксирует на API (`proxy_pass http://api/` со слешем срезает префикс `/api`). Заголовки `X-Real-IP` и `X-Forwarded-For` передают бэкенду адрес клиента. `client_max_body_size` по умолчанию равен 1 МБ, поэтому без `210m` скан отклоняется ещё в Nginx с 413. Для корректного `/docs` за прокси API запускается с `API_ROOT_PATH=/api`.

### 2.7. CORS

Браузер запрещает JavaScript-странице читать ответы другого источника (схема + хост + порт). **CORS** — способ сервера разрешить это заголовками `Access-Control-Allow-*`; для «непростых» запросов браузер сначала шлёт preflight `OPTIONS`. В CO-RAG CORS выключен: клиенты — Postman, curl и страницы с того же источника за Nginx. Поэтому preflight с чужого `Origin` получает `405 Method Not Allowed` без разрешающих заголовков.

### Ссылки

1. RFC 9110 — HTTP Semantics (методы, коды 401/403/409/413/422, идемпотентность): <https://www.rfc-editor.org/rfc/rfc9110>
2. Fielding R. Architectural Styles and the Design of Network-based Software Architectures, гл. 5 (REST): <https://ics.uci.edu/~fielding/pubs/dissertation/rest_arch_style.htm>
3. RFC 7519 — JSON Web Token; RFC 6750 — Bearer Token Usage: <https://www.rfc-editor.org/rfc/rfc7519>, <https://www.rfc-editor.org/rfc/rfc6750>
4. FastAPI — Dependencies with yield (early exit and scope); Security — OAuth2 with Password and Bearer: <https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/>, <https://fastapi.tiangolo.com/tutorial/security/>
5. ASGI Specification: <https://asgi.readthedocs.io/en/latest/specs/main.html>; Nginx `ngx_http_proxy_module`: <https://nginx.org/en/docs/http/ngx_http_proxy_module.html>

## 3. Вопросы преподавателя с ответами

1. **Что такое REST и почему API stateless?**
   REST — стиль, в котором сервер предоставляет ресурсы по URI, а клиент работает с ними единым набором методов HTTP, получая представления в JSON. Stateless значит, что сервер не хранит сессию между запросами: каждый запрос несёт JWT в `Authorization`. Поэтому любой воркер Gunicorn (или любой экземпляр сервиса за Nginx) обработает любой запрос.

2. **Какие методы идемпотентны? Чем PUT отличается от PATCH?**
   Идемпотентны GET, HEAD, OPTIONS, PUT и DELETE: повтор оставляет сервер в том же состоянии. PUT заменяет ресурс целиком, PATCH меняет только переданные поля. В CO-RAG всё редактирование — PATCH: `PatchModel.changes()` берёт `model_dump(exclude_unset=True)`, а явный `null` разрешён только для nullable-полей (`schemas/common.py`). POST не идемпотентен: повторный `POST /issues/{id}/ocr` получает 409.

3. **Когда 201, а когда 202?**
   201 Created — ресурс создан к моменту ответа (`POST /issues`, `POST /articles`). 202 Accepted — запрос принят, но работа будет выполнена позже: `POST /issues/{id}/ocr` только ставит задачу в очередь (`queued`), распознавание займёт минуты. Статус проверяется через `GET /ocr-jobs/{id}`, а в ЛР4 появятся уведомления по WebSocket.

4. **Чем 401 отличается от 403?**
   401 — клиент не аутентифицирован: нет токена, подпись неверна или срок истёк. По RFC 9110 такой ответ обязан содержать `WWW-Authenticate` (у нас `Bearer`, `errors.py` → `_handler`). 403 — личность известна, но прав нет (researcher на `POST /issues`) или пользователь заблокирован. Источники — `get_current_user` и `require_role` в `auth/dependencies.py`.

5. **Когда 409, а когда 422?**
   422 — данные запроса недопустимы сами по себе или относительно других данных. Это и ошибки Pydantic (короткий пароль), и правила сервиса: `page_end` больше числа страниц выпуска, несуществующий автор (`ArticleService._check_range`). 409 — запрос конфликтует с текущим состоянием ресурса: e-mail занят, у выпуска уже есть активная задача OCR, автор указан в статьях и его нельзя удалить.

6. **Как устроен DI в FastAPI? Что даёт?**
   Параметр с `Depends(f)` — это объявление зависимости: FastAPI вызывает `f` до обработчика, разрешая её собственные зависимости рекурсивно и кешируя результат на время запроса. У нас цепочка `get_article_service` → `get_article_repository` → `get_session` (`deps.py`), так что роутер не знает, как собирается сервис. Это даёт слабую связанность и простую подмену в тестах: `app.dependency_overrides[get_publisher] = ...` в `tests/api/conftest.py`.

7. **Зачем DTO отдельно от ORM?**
   Внешний контракт API не должен совпадать со схемой БД. DTO решает, какие поля видит клиент (`UserOut` без `password_hash`), в каком виде (`keywords: list[str]` вместо M:N-объектов) и что можно передать на вход (роль при регистрации не принимается вообще). Таблицы можно менять, не ломая клиентов. Плюс валидация на границе: ORM-модель про `EmailStr` ничего не знает.

8. **Как Pydantic валидирует запрос?**
   FastAPI видит в параметре модель (`data: ArticleCreate`), разбирает JSON и вызывает её валидацию. Проверяются типы с приведением (`"5"` → `5` для `int`), ограничения `Field(ge=1, max_length=…)`, `pattern`, `EmailStr` и пользовательские валидаторы: `field_validator` для повторов в `author_ids`, `model_validator` для `page_start <= page_end` (`schemas/article.py`). Все ошибки собираются в один ответ 422 с путём к полю (`loc`).

9. **ASGI против WSGI. Зачем Gunicorn, если есть Uvicorn? Почему не uWSGI?**
   WSGI синхронен: один запрос занимает поток до ответа. ASGI асинхронен, поддерживает WebSocket и конкурентность на event loop. Uvicorn — ASGI-сервер, но это один процесс. Gunicorn добавляет менеджер процессов: несколько воркеров для нескольких ядер и перезапуск упавших. Воркер `uvicorn_worker.UvicornWorker` соединяет одно с другим. uWSGI реализует WSGI и асинхронное FastAPI-приложение не запустит.

10. **Что делают async/await и event loop? Что сломается, если вызвать блокирующую функцию?**
    `await` отдаёт управление event loop, пока операция ждёт ввода-вывода, и цикл в это время обслуживает другие запросы. Блокирующий вызов (синхронный драйвер, тяжёлый CPU-код, обычный `open().write()`) останавливает весь воркер. Поэтому скан пишется через `anyio.open_file`, а PyMuPDF считает страницы в пуле потоков `anyio.to_thread.run_sync` (`IssueService.upload_scan`).

11. **Как устроена загрузка файла (multipart)?**
    `multipart/form-data` делит тело на части с границей `boundary`, у части с файлом есть имя и `Content-Type`. Starlette разбирает тело во временный файл (до 1 МБ в памяти, дальше на диске) и даёт `UploadFile`. Сервис не читает файл целиком: проверяет сигнатуру `%PDF-`, копирует порциями по 1 МиБ в `*.part`, считая размер (больше 200 МБ → 413). Затем PyMuPDF считает страницы (битый PDF → 422), и файл атомарно переименовывается в `SCANS_DIR/{год}-{номер}.pdf`.

12. **Почему публикация задачи OCR — после commit?**
    Брокер и БД — разные системы без общей транзакции. Если опубликовать до commit, воркер может взять задачу, которой ещё нет в БД, а транзакция может и откатиться. Поэтому `OcrService.start` делает явный `await session.commit()` и только потом `publisher.publish_job(...)`. Порядок проверяет тест `test_publish_after_commit`. Обратный риск (commit прошёл, публикация упала) решается в ЛР4 повтором публикации или паттерном outbox.

13. **Как хранятся пароли?**
    Только bcrypt-хеш (`corag_db.passwords`): в строке хеша лежат алгоритм, стоимость (12) и соль, поэтому одинаковые пароли дают разные хеши, а перебор медленный. Сравнение — `bcrypt.checkpw`. bcrypt учитывает не больше 72 байт, поэтому длинный пароль отклоняется в DTO (422), а не обрезается молча.

14. **Структура JWT. HS256 против RS256. Как отозвать токен?**
    Три части base64url через точку: header (`alg`), payload (`sub`, `role`, `iat`, `exp`) и подпись HMAC-SHA256 над первыми двумя частями. Payload читается кем угодно (проверка на jwt.io), но изменить его без секрета нельзя: подпись не сойдётся, тест `test_tampered_role_401`. HS256 — общий секрет для подписи и проверки, RS256 — закрытый ключ подписывает, открытый проверяет. Отозвать JWT до `exp` нельзя, если сервер его не хранит. У нас короткий TTL (30 мин), а блокировка срабатывает сразу, потому что `get_current_user` проверяет `is_active` по БД.

15. **Зачем reverse proxy и что такое `client_max_body_size`?**
    Nginx — единая точка входа: статика на `/`, API на `/api/`, в ЛР4 — WebSocket на `/ws`. В продакшене там же TLS, балансировка и лимиты. `client_max_body_size` ограничивает размер тела запроса, по умолчанию 1 МБ: без настройки `210m` загрузка скана падает с `413 Request Entity Too Large` от самого Nginx, до API запрос не доходит. Проверено: файл 195 МБ проходит, 215 МБ отсекает Nginx.

16. **Что такое CORS и нужен ли он здесь?**
    Это механизм, которым сервер разрешает JavaScript-странице с другого источника читать его ответы (`Access-Control-Allow-Origin`, preflight `OPTIONS`). Ограничивает браузер, а не сервер: curl и Postman CORS не касается. В CO-RAG CORS выключен, потому что клиент — Postman/curl, а веб-страницы раздаются с того же источника через Nginx. Если в ЛР6 появится фронтенд на другом порту, нужен будет `CORSMiddleware` со списком разрешённых источников.
