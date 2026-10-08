# ЛР №3. Сервисное взаимодействие через gRPC (RAG Service)

**Ветка:** `lr3-grpc-rag` · **Технологии:** gRPC (`grpcio`, `grpc.aio`), Protocol Buffers, Jina Embeddings, Ollama (OpenAI-совместимый API)
**Зависит от:** ЛР1 (`ChunkRepository.nearest`, `PageRepository`), ЛР2 (API Service) · **Готовит почву для:** ЛР4 (OCR Worker вызывает `IndexIssue`)
**Отчёт:** `docs/reports/lr3.md` (шаблон `docs/reports/_template.md`) · **Подготовка к сдаче:** `defense/lr3.md` + `defense/lr3-cheatsheet.md` (составляет Claude Code, раздел 8)

## 1. Задание из методички

Создать отдельный gRPC-сервис с дополнительным функционалом по бизнес-логике. FastAPI-сервер вызывает его методы во время обработки запроса. Сервис должен:

- [ ] иметь собственный gRPC-интерфейс;
- [ ] описывать интерфейс в `.proto`-файле;
- [ ] принимать запросы от API Service;
- [ ] возвращать результат вычислений через gRPC.

**Сдача:** в консоли видно, как обрабатывается пользовательский запрос и как в этом участвует gRPC-сервис. API Service и gRPC-сервис запущены в **отдельных процессах**.

В CO-RAG gRPC-сервис — это **RAG Service**. Его методы:

- `Ask` — ответ с источниками;
- `Search` — top-k фрагментов;
- `IndexIssue` — чанкинг и эмбеддинги страниц выпуска.

## 2. Результат лабы

- `proto/rag/v1/rag.proto` и сгенерированный код в `corag-contracts`.
- `services/rag`:
    - асинхронный gRPC-сервер;
    - настоящая модель Jina для эмбеддингов;
    - чанкер, ретривер (pgvector), клиент LLM (Ollama);
    - индексатор.
- В API Service: `RagClient` и эндпоинты `POST /rag/ask`, `POST /rag/search`.
- Ollama добавлен в `docker-compose.dev.yml`, LLM запускается локально на GPU.
- `scripts/demo_lr3.sh`; логи двух процессов показывают путь запроса.

## 3. Структура файлов

```
proto/rag/v1/rag.proto
scripts/gen_proto.sh                     # grpc_tools.protoc → packages/corag-contracts/src/corag_contracts/rag/v1/
packages/corag-contracts/
  pyproject.toml                         # grpcio, protobuf, pydantic
  src/corag_contracts/
    rag/v1/rag_pb2.py rag_pb2.pyi rag_pb2_grpc.py   # сгенерировано
    rag_client.py                        # RagClient (async): ask / search / index_issue, deadline, маппинг ошибок
services/rag/
  pyproject.toml                         # grpcio, grpcio-health-checking, grpcio-reflection, corag-db, corag-contracts,
                                         # torch, transformers, einops, openai (или httpx), numpy
  src/corag_rag/
    settings.py                          # RAG_GRPC_PORT=50051, EMBED_MODEL, EMBED_DEVICE=cpu, EMBED_DIM=1024,
                                         # CHUNK_TOKENS=512, CHUNK_OVERLAP=64, TOP_K=5, MIN_SCORE, LLM_BASE_URL, LLM_MODEL,
                                         # LLM_TEMPERATURE=0.1, LLM_TIMEOUT_S
    embedder.py                          # Embedder (протокол) + JinaEmbedder: embed_queries / embed_passages
    chunker.py                           # MarkdownChunker: страница → список чанков по токенам с перекрытием
    retriever.py                         # Retriever: embed(query) → ChunkRepository.nearest → фильтр MIN_SCORE
    llm.py                               # LLMClient (протокол) + OpenAICompatibleClient
    prompts.py                           # системный промпт и сборка контекста с нумерацией [1]..[k]
    indexer.py                           # Indexer.index_issue(issue_id)
    servicer.py                          # RagServicer(rag_pb2_grpc.RagServiceServicer)
    server.py                            # main(): grpc.aio.server, health, reflection, graceful shutdown
services/api/src/corag_api/
  routers/rag.py  schemas/rag.py         # AskIn/AskOut/CitationOut/SearchIn
  deps.py                                # get_rag_client (один канал на приложение, создаётся в lifespan)
scripts/demo_lr3.sh
tests/rag/
```

## 4. Шаги реализации

### Шаг 1. Контракт `rag.proto`

- Взять черновик из приложения А полного ТЗ (`package corag.rag.v1`).
- Сообщения: `Filters`, `AskRequest`, `Citation`, `AskResponse` (с флагом `found`), `SearchRequest`, `SearchResponse`, `IndexIssueRequest`, `IndexIssueResponse`.
- В `scripts/gen_proto.sh` вызвать `python -m grpc_tools.protoc` с флагами `--python_out`, `--grpc_python_out`, `--pyi_out`.
- Известная проблема: сгенерированный `rag_pb2_grpc.py` импортирует `rag_pb2` абсолютным путём. Чтобы импорты работали как `corag_contracts.rag.v1...`, нужно либо разложить `-I`/выходной каталог так, либо исправлять импорт в скрипте генерации (sed). Сгенерированный код коммитим.

### Шаг 2. Эмбеддер Jina

- Модель `jinaai/jina-embeddings-v3`: `AutoModel.from_pretrained(..., trust_remote_code=True)`, метод `encode(texts, task=...)`.
    - Для вопросов — `task="retrieval.query"`, для чанков — `task="retrieval.passage"`.
    - **Сигнатуру и зависимости сверить с model card** до написания кода.
- `EMBED_DEVICE`:
    - `cpu` по умолчанию — экономим VRAM для Ollama и OCR;
    - `cuda` — в fp16.
- Векторы L2-нормировать. Размерность проверить по `EMBED_DIM`.
- Веса кэшируются в `HF_HOME` (по умолчанию `./models/hf`, каталог в `.gitignore`). Модель загружается **один раз** при старте сервера; на первом запуске показать прогресс скачивания.

### Шаг 3. Чанкер

- Делит Markdown страницы на абзацы, затем набирает чанки по количеству токенов (токенизатор той же модели) до `CHUNK_TOKENS` с перекрытием `CHUNK_OVERLAP`.
- Формулы `$$…$$` и таблицы не разрезает посередине, если блок помещается в лимит.
- Короткие хвосты (< 50 токенов) склеивает с предыдущим чанком.
- Чанк всегда в пределах одной страницы: так ссылка в ответе указывает на точную страницу.

### Шаг 4. Индексатор (`IndexIssue`)

1. Загрузить страницы выпуска, у которых есть `text_md`. Если их нет → `FAILED_PRECONDITION`.
2. Нарезать чанки, посчитать эмбеддинги пачками (`EMBED_BATCH=32`).
3. В одной транзакции `ChunkRepository.replace_for_pages`: удалить старые чанки этих страниц и вставить новые. Повторный вызов идемпотентен.
4. Статус выпуска → `indexed`. Вернуть `pages_indexed` и `chunks_indexed`.
5. Логировать прогресс в консоль: `[rag] IndexIssue issue=12: 96 pages → 410 chunks (38.2 s)`.

### Шаг 5. Ретривер и LLM

- `Retriever.search(query, top_k, filters)`:
    - эмбеддинг вопроса;
    - `nearest()` (SQL из раздела 5.3 ТЗ, `LEFT JOIN articles` по диапазону страниц);
    - отбросить результаты с `score < MIN_SCORE`.
- `OpenAICompatibleClient`: `POST {LLM_BASE_URL}/chat/completions` (Ollama: `http://localhost:11434/v1`), модель `LLM_MODEL`, `temperature=0.1`, таймаут.
- Промпт (`prompts.py`) на русском:
    - «Отвечай только по приведённым фрагментам; ставь ссылки вида [n]; если ответа нет во фрагментах — так и скажи»;
    - фрагменты нумеруются с подписью «(статья, год, выпуск, стр.)».
- Ollama:
    - добавить в `docker-compose.dev.yml` сервис `ollama/ollama` с GPU (`deploy.resources.reservations.devices`), томом `ollama` и портом 11434;
    - модель: `docker compose exec ollama ollama pull $LLM_MODEL`;
    - под 8 ГБ VRAM подойдёт instruct-модель 3–8B в квантовании Q4 (кандидаты сравнить на месте, итоговое имя — в `.env`).

### Шаг 6. gRPC-сервер

- `RagServicer`:
    - `Ask`:
        - пустой вопрос → `INVALID_ARGUMENT`;
        - ничего не нашлось → `found=false` и стандартный ответ **без вызова LLM**;
        - иначе — LLM, ответ, `citations` (по порядку [1]..[k]);
    - `Search` — только ретривер;
    - `IndexIssue` — индексатор.
- Ошибки:
    - LLM недоступна → `UNAVAILABLE`;
    - таймаут → `DEADLINE_EXCEEDED`;
    - выпуск не найден → `NOT_FOUND`.
- Сервер:
    - `grpc.aio.server()`, порт из настроек;
    - `grpcio-health-checking` — понадобится для healthcheck в ЛР5;
    - `grpcio-reflection` — для отладки через `grpcurl`;
    - корректная остановка по SIGTERM.
- Логи: `[rag] ← Ask "…" top_k=5` → `[rag] retrieved 5 chunks (best 0.83)` → `[rag] → LLM qwen… 3.1 s` → `[rag] → AskResponse 5 citations`.

### Шаг 7. Интеграция в API Service

- `RagClient` в `corag_contracts.rag_client`, чтобы его же использовал OCR Worker:
    - один `grpc.aio.insecure_channel(RAG_ADDR)` на процесс;
    - deadline: `Ask` 120 с, `Search` 15 с, `IndexIssue` 30 мин;
    - перевод protobuf → Pydantic.
- Канал создаётся в lifespan API, через `Depends` попадает в роутер `rag.py`.
- `POST /rag/ask` (researcher+): тело `AskIn {question, top_k?, year_from?, year_to?}` → `AskOut {answer, found, citations[]}`.
- `POST /rag/search` — аналогично.
- Перевод gRPC-ошибок в HTTP:

| gRPC | HTTP |
| --- | --- |
| `INVALID_ARGUMENT` | 422 |
| `NOT_FOUND` | 404 |
| `UNAVAILABLE` | 503 |
| `DEADLINE_EXCEEDED` | 504 |

- Логи API: `[api] → gRPC Ask` / `[api] ← gRPC Ask 4.2 s, 5 citations`.
- Временный служебный эндпоинт `POST /issues/{id}/reindex` (admin) вызывает `IndexIssue`. Так до ЛР4 можно проиндексировать сид-данные настоящими эмбеддингами вместо случайных векторов из ЛР1.

### Шаг 8. Тесты

- Юнит-тесты:
    - чанкер (границы, перекрытие, формулы);
    - сборка промпта;
    - перевод ошибок.
- Тест сервисера:
    - в процессе поднимается `grpc.aio.server` на свободном порту;
    - эмбеддер и LLM подменяются **тестовыми двойниками только в тестах**;
    - проверки: `Ask` с найденными и ненайденными фрагментами, `IndexIssue` идемпотентен.
- Интеграционный тест с настоящей моделью помечен `@pytest.mark.slow`.

## 5. Как проверить и показать

```bash
docker compose -f docker-compose.dev.yml up -d          # postgres + ollama
docker compose -f docker-compose.dev.yml exec ollama ollama pull "$LLM_MODEL"
bash scripts/gen_proto.sh
# терминал 1:
uv run --package corag-rag python -m corag_rag.server
# терминал 2:
uv run --package corag-api uvicorn corag_api.main:create_app --factory
# терминал 3:
bash scripts/demo_lr3.sh     # логин → reindex выпусков → /rag/search → /rag/ask (+ вопрос «мимо» архива → found=false)
grpcurl -plaintext localhost:50051 list                 # благодаря reflection
```

На защите показываем два окна логов рядом: запрос пришёл в API → ушёл по gRPC → RAG сделал поиск и вызвал LLM → ответ вернулся.

## 6. Критерии готовности

- [ ] `rag.proto` описывает 3 метода; код генерируется скриптом.
- [ ] RAG Service — отдельный процесс; API вызывает его по gRPC во время обработки `/rag/ask` и `/rag/search`.
- [ ] Используются настоящие эмбеддинги Jina; `IndexIssue` заполняет `chunks`, повторный вызов не плодит дубли.
- [ ] `Ask` возвращает ответ LLM с `citations` (статья, выпуск, страница, фрагмент); вопрос вне архива → `found=false` без вызова LLM.
- [ ] Ошибки gRPC корректно переводятся в HTTP-коды.
- [ ] Логи обоих процессов наглядно показывают путь запроса.
- [ ] `pytest` и `ruff` проходят.
- [ ] Подготовка к сдаче составлена по разделу 8: `defense/lr3.md` (сценарий 5–7 мин, теория, вопросы с ответами) и `defense/lr3-cheatsheet.md`; команды проверены запуском.
- [ ] Отчёт `docs/reports/lr3.md` составлен по шаблону: настоящий вывод консоли, схемы, листинги; список TODO (скриншоты) закрыт.
- [ ] Пройден сценарий сдачи из `defense/lr3.md` (репетиция).
- [ ] PR `lr3-grpc-rag` → `main`.

## 7. Подводные камни

- `jina-embeddings-v3` требует `trust_remote_code=True` и может тянуть дополнительные пакеты. На CPU первый `encode` медленный — сделать прогрев при старте.
- Ollama, Jina на CUDA и позже DeepSeek-OCR вместе не поместятся в 8 ГБ. Отсюда `EMBED_DEVICE=cpu` (см. политику GPU в CLAUDE.md).
- Нельзя создавать gRPC-канал на каждый запрос: только один канал на процесс.
- Блокирующий `model.encode` в async-сервисере останавливает event loop. Вызывать через `asyncio.to_thread`.

## 8. Подготовка к сдаче (составляет Claude Code)

Делается **до отчёта**: раздел 2 «Теория» из `defense/lr3.md` используется в отчёте (раздел 9).
Порядок закрытия лабы:

1. Пройти разделы 5–6.
2. Подготовка к сдаче (этот раздел).
3. Отчёт (раздел 9).
4. Репетиция сдачи.
5. PR.

Общие правила — в CLAUDE.md, раздел «Документация по лабе». Промпт — 10.2.

### 8.1. `defense/lr3.md` — сценарий сдачи, теория, вопросы

#### Раздел 1. Сценарий сдачи (5–7 мин)

**Подготовка:**

- Ollama запущен, модель загружена (сделать один прогревочный запрос);
- RAG Service прогрет: модель эмбеддингов загружена;
- выпуски проиндексированы;
- два терминала с логами RAG и API рядом;
- готовые вопросы: один по архиву, один вне архива.

| Время | Говорю | Показываю |
| --- | --- | --- |
| 0:00–0:40 | Зачем RAG вынесен в отдельный gRPC-сервис | Диаграмма компонентов |
| 0:40–1:40 | Контракт и кодогенерация | `rag.proto`, `scripts/gen_proto.sh`, сгенерированные `*_pb2*.py` |
| 1:40–4:20 | Путь запроса | `POST /rag/ask` → в логах: API → gRPC → эмбеддинг → поиск → LLM → ответ; источники в JSON; вопрос вне архива → `found=false`; остановить RAG → 503 |
| 4:20–5:20 | Код | `RagServicer.Ask`, `RagClient` (deadline, маппинг ошибок); `grpcurl -plaintext localhost:50051 list` |
| 5:20–7:00 | Резерв на вопросы | — |

#### Раздел 2. Минимальная теория (2–3 стр.)

- RPC и его отличие от REST.
- gRPC: HTTP/2 (фреймы, мультиплексирование), `.proto`, кодогенерация, 4 типа вызовов, deadlines, статусы, health checking.
- Protocol Buffers: теги полей, двоичное кодирование в общих чертах, правила эволюции схемы.
- Минимум по RAG:
    - эмбеддинги;
    - векторный поиск и ANN;
    - метрики сходства;
    - чанкинг;
    - сборка промпта с источниками;
    - ограничения наивного RAG.

#### Раздел 3. Вопросы преподавателя с ответами (12–15)

На каждую тему — формулировка вопроса и ответ в 2–4 предложения. Где уместно, ответ ссылается на файл и функцию.

- RPC против REST;
- преимущества HTTP/2;
- Protobuf: номера полей и обратная совместимость;
- 4 типа вызовов gRPC;
- deadlines и статус-коды;
- кодогенерация stub'ов;
- почему отдельный процесс;
- gRPC в браузере;
- как работает наивный RAG;
- эмбеддинги и косинусное сходство;
- выбор размера чанка;
- борьба с галлюцинациями (порог, `found=false`);
- почему Jina на CPU;
- блокирующий код в async (`to_thread`);
- один канал на процесс.

### 8.2. `defense/lr3-cheatsheet.md` — шпаргалка (1 стр.)

- **Тезисы:** 3 метода; unary-вызовы; deadline'ы; `found=false` без LLM; один канал; inference через `to_thread`.
- **Команды:** генерация proto, запуск RAG и API, `ollama pull`, curl на `/rag/ask`, grpcurl, pytest.
- **Где в коде:** `rag.proto`, `RagServicer`, `JinaEmbedder`, `MarkdownChunker`, `Retriever`, `prompts`, `Indexer`, `RagClient`, роутер `rag`.
- **Частые ошибки:** импорт сгенерированного `pb2`; канал на каждый запрос; блокировка event loop; нехватка VRAM.

## 9. Отчёт по лабе

Отчёт пишется **после** того, как пройдены разделы 5 и 6 и составлена подготовка к сдаче (раздел 8). Шаблон и правила — `docs/reports/_template.md`.
Коротко: вывод консоли только настоящий, скриншоты — заглушки со списком TODO, теория — по `defense/lr3.md`, не больше страницы.

| Раздел отчёта | Содержание для ЛР3 |
| --- | --- |
| 1. Цель и задание | Пункты методички |
| 2. Теория | RPC и REST, gRPC поверх HTTP/2, Protobuf (номера полей, совместимость), типы вызовов, deadline и статусы; кратко — наивный RAG и эмбеддинги |
| 3.1 Реализовано | Таблица: метод gRPC → назначение → где вызывается (API, OCR Worker) |
| 3.2 Схемы | Компоненты API ↔ RAG ↔ PostgreSQL ↔ Ollama; sequence `Ask` (из ТЗ, по фактическому коду) |
| 3.3 Решения | Почему RAG вынесен в отдельный сервис; эмбеддер на CPU (политика GPU); порог `MIN_SCORE` и `found=false`; deadline'ы; таблица «gRPC → HTTP» |
| 4. Демонстрация | Логи двух процессов (API и RAG) на один `/rag/ask`; ответ JSON с citations; вопрос вне архива → `found=false`; `grpcurl list` / `describe`; вывод `IndexIssue` |
| 5. Тестирование | Вывод `pytest -q tests/rag` (без slow), `ruff check` |
| Приложение А | `proto/rag/v1/rag.proto` полностью; `servicer.py`, `rag_client.py`, `retriever.py`, `prompts.py` |
| Приложение Б | `scripts/gen_proto.sh`, секция RAG и LLM в `.env.example`, сервис `ollama` в `docker-compose.dev.yml` |
| Скриншоты (TODO) | Два терминала с логами рядом; ответ в Postman |

## 10. Промпты для Claude Code

### 10.1. Планирование (режим планирования)

```
Прочитай CLAUDE.md и plans/lr3.md, изучи код API Service и corag-db (ЛР1–2 в main). Ветка lr3-grpc-rag.
Составь план: rag.proto и генерация, RAG Service (эмбеддер Jina, чанкер, ретривер, LLM через Ollama, индексатор,
gRPC-сервер), RagClient и эндпоинты /rag/* в API, тесты, демо-скрипт.
Перед планом проверь model card jinaai/jina-embeddings-v3 (API encode, task, зависимости) и актуальные версии grpcio.
Учти политику GPU из CLAUDE.md. Код не пиши до одобрения плана.
```

### 10.2. Подготовка к сдаче (после реализации, обычный режим)

```
Лаба 3 реализована и проверена (разделы 5–6 plans/lr3.md пройдены). Составь подготовку к сдаче по разделу 8 plans/lr3.md
и правилам CLAUDE.md («Документация по лабе»): defense/lr3.md (раздел 1 — сценарий сдачи на 5–7 мин с подготовкой и таймингом,
раздел 2 — минимальная теория на 2–3 стр., раздел 3 — вопросы преподавателя с ответами) и defense/lr3-cheatsheet.md
(1 стр.: тезисы, команды, таблица «Где в коде», частые ошибки).
Все команды из сценария и шпаргалки выполни сам; примеры вывода — только настоящие. Ссылки на код — реальные пути и функции.
Закоммить: docs(lr3): подготовка к сдаче.
```

### 10.3. Отчёт (после реализации, обычный режим)

```
Лаба 3 реализована и проверена. Составь отчёт docs/reports/lr3.md по шаблону docs/reports/_template.md
и разделу 9 plans/lr3.md. Теорию возьми из defense/lr3.md (раздел 2) и перескажи своими словами,
не больше страницы. Все команды из раздела «Демонстрация» запусти сам и вставь настоящий вывод (длинный сокращай с «…»);
что запустить нельзя — пометь TODO. Схемы — mermaid по фактическому коду. В конце — список TODO для меня (скриншоты и т. п.).
Закоммить: docs(lr3): отчёт.
```
