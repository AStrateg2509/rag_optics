# ЛР №5. Контейнеризация и развёртывание распределённой системы

**Ветка:** `lr5-docker` · **Технологии:** Docker, Docker Compose, NVIDIA Container Toolkit (GPU в WSL2)
**Зависит от:** ЛР1–ЛР4 · **Результат — финальная сборка проекта для зачёта**
**Отчёт:** `docs/reports/lr5.md` (шаблон `docs/reports/_template.md`) · **Подготовка к сдаче:** `defense/lr5.md` + `defense/lr5-cheatsheet.md` (составляет Claude Code, раздел 8)

## 1. Задание из методички

Развернуть все компоненты в отдельных контейнерах. Минимальный набор контейнеров:

- [ ] PostgreSQL
- [ ] API Service
- [ ] gRPC Service (у нас RAG Service)
- [ ] RabbitMQ
- [ ] Notification Service (+ WebSocket)

Настроить:

- [ ] сетевое взаимодействие контейнеров;
- [ ] переменные окружения;
- [ ] постоянное хранение данных PostgreSQL;
- [ ] автоматический запуск всей системы через docker-compose.

**Сдача:** вся система запускается **одной командой `docker compose up`** и все компоненты корректно взаимодействуют.

В CO-RAG к минимальному набору добавляются:

- `ocr-worker` (GPU);
- `llm` (Ollama, GPU);
- служебные одноразовые `migrate` и `llm-init`;
- **[ОПЦИОНАЛЬНО]** `nginx`.

## 2. Результат лабы

- Dockerfile для каждого сервиса: многоэтапная сборка через uv, запуск не от root, healthcheck.
- `docker-compose.yml`: порядок запуска по healthcheck'ам, тома, `.env`, GPU-резервации.
- После `docker compose up` с нуля система сама:
    - применяет миграции и сид администратора;
    - скачивает LLM;
    - становится готовой к сценариям ЛР2–ЛР4.
- README: запуск, проверка GPU, известные ограничения.

## 3. Структура файлов

```
.dockerignore                           # .venv, models/, data/, .git, __pycache__, tests, docs
docker-compose.yml
.env.example                            # дополнить: COMPOSE_PROJECT_NAME, *_HOST для контейнеров, порты
deploy/docker/python-base.Dockerfile    # [ОПЦИОНАЛЬНО] общий базовый образ, если удобно
services/api/Dockerfile
services/rag/Dockerfile
services/ocr_worker/Dockerfile          # база с CUDA runtime
services/notification/Dockerfile
packages/corag-db/Dockerfile.migrate    # или target "migrate" в Dockerfile API
deploy/postgres/init.sql                # уже есть: CREATE EXTENSION vector
deploy/rabbitmq/rabbitmq.conf           # уже есть: consumer_timeout
deploy/nginx/nginx.conf                 # [ОПЦИОНАЛЬНО] upstream'ы по именам сервисов
README.md
scripts/demo_lr5.sh
```

## 4. Шаги реализации

### Шаг 1. Проверка GPU в Docker (WSL2)

- Docker Desktop с WSL2-бэкендом, актуальный драйвер NVIDIA в Windows.
- Проверка: `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` должна показать RTX 3060 Ti.
- Если GPU не видна — разобраться до начала остальной работы и записать решение в README.

### Шаг 2. Dockerfile'ы (шаблон)

```
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv      # версию uv закрепить
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
COPY services/<svc>/ services/<svc>/
RUN uv sync --frozen --no-dev --package <svc-package>    # ставит только зависимости сервиса

FROM python:3.12-slim
RUN useradd -m app
COPY --from=builder /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1
USER app
CMD [...]
```

- Чтобы слои кэшировались, сначала копируются `pyproject.toml` и `uv.lock` (`uv sync --no-install-workspace`), потом исходники.
- **api:**
    - `gunicorn ... -k uvicorn.workers.UvicornWorker`;
    - `HEALTHCHECK` через `python -c` на `/health`.
- **rag:**
    - torch из **CPU-индекса** (`[tool.uv.sources]`/`[[tool.uv.index]]` с `pytorch-cpu`), потому что `EMBED_DEVICE=cpu` — образ на порядок меньше;
    - healthcheck через gRPC health (`grpc_health_probe` или маленький python-скрипт).
- **ocr_worker:**
    - база `nvidia/cuda:12.x-runtime-ubuntu22.04`, Python ставится через `uv python install 3.12`;
    - torch из CUDA-индекса;
    - версии под требования DeepSeek-OCR из ЛР4.
- **notification:** uvicorn, healthcheck `/health`.
- **migrate:** образ с `corag-db`. Команда: `alembic upgrade head` (или `create_schema`, если Alembic не делался), затем `python -m corag_db.seed --admin-only`.

### Шаг 3. `docker-compose.yml`

| Сервис | Образ | Порты наружу | Тома | depends_on |
| --- | --- | --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` | — (5432 только в dev) | `pgdata`, `init.sql` | — |
| `rabbitmq` | `rabbitmq:3-management` | 15672 (UI) | `rabbitmq.conf` | — |
| `migrate` | `services/api` target migrate | — | — | postgres: `service_healthy` |
| `llm` | `ollama/ollama` | — | `ollama` | — |
| `llm-init` | `ollama/ollama` (`ollama pull $LLM_MODEL` через `OLLAMA_HOST=llm`) | — | — | llm: `service_healthy` |
| `rag` | build | — | `models` (HF_HOME) | migrate: `completed_successfully`, llm-init: `completed_successfully` |
| `api` | build | 8000 | `scans` | migrate, rag: `service_healthy`, rabbitmq: `service_healthy` |
| `ocr-worker` | build | — | `scans`, `models` | rabbitmq, rag: `service_healthy` |
| `notification` | build | 8001 | — | rabbitmq: `service_healthy` |
| `nginx` **[ОПЦИОНАЛЬНО]** | `nginx:stable` | 80 | `nginx.conf`, static | api, notification |

- **Сеть:** одна сеть `backend`, сервисы обращаются друг к другу по имени: `postgres:5432`, `rabbitmq:5672`, `rag:50051`, `llm:11434`. **[ОПЦИОНАЛЬНО]** вторая сеть `frontend` для `nginx` ↔ `api`/`notification`.
- **Переменные:**
    - `env_file: .env` + `environment:` для адресов внутри сети;
    - `.env.example` полный и с комментариями;
    - секреты в образы не попадают.
- **GPU:** у `ocr-worker` и `llm` — `deploy.resources.reservations.devices: [{driver: nvidia, count: all, capabilities: [gpu]}]`.
- **Тома:**
    - `pgdata` — постоянные данные БД;
    - `scans` — общий для `api` и `ocr-worker`;
    - `models` — веса HF, чтобы не скачивать при каждой пересборке;
    - `ollama` — модели LLM.
- **Healthcheck:** у каждого долгоживущего сервиса. `restart: unless-stopped` для сервисов, `restart: "no"` для одноразовых.
- Первый запуск долгий: скачиваются веса Jina, DeepSeek-OCR и LLM. Это нормально, описать в README. **[ОПЦИОНАЛЬНО]** `scripts/prefetch_models.sh` заранее наполняет том `models`.

### Шаг 4. [ОПЦИОНАЛЬНО] Nginx в compose

- Upstream'ы по именам сервисов:
    - `location /api/` → `api:8000`;
    - `location /ws` → `notification:8001` (Upgrade/Connection);
    - `/` — статика.
- Тогда наружу открыт только порт 80, а `api` и `notification` пробрасываются лишь в dev-режиме.

### Шаг 5. Демонстрация и README

`scripts/demo_lr5.sh` после `docker compose up -d`:

- ждёт healthy у всех сервисов (`docker compose ps --format json`);
- прогоняет сокращённый сценарий: логин → создание выпуска → загрузка скана → OCR (события видны в WS-клиенте) → `/rag/ask`;
- в конце: `docker compose down` → `up -d` → проверка, что данные на месте (постоянство `pgdata`).

README:

- требования (Docker Desktop + WSL2, драйвер NVIDIA, ~20+ ГБ диска под образы и модели);
- `cp .env.example .env` → `docker compose up`;
- адреса сервисов;
- как подключить WS-клиент;
- известные ограничения (VRAM, первый запуск).

### Шаг 6. Финальная проверка с нуля

```bash
docker compose down -v          # удалить и тома — проверяем «чистую машину»
docker compose up --build       # одна команда
```

Всё должно подняться без ручных действий.

## 5. Как проверить и показать

```bash
cp .env.example .env
docker compose up --build -d && docker compose ps    # все healthy, migrate/llm-init — exited (0)
uv run --package ws-client ws-client --url ws://localhost:8001/ws --email admin@corag.local
bash scripts/demo_lr5.sh data/samples/scan_3pages.pdf
docker compose logs -f api rag ocr-worker notification   # путь запроса по логам
```

## 6. Критерии готовности

- [ ] Все компоненты в отдельных контейнерах: минимум postgres, api, rag (gRPC), rabbitmq, notification; плюс ocr-worker и llm.
- [ ] Сервисы связываются по именам внутри сети compose; все адреса заданы переменными окружения.
- [ ] Данные PostgreSQL переживают `docker compose down` и `up` (том `pgdata`).
- [ ] `docker compose up` с нуля поднимает систему без ручных шагов: миграции, сид администратора, загрузка LLM.
- [ ] Healthcheck'и и `depends_on` с условиями задают порядок старта; одноразовые сервисы завершаются с кодом 0.
- [ ] GPU доступна `ocr-worker` и `llm`; сквозной сценарий ЛР4 работает в контейнерах.
- [ ] Образы собираются только со своими зависимостями (у `rag` torch для CPU); `.dockerignore` настроен.
- [ ] README описывает запуск и ограничения.
- [ ] **[ОПЦИОНАЛЬНО]** Nginx — единая точка входа на порту 80; отдельная сеть `frontend`.
- [ ] Подготовка к сдаче составлена по разделу 8: `defense/lr5.md` (сценарий 5–7 мин, теория, вопросы с ответами) и `defense/lr5-cheatsheet.md`; команды проверены запуском.
- [ ] `defense/final.md` для зачёта составлен (раздел 8.3).
- [ ] Отчёт `docs/reports/lr5.md` составлен по шаблону: настоящий вывод консоли, схемы, листинги; список TODO (скриншоты) закрыт.
- [ ] Итоговый отчёт `docs/reports/final.md` собран по разделам методички.
- [ ] Пройден сценарий сдачи из `defense/lr5.md` (репетиция).
- [ ] PR `lr5-docker` → `main`.

## 7. Подводные камни

- `depends_on` без `condition` ждёт только старта контейнера, а не готовности сервиса.
- Внутри контейнера `localhost` — это сам контейнер. Адреса только по именам сервисов.
- Образы с CUDA-torch весят несколько гигабайт. Нужны многоэтапная сборка и `.dockerignore`, кэш uv (`--mount=type=cache`) ускоряет пересборку.
- Права на тома: процессу без root нужна запись в `scans` и `models` (`chown` в Dockerfile или `user:` в compose).
- В WSL2 Docker Desktop выделяет ограниченную память. Возможно, придётся увеличить лимит в `.wslconfig`.

## 8. Подготовка к сдаче (составляет Claude Code)

Делается **до отчёта**: раздел 2 «Теория» из `defense/lr5.md` используется в отчёте (раздел 9).
Порядок закрытия лабы:

1. Пройти разделы 5–6.
2. Подготовка к сдаче (этот раздел).
3. Отчёт (раздел 9).
4. Репетиция сдачи.
5. PR.

Общие правила — в CLAUDE.md, раздел «Документация по лабе». Промпт — 10.2.

### 8.1. `defense/lr5.md` — сценарий сдачи, теория, вопросы

#### Раздел 1. Сценарий сдачи (5–7 мин)

**Подготовка:**

- образы собраны заранее (сборку на сдаче не делаем — долго);
- модели в томах;
- `docker compose down` выполнен (тома сохранены);
- WS-клиент и тестовый скан готовы.

| Время | Говорю | Показываю |
| --- | --- | --- |
| 0:00–0:30 | Что и в каких контейнерах | Диаграмма развёртывания |
| 0:30–1:30 | Устройство compose | `docker-compose.yml`: сервисы, healthcheck'и, `depends_on` с условиями, тома, GPU |
| 1:30–2:40 | Запуск одной командой | `docker compose up -d` → `docker compose ps` (всё healthy, `migrate` и `llm-init` — exited 0) |
| 2:40–4:30 | Взаимодействие | Сокращённый сквозной сценарий (`demo_lr5.sh`) + WS-клиент; `docker compose exec api getent hosts postgres` — DNS по имени сервиса |
| 4:30–5:30 | Постоянство данных | `docker compose down` → `up -d` → данные на месте |
| 5:30–7:00 | Резерв на вопросы | — |

#### Раздел 2. Минимальная теория (2–3 стр.)

- Контейнеризация: изоляция процессов, сравнение с ВМ.
- Архитектура Docker: демон, образ, слои, реестр.
- Основные инструкции Dockerfile, многоэтапная сборка.
- Сети и DNS, тома.
- Docker Compose: сервисы, зависимости, healthcheck, профили; конфигурация через окружение (принцип 12-factor).

#### Раздел 3. Вопросы преподавателя с ответами (12–15)

На каждую тему — формулировка вопроса и ответ в 2–4 предложения. Где уместно, ответ ссылается на файл и функцию.

- образ против контейнера;
- слои и кэш сборки;
- многоэтапная сборка;
- контейнер против ВМ (namespaces, cgroups);
- сеть compose и DNS;
- named volume против bind mount;
- `depends_on` и healthcheck;
- переменные окружения и секреты;
- политики restart;
- GPU в контейнере (NVIDIA Container Toolkit, WSL2);
- зачем запускать не от root;
- `.dockerignore`;
- масштабирование (`--scale ocr-worker=2` — что сломается);
- как смотреть логи.

### 8.2. `defense/lr5-cheatsheet.md` — шпаргалка (1 стр.)

- **Тезисы:** контейнеры и их назначение; порядок старта; что хранится в каких томах; адреса по именам сервисов; GPU у `ocr-worker` и `llm`.
- **Команды:** `up -d`, `ps`, `logs -f <svc>`, `exec`, `down` и `down -v`, `build`, `config` (проверка итогового compose), проверка GPU.
- **Где в коде:** Dockerfile каждого сервиса, секции compose, `init.sql`, `rabbitmq.conf`, `.env.example`.
- **Частые ошибки:** `localhost` внутри контейнера; `depends_on` без условия; права на тома; лимит памяти WSL2.

### 8.3. Подготовка к зачёту — `defense/final.md`

Для итоговой демонстрации всей системы, рядом с итоговым отчётом из раздела 9.1:

- **Сценарий зачёта на 5–7 мин** (подготовка + тайминг):
    - `docker compose up` → вопрос к архиву с источниками;
    - загрузка скана и OCR с уведомлениями в WS-клиенте;
    - вопрос по новому выпуску;
    - ссылка на репозиторий.
- **15–20 самых вероятных вопросов по всему курсу** с короткими ответами. Отбираются из `defense/lr1.md`–`lr5.md`, плюс сквозные: почему система распределённая, где синхронное и где асинхронное взаимодействие, JSON против Protobuf.
- **Сводная шпаргалка «Где в коде»** по всем компонентам — в конце файла.

## 9. Отчёт по лабе

Отчёт пишется **после** того, как пройдены разделы 5 и 6 и составлена подготовка к сдаче (раздел 8). Шаблон и правила — `docs/reports/_template.md`.
Коротко: вывод консоли только настоящий, скриншоты — заглушки со списком TODO, теория — по `defense/lr5.md`, не больше страницы.

| Раздел отчёта | Содержание для ЛР5 |
| --- | --- |
| 1. Цель и задание | Пункты методички; Nginx и сеть frontend — «(доп.)» |
| 2. Теория | Образ и контейнер, слои и кэш, многоэтапная сборка, namespaces и cgroups; Compose: сети и DNS, тома, переменные, healthcheck, `depends_on` с условиями; GPU в контейнерах |
| 3.1 Реализовано | Таблица контейнеров (образ, порты, тома, зависимости, healthcheck) по фактическому `docker-compose.yml` |
| 3.2 Схемы | Схема развёртывания: контейнеры, сети, тома, проброшенные порты |
| 3.3 Решения | Отдельные образы с разными зависимостями (torch CPU и CUDA); одноразовые `migrate` и `llm-init`; порядок старта; хранение моделей в томах |
| 4. Демонстрация | `docker compose up --build` с нуля (сокращённый лог), `docker compose ps` (все healthy), сквозной сценарий `demo_lr5.sh`, `down` → `up` и проверка данных; `docker images` (размеры) |
| 5. Тестирование | Вывод `pytest -q`, `ruff check` |
| Приложение А | Один Dockerfile полностью (api) + отличия Dockerfile `ocr_worker` |
| Приложение Б | `docker-compose.yml` полностью, `.env.example`, `.dockerignore`; [опц.] `nginx.conf` |
| Скриншоты (TODO) | Docker Desktop со стеком; `nvidia-smi` в контейнере |

### 9.1. Итоговый отчёт по проекту (для зачёта)

После отчёта ЛР5 Claude Code собирает `docs/reports/final.md` — итоговый отчёт по разделам методички («Оформление отчёта»):

1. **Введение** — задачи ИС и предметная область (из ТЗ, актуализировать).
2. **Сценарии использования** — бизнес-цели (что? измеримо), функции (как?), сценарии (конкретные действия пользователя). Фактические эндпоинты вместо проектных.
3. **Структура базы данных** — логическая и физическая схемы по фактическим моделям (ER-диаграмма + DDL из `pg_dump --schema-only`).
4. **Архитектура приложения** — технологии и обоснование, компоненты, способы взаимодействия; UML-диаграммы компонентов, классов, последовательностей, развёртывания.
5. **Приложения** — листинги модулей (по одному из однотипных), конфигурационные файлы, ссылка на GitHub.

Источники: `docs/LR0_TZ.md` и `docs/reports/lr1.md`–`lr5.md`. Всё, что в реализации отличается от ТЗ, перечислить в подразделе «Отличия от ТЗ» с причинами (например, `ocr.queued`, политика GPU).

## 10. Промпты для Claude Code

### 10.1. Планирование (режим планирования)

```
Прочитай CLAUDE.md и plans/lr5.md, изучи все сервисы (ЛР1–4 в main), их pyproject и переменные окружения. Ветка lr5-docker.
Составь план: Dockerfile для каждого сервиса (uv, многоэтапная сборка, без root, healthcheck; rag — torch для CPU,
ocr_worker — CUDA), docker-compose.yml с healthcheck'ами и depends_on по условиям, тома, .env, GPU-резервации,
одноразовые migrate и llm-init, README и демо. Опционально: Nginx и сеть frontend — ВКЛЮЧИТЬ / НЕТ.
Отдельно перечисли, что надо проверить руками (GPU в WSL2). Код не пиши до одобрения плана.
```

### 10.2. Подготовка к сдаче (после реализации, обычный режим)

```
Лаба 5 реализована и проверена (разделы 5–6 plans/lr5.md пройдены). Составь подготовку к сдаче по разделу 8 plans/lr5.md
и правилам CLAUDE.md («Документация по лабе»): defense/lr5.md (раздел 1 — сценарий сдачи на 5–7 мин с подготовкой и таймингом,
раздел 2 — минимальная теория на 2–3 стр., раздел 3 — вопросы преподавателя с ответами) и defense/lr5-cheatsheet.md
(1 стр.: тезисы, команды, таблица «Где в коде», частые ошибки). Также defense/final.md по разделу 8.3.
Все команды из сценария и шпаргалки выполни сам; примеры вывода — только настоящие. Ссылки на код — реальные пути и функции.
Закоммить: docs(lr5): подготовка к сдаче.
```

### 10.3. Отчёт (после реализации, обычный режим)

```
Лаба 5 реализована и проверена. Составь отчёт docs/reports/lr5.md по шаблону docs/reports/_template.md
и разделу 9 plans/lr5.md и итоговый docs/reports/final.md по разделу 9.1. Теорию возьми из defense/lr5.md (раздел 2) и перескажи своими словами,
не больше страницы. Все команды из раздела «Демонстрация» запусти сам и вставь настоящий вывод (длинный сокращай с «…»);
что запустить нельзя — пометь TODO. Схемы — mermaid по фактическому коду. В конце — список TODO для меня (скриншоты и т. п.).
Закоммить: docs(lr5): отчёт.
```
