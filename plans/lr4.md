# ЛР №4. Асинхронный обмен сообщениями и уведомления в реальном времени

**Ветка:** `lr4-rabbitmq-ws` · **Технологии:** RabbitMQ (aio-pika), WebSocket (FastAPI, websockets), DeepSeek-OCR (transformers, CUDA), PyMuPDF
**Зависит от:** ЛР2 (`OcrService`, `JobPublisher`, JWT), ЛР3 (`RagClient.index_issue`) · **Готовит почву для:** ЛР5
**Отчёт:** `docs/reports/lr4.md` (шаблон `docs/reports/_template.md`) · **Подготовка к сдаче:** `defense/lr4.md` + `defense/lr4-cheatsheet.md` (составляет Claude Code, раздел 8)

## 1. Задание из методички

Добавить в проект уведомления: сервис уведомлений (Notification Service) и клиентское приложение, которое их получает.

- [ ] При значимых событиях API Service публикует сообщения в брокер.
- [ ] Notification Service:
    - [ ] подписывается на очередь;
    - [ ] получает события из RabbitMQ;
    - [ ] формирует уведомления для пользователей;
    - [ ] предоставляет WebSocket-интерфейс;
    - [ ] передаёт уведомления подключённым клиентам.
- [ ] Клиентское приложение:
    - [ ] подключается к WebSocket-серверу;
    - [ ] получает уведомления в реальном времени;
    - [ ] отображает их.

**Сдача:** виден процесс обработки запроса и участие в нём сервиса уведомлений. API Service, Notification Service и клиент — **отдельные процессы**.

В CO-RAG через брокер идёт ещё и сама задача OCR: API → очередь `ocr.jobs` → **OCR Worker**. Воркер публикует события прогресса. Так ЛР4 закрывает функции А2–А4 и Ф6 из ТЗ.

## 2. Результат лабы

- RabbitMQ в `docker-compose.dev.yml`. Топология объявляется кодом, одинаково во всех сервисах.
- Контракты сообщений (Pydantic) в `corag-contracts`.
- API: `AmqpPublisher` вместо `LoggingPublisher`. Публикует задачу в `ocr.jobs` и событие `ocr.queued`.
- `services/ocr_worker`: настоящий DeepSeek-OCR на GPU. Задачи берёт по одной, подтверждает вручную, умеет продолжить прерванную работу. После OCR вызывает `IndexIssue`.
- `services/notification`: потребитель `notifications` + WebSocket `/ws` с проверкой JWT и маршрутизацией по `audience`.
- `tools/ws_client`: консольный клиент.
- `scripts/demo_lr4.sh`: сквозной сценарий на настоящем скане.

## 3. Структура файлов

```
docker-compose.dev.yml                  # + rabbitmq:3-management (5672, 15672), deploy/rabbitmq/rabbitmq.conf
deploy/rabbitmq/rabbitmq.conf           # consumer_timeout = 7200000 (2 ч, долгие задачи OCR)
packages/corag-contracts/src/corag_contracts/
  topology.py                           # константы exchange/queue/routing key + declare_topology(channel)
  messages.py                           # OcrJobMessage
  events.py                             # EventEnvelope[T], OcrQueued, OcrStarted, OcrProgress, OcrCompleted, OcrFailed, IssueIndexed
  auth.py                               # decode_token (общий для API и Notification), если не вынесен в ЛР2
services/api/src/corag_api/publishers.py  # + AmqpPublisher (aio-pika RobustConnection, publisher confirms)
services/ocr_worker/
  pyproject.toml                        # aio-pika, pymupdf, torch (CUDA), transformers, pillow, httpx, corag-db, corag-contracts
  src/corag_ocr/
    settings.py                         # AMQP_URL, SCANS_DIR, OCR_MODEL=deepseek-ai/DeepSeek-OCR, OCR_DPI=200,
                                        # OCR_MODE (режим разрешения модели), OCR_UNLOAD_AFTER_JOB=true, LLM_BASE_URL/LLM_MODEL (для выгрузки Ollama)
    pdf.py                              # render_page(pdf, n, dpi) → PNG во временный каталог
    engine.py                           # OcrEngine (протокол) + DeepSeekOcrEngine: load() / recognize(image) → markdown / unload()
    gpu.py                              # unload_ollama_model(), free_cuda()
    pipeline.py                         # process_job(msg): основной сценарий, публикация событий
    consumer.py  main.py                # подписка на ocr.jobs, prefetch=1, ручной ack
services/notification/
  pyproject.toml                        # fastapi, uvicorn, aio-pika, corag-contracts
  src/corag_notify/
    settings.py  manager.py             # ConnectionManager: connect/disconnect/send_to(audience)
    consumer.py                         # фоновая задача: очередь notifications → manager
    app.py                              # FastAPI: lifespan (consumer), WS /ws?token=..., GET /health
tools/ws_client/
  pyproject.toml  src/ws_client/main.py # websockets + httpx: логин → /ws → печать событий, переподключение
scripts/demo_lr4.sh
tests/notify/  tests/ocr/  tests/contracts/
```

## 4. Шаги реализации

### Шаг 1. Брокер и топология

- RabbitMQ в `docker-compose.dev.yml`:
    - образ `rabbitmq:3-management`, пользователь и пароль из `.env`;
    - healthcheck `rabbitmq-diagnostics -q ping`;
    - конфиг с `consumer_timeout` (по умолчанию 30 мин, а OCR большого выпуска может идти дольше).
- `declare_topology(channel)` — идемпотентная функция, её вызывают все сервисы при старте:
    - exchange `ocr`, тип `direct`, durable → очередь `ocr.jobs` (durable), привязка с ключом `ocr.jobs`;
    - exchange `events`, тип `topic`, durable → очередь `notifications` (durable), привязки `ocr.*` и `issue.*`;
    - **[ОПЦИОНАЛЬНО]** dead-letter exchange `ocr.dlx` и очередь `ocr.jobs.dead` для «ядовитых» сообщений.

### Шаг 2. Контракты

- `OcrJobMessage {job_id, issue_id, scan_path, requested_by}`.
- `EventEnvelope {event, occurred_at, audience: Literal["admin","all"], data}` + модели `data` по таблице:

| event | Кто публикует | audience | data |
| --- | --- | --- | --- |
| `ocr.queued` | **API Service** | admin | `job_id, issue_id` |
| `ocr.started` | OCR Worker | admin | `job_id, issue_id, pages_total` |
| `ocr.progress` | OCR Worker | admin | `job_id, pages_done, pages_total` |
| `ocr.completed` | OCR Worker | admin | `job_id, issue_id, duration_s` |
| `ocr.failed` | OCR Worker | admin | `job_id, page_number, error` |
| `issue.indexed` | OCR Worker | all | `issue_id, year, number, chunks_indexed` |

`ocr.queued` — небольшое дополнение к ТЗ. Методичка прямо требует, чтобы события публиковал API Service.

- Сериализация — `model_dump_json()`, `content_type="application/json"`, `delivery_mode=PERSISTENT`.

### Шаг 3. Публикация из API

- `AmqpPublisher`:
    - `connect_robust` в lifespan;
    - канал с publisher confirms;
    - `publish_job(msg)` → exchange `ocr`;
    - `publish_event(env)` → exchange `events` с routing key = `event`.
- `OcrService.start`: после commit отправляет `OcrJobMessage`, затем событие `ocr.queued`.
- Если брокер недоступен: задача помечается `failed` с текстом ошибки, API отвечает 503.

### Шаг 4. OCR Worker

1. **Потребитель:**
    - `channel.set_qos(prefetch_count=1)`;
    - `queue.consume(handler, no_ack=False)`;
    - внутри обработчика — `async with message.process(requeue=False)` либо явные `ack` / `nack`.
2. **Подготовка GPU:**
    - `unload_ollama_model()` — `POST {OLLAMA}/api/generate` с телом `{"model": LLM_MODEL, "keep_alive": 0}`;
    - затем ленивая загрузка DeepSeek-OCR (bf16, cuda).
3. **Движок `DeepSeekOcrEngine`:**
    - загрузка через `transformers` с `trust_remote_code=True`;
    - промпт для документа — вида `"<image>\n<|grounding|>Convert the document to markdown."`;
    - режим разрешения из `OCR_MODE`.
    - **Точную сигнатуру `infer`, версии torch, transformers и flash-attn сверить с model card `deepseek-ai/DeepSeek-OCR`.**
    - Если flash-attn не собирается — `attn_implementation="eager"`.
    - Инференс вызывается через `asyncio.to_thread(...)`. Иначе блокируется event loop, пропадают heartbeat'ы AMQP, и брокер рвёт соединение.
4. **Пайплайн `process_job`:**
    - `mark_running`, событие `ocr.started` (`pages_total` из PDF);
    - по каждой странице:
        - если страница уже распознана (повторная доставка после падения), пропустить — воркер продолжает с места остановки;
        - иначе рендер (PyMuPDF, `OCR_DPI`) → `recognize` → `PageRepository.upsert` → `inc_progress` → событие `ocr.progress`;
    - `mark_done`, статус выпуска `ocr_done`, событие `ocr.completed`;
    - `RagClient.index_issue(issue_id)` (gRPC) → событие `issue.indexed` (audience `all`);
    - при `OCR_UNLOAD_AFTER_JOB` — `engine.unload()` + `free_cuda()`;
    - `ack`.
5. **Ошибки:**
    - исключение на странице → `mark_failed(error)`, статус выпуска `failed`, событие `ocr.failed`;
    - `nack(requeue=False)`: сообщение уходит в DLX, если он настроен, или удаляется — бесконечных повторов нет;
    - если воркер упал без ack, RabbitMQ доставит сообщение заново, а пропуск готовых страниц делает это безопасным.
6. Логи по каждой странице: `[ocr] job=41 page 17/96 → 1 284 chars (6.8 s)`.

### Шаг 5. Notification Service

- `ConnectionManager`:
    - `dict[WebSocket, ClientInfo(user_id, role)]`;
    - `send(envelope)`: `audience == "admin"` → только админам, `"all"` → всем;
    - упавшие соединения удаляются.
- WebSocket `/ws?token=…`:
    - `decode_token` до `accept()`;
    - невалидный или истёкший токен → `close(code=1008)`;
    - после подключения клиенту отправляется `{"event": "connected", "role": ...}`;
    - входящие сообщения клиента читаются ради ping и обнаружения разрыва.
    - Без JWT (если в ЛР2 он не делался): анонимные подключения получают только события `audience == "all"`.
- Потребитель: фоновая задача в lifespan.
    - `declare_topology`, подписка на `notifications`;
    - разбор `EventEnvelope`, при ошибке разбора — лог и ack;
    - `manager.send`, ack.
- Логи: `[notify] ← ocr.progress job=41 17/96 → 1 admin client(s)`.

### Шаг 6. Консольный клиент

- `uv run --package ws-client ws-client --email admin@corag.local --password ...`:
    - логин через REST, получение токена;
    - подключение к `ws://localhost:8001/ws?token=...` (или через Nginx — `ws://localhost/ws`).
- Печатает события строкой со временем. Для `ocr.progress` — полоса прогресса.
- При разрыве переподключается с экспоненциальной задержкой.

### Шаг 7. [ОПЦИОНАЛЬНО] Nginx для WebSocket

Если Nginx делался в ЛР2, раскомментировать `location /ws`:

- `proxy_pass` на Notification Service;
- `proxy_http_version 1.1`;
- `Upgrade $http_upgrade`;
- `Connection "upgrade"`;
- `proxy_read_timeout 3600s`.

### Шаг 8. Тесты

- `ConnectionManager`: маршрутизация по `audience`, удаление мёртвых соединений.
- WebSocket через `TestClient`: без токена → 1008; с токеном админа приходит событие.
- Контракты: сериализация и десериализация всех событий.
- Пайплайн OCR с тестовым двойником движка (**только в тестах**): пропуск распознанных страниц, ошибка → `ocr.failed`.
- **[ОПЦИОНАЛЬНО]** Интеграционный тест с настоящим RabbitMQ (`@pytest.mark.integration`).

## 5. Как проверить и показать

```bash
docker compose -f docker-compose.dev.yml up -d                     # postgres, rabbitmq, ollama
uv run --package corag-rag python -m corag_rag.server              # терминал 1
uv run --package corag-api uvicorn corag_api.main:create_app --factory --port 8000   # терминал 2
uv run --package corag-notify uvicorn corag_notify.app:app --port 8001               # терминал 3
uv run --package corag-ocr python -m corag_ocr.main                # терминал 4 (GPU)
uv run --package ws-client ws-client --email admin@corag.local     # терминал 5 — клиент
bash scripts/demo_lr4.sh data/samples/scan_3pages.pdf              # создать выпуск → загрузить скан → POST /ocr
# RabbitMQ UI: http://localhost:15672 — видны очереди и сообщения
```

Для демо взять небольшой реальный скан — 3–5 страниц старого выпуска. Тогда весь цикл займёт меньше пары минут.

## 6. Критерии готовности

- [ ] API публикует задачу в `ocr.jobs` и событие `ocr.queued` в `events`.
- [ ] OCR Worker распознаёт настоящий скан DeepSeek-OCR, пишет страницы в БД, публикует `started`, `progress`, `completed` и вызывает `IndexIssue`.
- [ ] Notification Service подписан на `notifications`, проверяет JWT, рассылает по `audience`.
- [ ] Клиент получает и отображает события в реальном времени. API, Notification и клиент — отдельные процессы.
- [ ] Повторная доставка (убить воркер посреди задачи и запустить снова) продолжает с места остановки, дублей нет.
- [ ] Ошибка OCR → `ocr.failed`, задача в статусе `failed`, бесконечных повторов нет.
- [ ] После OCR вопрос `/rag/ask` находит текст нового выпуска.
- [ ] `pytest` и `ruff` проходят.
- [ ] **[ОПЦИОНАЛЬНО]** DLX для ядовитых сообщений; `/ws` через Nginx.
- [ ] Подготовка к сдаче составлена по разделу 8: `defense/lr4.md` (сценарий 5–7 мин, теория, вопросы с ответами) и `defense/lr4-cheatsheet.md`; команды проверены запуском.
- [ ] Отчёт `docs/reports/lr4.md` составлен по шаблону: настоящий вывод консоли, схемы, листинги; список TODO (скриншоты) закрыт.
- [ ] Пройден сценарий сдачи из `defense/lr4.md` (репетиция).
- [ ] PR `lr4-rabbitmq-ws` → `main`.

## 7. Подводные камни

- Синхронный инференс в async-воркере рвёт AMQP-соединение. Нужен `asyncio.to_thread` и `connect_robust`.
- `consumer_timeout`: брокер закрывает канал, если сообщение не подтверждено дольше лимита. Лимит поднимается в `rabbitmq.conf`.
- Без `prefetch_count=1` воркер заберёт все задачи сразу, хотя обрабатывает их по одной.
- VRAM: если `/rag/ask` придёт во время OCR, Ollama снова загрузит модель, и возможен OOM. Принятое ограничение: на время демо OCR вопросы не задаём. Ограничение описать в README.
- WebSocket в браузере не умеет задавать заголовки — поэтому токен передаётся в query. В логах Nginx query с токеном лучше маскировать.

## 8. Подготовка к сдаче (составляет Claude Code)

Делается **до отчёта**: раздел 2 «Теория» из `defense/lr4.md` используется в отчёте (раздел 9).
Порядок закрытия лабы:

1. Пройти разделы 5–6.
2. Подготовка к сдаче (этот раздел).
3. Отчёт (раздел 9).
4. Репетиция сдачи.
5. PR.

Общие правила — в CLAUDE.md, раздел «Документация по лабе». Промпт — 10.2.

### 8.1. `defense/lr4.md` — сценарий сдачи, теория, вопросы

#### Раздел 1. Сценарий сдачи (5–7 мин)

**Подготовка:**

- инфраструктура и 4 процесса запущены;
- веса DeepSeek-OCR скачаны, первый прогон уже был;
- модель Ollama выгружена;
- WS-клиент подключён под админом;
- маленький скан на 3 страницы;
- открыта RabbitMQ UI;
- окна разложены: клиент крупно, логи воркера и Notification рядом.

| Время | Говорю | Показываю |
| --- | --- | --- |
| 0:00–0:40 | Зачем брокер: долгий OCR, развязка, масштабирование воркеров | Схема топологии |
| 0:40–1:30 | Топология | RabbitMQ UI: exchanges `ocr` и `events`, очереди, привязки |
| 1:30–4:40 | Сквозной сценарий | Создать выпуск → загрузить скан → `POST /ocr` (202) → в клиенте `ocr.queued`, `started`, `progress` по страницам, `completed`, `issue.indexed`; в логах воркера — страницы. Если время есть: убить воркер, запустить снова — продолжение с места остановки |
| 4:40–5:30 | Код | `prefetch=1`, ручной ack, `to_thread`; `ConnectionManager.send` по `audience`; проверка токена на `/ws` |
| 5:30–7:00 | Резерв на вопросы | — |

#### Раздел 2. Минимальная теория (2–3 стр.)

- Обмен сообщениями: очередь задач (point-to-point) и publish/subscribe; событийно-ориентированная архитектура.
- AMQP 0-9-1: producer, exchange, binding, queue, consumer; типы exchange; подтверждения; гарантии доставки; prefetch; DLX.
- WebSocket:
    - рукопожатие через HTTP Upgrade;
    - фреймы, ping/pong, коды закрытия;
    - сравнение с polling, long polling и SSE;
    - аутентификация.

#### Раздел 3. Вопросы преподавателя с ответами (12–15)

На каждую тему — формулировка вопроса и ответ в 2–4 предложения. Где уместно, ответ ссылается на файл и функцию.

- синхронное против асинхронного взаимодействия;
- зачем брокер;
- модель AMQP;
- типы exchange (direct, topic, fanout);
- durable и persistent;
- ack, nack, requeue;
- prefetch;
- at-least-once и идемпотентность;
- DLX;
- heartbeat и `consumer_timeout`;
- RabbitMQ против Kafka;
- рукопожатие WebSocket (101);
- WebSocket против polling и SSE;
- токен в query и код закрытия 1008;
- как масштабировать Notification Service на несколько экземпляров;
- почему OCR — отдельный воркер (GPU, кластер).

### 8.2. `defense/lr4-cheatsheet.md` — шпаргалка (1 стр.)

- **Тезисы:** задача → `ocr.jobs` (direct), события → `events` (topic); `prefetch=1` + ручной ack; продолжение через пропуск готовых страниц; `audience`; 1008.
- **Команды:** запуск 4 процессов и клиента, `demo_lr4.sh`, адрес RabbitMQ UI, `rabbitmqctl list_queues`.
- **Где в коде:** `declare_topology`, `AmqpPublisher`, `process_job`, `DeepSeekOcrEngine`, `unload_ollama_model`, `ConnectionManager`, `/ws`, `ws_client`.
- **Частые ошибки:** обрыв соединения при синхронном инференсе; `consumer_timeout`; OOM при параллельном `/rag/ask`; забытый `prefetch`.

## 9. Отчёт по лабе

Отчёт пишется **после** того, как пройдены разделы 5 и 6 и составлена подготовка к сдаче (раздел 8). Шаблон и правила — `docs/reports/_template.md`.
Коротко: вывод консоли только настоящий, скриншоты — заглушки со списком TODO, теория — по `defense/lr4.md`, не больше страницы.

| Раздел отчёта | Содержание для ЛР4 |
| --- | --- |
| 1. Цель и задание | Пункты методички; DLX и `/ws` через Nginx — «(доп.)» |
| 2. Теория | Брокер, AMQP (exchange, queue, binding, routing key), типы exchange, ack и prefetch, at-least-once и идемпотентность; WebSocket (handshake 101, full-duplex), сравнение с polling и SSE |
| 3.1 Реализовано | Таблица событий (event → кто публикует → audience → когда); таблица процессов (API, OCR Worker, Notification, клиент) |
| 3.2 Схемы | Топология RabbitMQ (flowchart: exchanges → queues → consumers); sequence OCR-задачи от `POST /ocr` до `issue.indexed` на клиенте |
| 3.3 Решения | Задача OCR через очередь вместо gRPC; `ocr.queued` из API; продолжение после падения; `to_thread` и `consumer_timeout`; политика VRAM; JWT в query для WS |
| 4. Демонстрация | Логи API, OCR Worker, Notification и вывод WS-клиента на одном скане; эксперимент «убить воркер посреди задачи → перезапуск → продолжение»; `/rag/ask` по новому выпуску |
| 5. Тестирование | Вывод `pytest -q tests/notify tests/ocr tests/contracts`, `ruff check` |
| Приложение А | `topology.py`, `events.py`, `publishers.py` (AmqpPublisher), `pipeline.py`, `engine.py`, `manager.py`, `ws_client/main.py` |
| Приложение Б | `deploy/rabbitmq/rabbitmq.conf`, сервис RabbitMQ в compose; [опц.] `location /ws` из `nginx.conf` |
| Скриншоты (TODO) | RabbitMQ Management (очереди, сообщения); 4–5 терминалов во время OCR; `nvidia-smi` во время распознавания |

## 10. Промпты для Claude Code

### 10.1. Планирование (режим планирования)

```
Прочитай CLAUDE.md и plans/lr4.md, изучи код API (OcrService, publishers), RAG (RagClient) и corag-db. Ветка lr4-rabbitmq-ws.
Составь план: топология RabbitMQ и контракты, AmqpPublisher в API, OCR Worker с настоящим DeepSeek-OCR
(политика GPU из CLAUDE.md, to_thread, prefetch=1, ручной ack, продолжение после падения), Notification Service с WebSocket и JWT,
консольный клиент, тесты, демо. Опционально: DLX — ВКЛЮЧИТЬ / НЕТ; /ws через Nginx — ВКЛЮЧИТЬ / НЕТ.
Перед планом изучи model card deepseek-ai/DeepSeek-OCR (версии torch/transformers/flash-attn, сигнатура infer, режимы
разрешения, требования к VRAM) и предложи режим, который поместится в 8 ГБ. Код не пиши до одобрения плана.
```

### 10.2. Подготовка к сдаче (после реализации, обычный режим)

```
Лаба 4 реализована и проверена (разделы 5–6 plans/lr4.md пройдены). Составь подготовку к сдаче по разделу 8 plans/lr4.md
и правилам CLAUDE.md («Документация по лабе»): defense/lr4.md (раздел 1 — сценарий сдачи на 5–7 мин с подготовкой и таймингом,
раздел 2 — минимальная теория на 2–3 стр., раздел 3 — вопросы преподавателя с ответами) и defense/lr4-cheatsheet.md
(1 стр.: тезисы, команды, таблица «Где в коде», частые ошибки).
Все команды из сценария и шпаргалки выполни сам; примеры вывода — только настоящие. Ссылки на код — реальные пути и функции.
Закоммить: docs(lr4): подготовка к сдаче.
```

### 10.3. Отчёт (после реализации, обычный режим)

```
Лаба 4 реализована и проверена. Составь отчёт docs/reports/lr4.md по шаблону docs/reports/_template.md
и разделу 9 plans/lr4.md. Теорию возьми из defense/lr4.md (раздел 2) и перескажи своими словами,
не больше страницы. Все команды из раздела «Демонстрация» запусти сам и вставь настоящий вывод (длинный сокращай с «…»);
что запустить нельзя — пометь TODO. Схемы — mermaid по фактическому коду. В конце — список TODO для меня (скриншоты и т. п.).
Закоммить: docs(lr4): отчёт.
```
