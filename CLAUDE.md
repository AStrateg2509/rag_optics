# CLAUDE.md — проект CO-RAG

Этот файл Claude Code читает в начале каждой сессии. Здесь собран постоянный контекст проекта.
Задание на конкретную лабу лежит в `plans/lrN.md`. Если оно расходится с этим файлом, правильнее `plans/lrN.md`.

## Что это за проект

CO-RAG — распределённая система для курса «Технологии сетевого программирования».
Автор: Кравченко Иван Антонович, группа 6402-010302D. Проект выполняется индивидуально.

Система делает три вещи:

- переводит сканы журнала «Компьютерная оптика» в текст (OCR моделью DeepSeek-OCR);
- индексирует текст: чанки + эмбеддинги Jina, хранятся в PostgreSQL + pgvector;
- отвечает на вопросы по архиву по схеме наивного RAG с локальной LLM, со ссылками на статью, выпуск и страницу.

Полное ТЗ (ЛР0) лежит в `docs/LR0_TZ.md`, краткая PDF-версия — в `docs/LR0_TZ_short.pdf`. Главное из ТЗ — ниже.

## Архитектура (из ТЗ, менять только по согласованию)

| Компонент | Технологии | Роль |
| --- | --- | --- |
| API Service (`services/api`) | FastAPI, Pydantic v2, SQLAlchemy 2.0 (async) | REST API, auth (JWT), бизнес-логика; вызывает RAG по gRPC, публикует в RabbitMQ |
| RAG Service (`services/rag`) | grpcio (`grpc.aio`), Protobuf, Jina, OpenAI-совместимый клиент | `Ask`, `Search`, `IndexIssue` |
| OCR Worker (`services/ocr_worker`) | aio-pika, PyMuPDF, DeepSeek-OCR (transformers, CUDA) | Очередь `ocr.jobs` → страницы в Markdown → БД → события → `IndexIssue` |
| Notification Service (`services/notification`) | FastAPI WebSocket, aio-pika | Очередь `notifications` → push клиентам по WebSocket |
| WS-клиент (`tools/ws_client`) | websockets | Консольный клиент уведомлений |
| Общие пакеты (`packages/corag-db`, `packages/corag-contracts`) | SQLAlchemy, Pydantic, сгенерированный protobuf | ORM-модели и репозитории; контракты сообщений и gRPC |
| Инфраструктура | PostgreSQL 16 + pgvector, RabbitMQ 3, Ollama, Nginx | — |

Форматы данных:

- JSON — REST, сообщения RabbitMQ, WebSocket;
- Protocol Buffers — gRPC (`proto/rag/v1/rag.proto`).

Роли пользователей: `researcher` (назначается по умолчанию) и `admin` (наследует права исследователя).

### Таблицы БД (10 шт.)

`users`, `issues`, `pages`, `articles`, `authors`, `keywords`,
`article_authors` (M:N, с полем `author_order`), `article_keywords` (M:N), `chunks` (`VECTOR(1024)`, индекс HNSW cosine), `ocr_jobs`.

Статья связана со страницами не внешним ключом, а диапазоном `page_start..page_end` внутри выпуска.
Эталонный DDL — раздел 5.2 полного ТЗ.

### RabbitMQ

- Exchange `ocr` (direct) → очередь `ocr.jobs` (durable).
- Exchange `events` (topic) → очередь `notifications` с привязками `ocr.*` и `issue.*`.
- События: `ocr.queued` (публикует API), `ocr.started`, `ocr.progress`, `ocr.completed`, `ocr.failed`, `issue.indexed`.
- Конверт события: `{event, occurred_at, audience: "admin"|"all", data}`.

## Структура репозитория

```
.
├── CLAUDE.md
├── README.md
├── pyproject.toml            # корень uv workspace + настройки ruff и pytest
├── uv.lock
├── .env.example              # все переменные окружения с комментариями; .env не коммитить
├── docker-compose.dev.yml    # инфраструктура для разработки (postgres, rabbitmq, ollama)
├── docker-compose.yml        # вся система (ЛР5)
├── proto/rag/v1/rag.proto
├── packages/
│   ├── corag-db/             # src/corag_db: base, models, session, repositories, seed; alembic/
│   └── corag-contracts/      # src/corag_contracts: events, topology, rag (сгенерированный pb2)
├── services/
│   ├── api/                  # src/corag_api: main, routers, schemas, services, deps, auth
│   ├── rag/                  # src/corag_rag: server, embedder, chunker, retriever, llm, indexer
│   ├── ocr_worker/           # src/corag_ocr: consumer, engine, pdf, pipeline
│   └── notification/         # src/corag_notify: app, manager, consumer
├── tools/ws_client/
├── deploy/nginx/nginx.conf
├── scripts/                  # demo_lrN.*, gen_proto.sh, seed, import_text_layer.py
├── tests/                    # или tests/ внутри каждого пакета
├── docs/                     # LR0_TZ.md, reports/ (_template.md, lrN.md, final.md)
├── defense/                  # lrN.md (сценарий, теория, вопросы), lrN-cheatsheet.md, final.md
└── plans/                    # lr1.md … lr6.md
```

Каждый сервис — отдельный член uv workspace со своим `pyproject.toml`. Тогда Docker-образ сервиса ставит только его зависимости: torch нужен только в `rag` и `ocr_worker`.

## Инструменты и команды

- Python 3.12, менеджер пакетов **uv**: `uv sync`, `uv add --package <name> <dep>`, `uv run ...`.
- Линтер и форматтер **ruff**: `uv run ruff check . --fix && uv run ruff format .`.
- Тесты **pytest** + pytest-asyncio: `uv run pytest -q`.
- Инфраструктура для разработки: `docker compose -f docker-compose.dev.yml up -d`.
- Окружение: Windows 10 + WSL2 Ubuntu, VS Code. Docker Desktop работает на WSL2-бэкенде. GPU — RTX 3060 Ti, 8 ГБ.

## Соглашения по коду

- Слои в API: router → service → repository → ORM. В роутерах нет бизнес-логики и SQL.
- Сервисы и репозитории внедряются через `Depends`.
- DTO (Pydantic) и ORM-модели — разные классы. Наружу никогда не отдаём `password_hash`.
- Конфигурация — только через `pydantic-settings` и переменные окружения. Никаких захардкоженных хостов и паролей. Любую новую переменную сразу добавляем в `.env.example`.
- Работа с БД асинхронная (`AsyncSession`, asyncpg). Связи загружаются явно (`selectinload`), чтобы не было N+1.
- Доменные исключения (`NotFound`, `Conflict`, `Forbidden`, `ValidationFailed`) переводятся в HTTP-коды в exception handlers. Это 404, 409, 403 и 422.
- Логи пишем через `logging` в консоль, одна строка на шаг: `[api] → gRPC Ask question=...`. На защите процесс обработки показываем именно по логам.
- Идентификаторы в коде — на английском, комментарии и docstrings — на русском.
- Тяжёлые зависимости (torch, transformers) ставим только в сервисы, которым они нужны.
- Код из `corag_contracts/rag` генерируется (`scripts/gen_proto.sh`), руками его не правим.

## Политика GPU (8 ГБ VRAM) — важно

Модели настоящие, без заглушек:

- DeepSeek-OCR — в OCR Worker;
- Jina Embeddings — в RAG Service;
- LLM — в Ollama.

Всё это не помещается в видеопамять одновременно, поэтому:

1. Jina по умолчанию работает на CPU (`EMBED_DEVICE=cpu`). Перевести на `cuda` можно, только если хватает памяти.
2. Перед загрузкой DeepSeek-OCR воркер выгружает модель из Ollama (`POST /api/generate` с `keep_alive: 0`). После задачи он освобождает свою модель (`del`, `gc.collect()`, `torch.cuda.empty_cache()`) при `OCR_UNLOAD_AFTER_JOB=true`.
3. Пока идёт OCR, ответы RAG могут быть медленными. Это известное ограничение, оно описано в README.
4. Если DeepSeek-OCR не помещается в память, сначала уменьшаем режим разрешения модели. Если не помогло — пробуем 8-битную загрузку. Крайний вариант — запуск воркера на GPU-узле кластера: архитектура это позволяет, воркер сам подключается к брокеру.

## Git

- Одна лаба — одна ветка: `lr1-db`, `lr2-rest-api`, `lr3-grpc-rag`, `lr4-rabbitmq-ws`, `lr5-docker`, `lr6-jinja`. Ветки создаются от актуального `main`.
- Коммиты в стиле Conventional Commits на русском: `feat(api): эндпоинты статей`.
- По завершении лабы — PR в `main`. В описании PR: что сделано, как проверить, чек-лист из `plans/lrN.md`.

## Документация по лабе (составляет Claude Code)

Каждую лабу закрывают документы, которые составляет Claude Code. Только Markdown, на русском.
Что именно в них писать для конкретной лабы — разделы 8–9 файла `plans/lrN.md`; готовые промпты — раздел 10.

| Файл | Что это | Объём | Когда |
| --- | --- | --- | --- |
| `defense/lrN.md` | Раздел 1 — сценарий сдачи (подготовка + тайминг). Раздел 2 — минимальная теория. Раздел 3 — вопросы преподавателя с ответами | выступление 5–7 мин; теория 2–3 стр. (≈1000–1500 слов) | после разделов 5–6 плана |
| `defense/lrN-cheatsheet.md` | Шпаргалка: тезисы, команды, «Где в коде», частые ошибки | 1 стр. (≈350–450 слов) | вместе с `defense/lrN.md` |
| `docs/reports/lrN.md` | Отчёт о выполненной работе по шаблону `docs/reports/_template.md` | по шаблону | после `defense/` (теория берётся из раздела 2) |
| `defense/final.md`, `docs/reports/final.md` | Сценарий зачёта и итоговый отчёт по проекту | — | в ЛР5 |

Порядок закрытия лабы:

1. Пройти разделы 5–6 плана.
2. Составить `defense/lrN.md` и `defense/lrN-cheatsheet.md`.
3. Составить отчёт `docs/reports/lrN.md`.
4. Репетиция сдачи по разделу 1 `defense/lrN.md`.
5. PR.

Общие правила:

- **Источник истины — реальный код и реальный вывод команд.** Вывод, логи и ответы API копируются из настоящих прогонов, длинное обрезается с «…». Метрики — только измеренные. Каждая команда из сценария, шпаргалки и отчёта должна быть выполнена хотя бы раз. Что запустить нельзя — помечается TODO.
- Диаграммы — в Mermaid. Ссылки на код — относительные пути с функцией или классом, например `services/api/src/corag_api/services/ocr_service.py` → `OcrService.start`. Фрагменты кода — до 30 строк, с указанием файла.
- **`defense/lrN.md`**:
    - **раздел 1** — «Подготовка до сдачи» (что запустить заранее, какие окна и данные) и таблица `Время | Говорю | Показываю (окно / команда)` на 5–7 минут, последние 1–1,5 мин — резерв на вопросы;
    - **раздел 2** — теория, минимум для уверенных ответов: своими словами, на примерах из CO-RAG, в конце 3–5 ссылок на первоисточники (документация, RFC);
    - **раздел 3** — 10–15 вероятных вопросов по теории и коду, ответ — 2–4 предложения, со ссылкой на код, где уместно.
- **`defense/lrN-cheatsheet.md`** умещается на экран или лист:
    - 5–8 тезисов;
    - команды запуска и проверки;
    - таблица **«Где в коде»** (`понятие → файл → функция/класс`);
    - 3–4 частых ошибки.
- **`docs/reports/lrN.md`** — строго по `docs/reports/_template.md` и его правилам. Скриншоты делает автор: в отчёте остаются заглушки и список TODO.
- Коммиты: `docs(lrN): подготовка к сдаче`, `docs(lrN): отчёт`. Если код в ветке потом меняется, документы обновляются.

## Как работать с планами

1. Перед реализацией прочитай `plans/lrN.md` целиком и сверь его с текущим кодом.
2. Пункты с пометкой **[ОПЦИОНАЛЬНО]** выполняй, только если об этом прямо сказано в запросе.
3. Если версия библиотеки или API модели неясны (DeepSeek-OCR, jina-embeddings-v3, pgvector), сначала проверь README или model card. Не угадывай сигнатуры.
4. Лаба готова, когда отмечены все пункты «Критериев готовности», проходят `ruff` и `pytest`, демо-скрипт отрабатывает, составлены `defense/lrN.md`, `defense/lrN-cheatsheet.md` и `docs/reports/lrN.md` (разделы 8–9 плана).
