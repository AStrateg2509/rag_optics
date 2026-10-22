# ЛР2 — шпаргалка

## Тезисы

1. **Слои**: router (DTO) → service (правила, доменные исключения) → repository (SQL) → ORM.
2. **DI через `Depends`**: сессия → репозитории → сервисы; сессия на запрос = транзакция, `scope="function"` — commit до ответа.
3. **DTO ≠ ORM**: `UserOut` без `password_hash`; 422 от Pydantic ещё до сервиса.
4. **401 ≠ 403**: 401 — кто ты? (+`WWW-Authenticate: Bearer`); 403 — роль не та или пользователь заблокирован.
5. **JWT HS256**: `sub`, `role`, `iat`, `exp` (30 мин); роль и `is_active` — из БД, блокировка действует сразу.
6. **202 для OCR**: задача `queued`; **publish только после commit** (`OcrService.start`).
7. **Скан**: `%PDF-` → порции по 1 МиБ в `*.part` → PyMuPDF в потоке → rename; >200 МБ → 413.
8. **Gunicorn** (процессы) + **UvicornWorker** (event loop); uWSGI — WSGI, не для ASGI.

## Команды

```bash
docker compose -f docker-compose.dev.yml up -d                   # postgres + nginx
uv run python -m corag_db.seed
uv run --package corag-api uvicorn corag_api.main:create_app --factory --reload   # разработка
uv run --package corag-api gunicorn 'corag_api.main:create_app()' \
  -k uvicorn_worker.UvicornWorker -w 2 -b 0.0.0.0:8000 --access-logfile -
bash scripts/demo_lr2.sh                                          # весь сценарий, ~1,3 с
bash scripts/demo_lr2.sh http://localhost/api                     # то же через Nginx
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d 'username=admin@corag.local&password=admin12345' | jq -r .access_token)
curl -s localhost:8000/users/me -H "Authorization: Bearer $TOKEN" | jq
curl -s localhost/api/health                                      # через Nginx
uv run pytest -q tests/api && uv run ruff check .                 # 51 passed
```

## Эндпоинты (сжато)

| Ресурс | researcher+ | admin |
| --- | --- | --- |
| `/auth` | `register` 201/409, `login` 200/401/403 (все) | — |
| `/users` | `GET /me` | `GET /users`, `PATCH /users/{id}` (409 — над собой) |
| `/issues` | `GET`, `GET /{id}`, `GET /{id}/pages/{n}` | `POST` 201, `PATCH`, `DELETE` 204, `POST /{id}/scan` 200/413/422, `POST /{id}/ocr` **202**/409 |
| `/articles` | `GET ?year&author&keyword&issue_id`, `GET /{id}` | `POST` 201/409/422, `PATCH`, `DELETE` |
| `/authors`, `/keywords` | `GET ?q` | `POST`, `PATCH` (авторы), `DELETE` (автор со статьями → 409) |
| `/ocr-jobs/{id}`, `/health` | `/health` — все | `GET /ocr-jobs/{id}` |

## Где в коде (`services/api/src/corag_api/…`)

| Понятие | Файл | Функция / класс |
| --- | --- | --- |
| Фабрика, lifespan, роутеры | `main.py` | `create_app`, `lifespan` |
| Сессия на запрос, DI | `deps.py` | `get_session`, `SessionDep`, `get_article_service` |
| Текущий пользователь, роли | `auth/dependencies.py` | `get_current_user`, `require_role` |
| JWT | `packages/corag-contracts/…/auth.py` | `create_access_token`, `decode_token` |
| Исключения → HTTP | `errors.py` | `register_exception_handlers`, `STATUS_CODES` |
| Статьи | `services/article_service.py` | `ArticleService.create`, `_check_range` |
| Загрузка скана | `services/issue_service.py` | `IssueService.upload_scan` |
| OCR 202, commit → publish | `services/ocr_service.py` | `OcrService.start` |
| DTO | `schemas/article.py`, `schemas/common.py` | `ArticleCreate`, `ArticleOut`, `Paginated`, `PatchModel` |
| Прокси | `deploy/nginx/nginx.conf` | `location /api/`, `client_max_body_size` |

## Частые ошибки

- **`await file.read()` целиком** — 200 МБ в памяти воркера; читать порциями.
- **`MissingGreenlet` в DTO** — связь не загружена заранее; нужен `selectinload`.
- **413 за Nginx** — `client_max_body_size` по умолчанию 1m (у нас 210m); ответ — HTML от Nginx, не JSON.
- **Commit после ответа** — yield-зависимость по умолчанию закрывается после ответа; `scope="function"`.
