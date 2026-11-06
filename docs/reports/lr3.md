# Отчёт по лабораторной работе №3 «Сервисное взаимодействие через gRPC»

**Дисциплина:** Технологии сетевого программирования
**Выполнил:** Кравченко Иван Антонович, гр. 6402-010302D
**Проверил:** Кашапов Артем Ильясович
**Дата:** 06.11.2026
**Репозиторий:** <https://github.com/AStrateg2509/rag_optics>, ветка `lr3-grpc-rag`, PR #<номер>

## 1. Цель и задание

**Цель:** вынести дополнительную бизнес-логику CO-RAG в отдельный gRPC-сервис. Это RAG Service, который отвечает на вопросы по архиву журнала «Компьютерная оптика» и индексирует выпуски. API Service вызывает его во время обработки REST-запросов.

**Задание:**

1. Создать отдельный gRPC-сервис с собственным gRPC-интерфейсом — выполнено (`services/rag`, сервис `corag.rag.v1.RagService`).
2. Описать интерфейс в `.proto`-файле — выполнено (`proto/rag/v1/rag.proto`, код генерируется `scripts/gen_proto.sh`).
3. Принимать запросы от API Service — выполнено (`RagClient` в `corag-contracts`, вызовы из `POST /rag/ask`, `POST /rag/search`, `POST /issues/{id}/reindex`).
4. Возвращать результат вычислений через gRPC — выполнено (ответ LLM с источниками, найденные фрагменты, итог индексации).
5. API Service и gRPC-сервис работают в отдельных процессах, а в консоли видно, как обрабатывается запрос — выполнено (логи `[api] …` и `[rag] …`, раздел 4).

Дополнительных заданий в ЛР3 нет.

## 2. Краткие теоретические сведения

**RPC** (Remote Procedure Call) — вызов процедуры другого процесса, оформленный как вызов локальной функции: `stub.Ask(request)`. В REST контракт — ресурсы и методы HTTP, данные обычно в JSON, клиентом может быть любой, включая браузер. В RPC контракт — процедуры с типизированными сообщениями. В CO-RAG REST смотрит наружу, а gRPC связывает API и RAG Service.

**gRPC** работает только поверх **HTTP/2**. Данные идут двоичными фреймами, каждый вызов — отдельный поток (stream), и потоки мультиплексируются в одном TCP-соединении. Поэтому клиент держит один канал на процесс. Запрос — это HEADERS (`:path /corag.rag.v1.RagService/Ask`, `grpc-timeout`) и DATA с сообщением Protobuf, статус приходит в trailers (`grpc-status`). Контракт описывается в `.proto`, `protoc` генерирует классы сообщений, клиентский stub и базовый класс сервера.

Типы вызовов: unary, server streaming, client streaming и bidirectional. `Ask`, `Search` и `IndexIssue` — unary: один запрос, один ответ. `AskStream` — server streaming: сервер шлёт поток сообщений (сначала источники, затем текст LLM по частям), конец потока — trailers со статусом.

**Deadline** — срок ответа, который клиент передаёт серверу. По истечении вызов отменяется со статусом `DEADLINE_EXCEEDED`. Ошибки передаются **статусами** (`INVALID_ARGUMENT`, `NOT_FOUND`, `UNAVAILABLE`…), и API переводит их в коды HTTP.

**Protocol Buffers** кодирует поле парой «ключ — значение», где ключ = номер поля и wire type: `IndexIssueRequest(issue_id=12)` → `08 0c`, 2 байта. Имён полей в данных нет. Поэтому номера полей нельзя менять и переиспользовать, а новые поля добавляются с новыми номерами. Старый клиент пропустит их, зная длину по wire type. Так в CO-RAG расширен `Citation`.

**Наивный RAG** работает так: вопрос → эмбеддинг → top-k ближайших фрагментов архива → промпт «инструкция + пронумерованные фрагменты + вопрос» → ответ LLM со ссылками [n]. **Эмбеддинг** jina-embeddings-v3 — вектор из 1024 чисел, близость текстов меряется косинусным сходством (pgvector `<=>` — расстояние `1 − cos`). Поиск приближённый, по индексу HNSW. Если ни один фрагмент не прошёл порог сходства, ответ «не найдено» выдаётся без вызова LLM, а это главный способ не дать модели выдумывать. Подробнее — `defense/lr3.md`, раздел 2.

## 3. Ход выполнения

### 3.1. Что реализовано

Таблица 1 — Пункты задания и их реализация

| Пункт задания | Как реализовано в CO-RAG | Где в коде |
| --- | --- | --- |
| Собственный gRPC-интерфейс | `corag.rag.v1.RagService`: unary `Ask`, `Search`, `IndexIssue` и server streaming `AskStream`; асинхронный сервер `grpc.aio` + health checking + reflection | `services/rag/src/corag_rag/server.py` → `serve`, `servicer.py` → `RagServicer` |
| Интерфейс в `.proto` | Черновик из приложения А ТЗ + поля `Citation.issue_id = 9`, `authors = 10`; генерация `grpc_tools.protoc` с исправлением импорта | `proto/rag/v1/rag.proto`, `scripts/gen_proto.sh`, `packages/corag-contracts/src/corag_contracts/rag/v1/` |
| Приём запросов от API Service | `RagClient`: один канал `grpc.aio` на процесс (создаётся в `lifespan`), deadline, protobuf → Pydantic, статусы → исключения | `packages/corag-contracts/src/corag_contracts/rag_client.py` → `RagClient`; `services/api/src/corag_api/main.py` → `lifespan` |
| Результат вычислений через gRPC | Эмбеддинг Jina → pgvector → порог → LLM (Ollama) → ответ с `citations`; индексация страниц выпуска | `retriever.py` → `Retriever.search`, `llm.py` → `OpenAICompatibleClient`, `indexer.py` → `Indexer.index_issue` |
| REST-эндпоинты | `POST /rag/ask`, `POST /rag/search` (researcher+), `POST /issues/{id}/reindex` (admin, временный до ЛР4) | `services/api/src/corag_api/routers/rag.py`, `routers/issues.py` → `reindex_issue` |
| Перевод ошибок gRPC → HTTP | `INVALID_ARGUMENT` → 422, `NOT_FOUND` → 404, `FAILED_PRECONDITION` → 409, `UNAVAILABLE` → 503, `DEADLINE_EXCEEDED` → 504, прочие → 502 | `services/api/src/corag_api/services/rag_service.py` → `_translate` |
| Отдельные процессы, логи пути запроса | `python -m corag_rag.server` и `uvicorn corag_api.main:create_app`; строки `[api] → gRPC …` и `[rag] ← Ask …` | раздел 4 |
| LLM локально на GPU | Сервис `ollama/ollama:0.40.8` с GPU, `OLLAMA_CONTEXT_LENGTH=8192`; модель выбрана сравнением | `docker-compose.dev.yml`, `scripts/compare_llm.py` |

Таблица 2 — Методы gRPC

| Метод | Назначение | Где вызывается |
| --- | --- | --- |
| `Ask(AskRequest) → AskResponse` | Ответ LLM по найденным фрагментам с источниками; `found=false` без вызова LLM, если ничего не прошло порог | API: `POST /rag/ask` (`RagService.ask`) |
| `AskStream(AskRequest) → stream AskStreamResponse` | То же, что `Ask`, но потоком: первым сообщением `sources` (источники или `found=false`), затем `delta` — части ответа LLM по мере генерации | `RagClient.ask_stream`; `scripts/ask_stream.py`, grpcurl, Postman (REST-эндпоинта нет) |
| `Search(SearchRequest) → SearchResponse` | Только поиск: top-k фрагментов с источниками, без генерации | API: `POST /rag/search` (`RagService.search`) |
| `IndexIssue(IndexIssueRequest) → IndexIssueResponse` | Чанкинг страниц выпуска, эмбеддинги, замена чанков, статус `indexed` (идемпотентно) | API: `POST /issues/{id}/reindex` (до ЛР4); OCR Worker после распознавания (ЛР4, `RagClient.index_issue`) |

### 3.2. Схемы

```mermaid
flowchart LR
    C["Клиент<br/>curl / Postman / demo_lr3.sh"] -->|"HTTP + JSON, JWT"| A
    C -->|"HTTP :80 /api/"| N["Nginx"] --> A
    A["API Service :8000<br/>FastAPI: routers/rag.py → RagService → RagClient"]
    A -->|"gRPC: HTTP/2 + Protobuf<br/>Ask · Search · IndexIssue"| R["RAG Service :50051<br/>grpc.aio: RagServicer<br/>+ health, reflection"]
    R --- J["jina-embeddings-v3<br/>CPU, в процессе RAG"]
    A -->|"SQL (asyncpg)"| P[("PostgreSQL 16 + pgvector<br/>chunks VECTOR(1024), HNSW")]
    R -->|"SQL: nearest, replace_for_pages"| P
    R -->|"HTTP: POST /v1/chat/completions"| O["Ollama :11434, GPU<br/>qwen2.5:7b-instruct"]
    W["OCR Worker (ЛР4)"] -.->|"gRPC IndexIssue"| R
```

Рисунок 1 — Компоненты: API ↔ RAG Service ↔ PostgreSQL ↔ Ollama

```mermaid
sequenceDiagram
    autonumber
    participant C as Клиент
    participant A as API: routers/rag.py, RagService
    participant R as RAG: RagServicer.Ask
    participant E as JinaEmbedder
    participant P as PostgreSQL + pgvector
    participant L as Ollama
    C->>A: POST /rag/ask {question, top_k?, year_from?, year_to?} + JWT
    A->>A: require_researcher, AskIn (ошибка → 422)
    A->>R: RagClient.ask → Ask(AskRequest), deadline 120 с
    R->>R: _validate (ошибка → INVALID_ARGUMENT)
    R->>E: embed_queries, task=retrieval.query (asyncio.to_thread)
    E-->>R: вектор 1024
    R->>P: ChunkRepository.nearest (<=>, LEFT JOIN articles), author_names
    P-->>R: фрагменты со score
    alt ни один score ≥ MIN_SCORE
        R-->>A: AskResponse(found=false, NOT_FOUND_ANSWER)
    else есть фрагменты
        R->>L: build_messages ([1]..[k] + вопрос), timeout ≤ остаток deadline
        L-->>R: ответ со ссылками [n]
        R-->>A: AskResponse(answer, citations, found=true)
    end
    A-->>C: 200 AskOut
    Note over A,R: UNAVAILABLE → 503, DEADLINE_EXCEEDED → 504, INVALID_ARGUMENT → 422
```

Рисунок 2 — Последовательность `Ask` (по фактическому коду)

```mermaid
sequenceDiagram
    participant A as API / OCR Worker
    participant I as Indexer.index_issue
    participant P as PostgreSQL
    participant E as JinaEmbedder
    A->>I: IndexIssue(issue_id), deadline 30 мин
    I->>P: транзакция 1: выпуск (нет → NOT_FOUND), страницы
    Note over I: нет текста страниц → FAILED_PRECONDITION
    I->>I: MarkdownChunker.split по каждой странице (to_thread)
    loop пачки по EMBED_BATCH=32, вне транзакции
        I->>E: embed_passages, task=retrieval.passage
    end
    I->>P: транзакция 2: replace_for_pages (DELETE + INSERT) и статус indexed
    I-->>A: pages_indexed, chunks_indexed
```

Рисунок 3 — `IndexIssue`: эмбеддинги вне транзакции, замена чанков в одной транзакции

### 3.3. Ключевые решения

- **RAG — отдельный процесс.** Процесс с Jina занимает 3,2 ГБ ОЗУ (RSS) и грузит модель 5 с; LLM живёт на GPU в Ollama. API остаётся лёгким, его воркеры Gunicorn не грузят модель каждый. RAG перезапускается и масштабируется независимо, а `IndexIssue` вызывает ещё и OCR Worker (ЛР4). Поэтому `RagClient` лежит в общем пакете `corag-contracts`, а не в API.
- **Эмбеддер на CPU** (политика GPU из CLAUDE.md). На карте 8 ГБ: qwen2.5:7b-instruct с контекстом 8192 по `ollama ps` занимает 5,1 GB, а в ЛР4 добавится DeepSeek-OCR. Jina на CPU в float32 (в `config.json` модели — bfloat16) тратит на эмбеддинг вопроса ~1,4 с. `EMBED_DEVICE=cuda` (float16) включается переменной. `model.encode` вызывается через `asyncio.to_thread` под `asyncio.Lock` на пачку, поэтому event loop не блокируется, а `Ask` вклинивается между пачками долгой индексации.
- **Порог `MIN_SCORE=0.35` и `found=false`.** Порог выбран замером на сиде (лучший score ответов `/rag/search`):
  - 8 вопросов по архиву — 0,44–0,81;
  - 5 вопросов вне архива — 0,03–0,15;
  - близкие по теме, но которых нет в архиве («квантовый компьютер», «голография») — 0,29 и 0,22.

  Порог лежит в зазоре 0,29–0,44. Если ни один фрагмент не прошёл порог, `RagServicer.Ask` возвращает стандартный ответ без вызова LLM.
- **Deadline.** Ask — 120 с, Search — 15 с, IndexIssue — 30 мин (`RagClient`, переопределяются `RAG_*_TIMEOUT_S`). `RagServicer._llm_timeout` берёт `min(LLM_TIMEOUT_S, context.time_remaining() − 0,5)`, чтобы не ждать LLM дольше, чем ждёт клиент.
- **Перевод статусов.** В RAG доменные исключения переводятся в статусы одним декоратором `rpc`. В клиенте `map_rpc_error` превращает их в `RagError` и подклассы, в API `RagService._translate` — в исключения API, а exception handlers отдают JSON `{"detail": …}`.

  Таблица 3 — Перевод статусов gRPC в HTTP

  | gRPC | HTTP | Пример в CO-RAG |
  | --- | --- | --- |
  | `INVALID_ARGUMENT` | 422 | пустой вопрос, `top_k` вне 1..20 |
  | `NOT_FOUND` | 404 | reindex несуществующего выпуска |
  | `FAILED_PRECONDITION` | 409 | у страниц выпуска нет текста (нужен OCR) |
  | `UNAVAILABLE` | 503 | RAG Service остановлен; LLM-сервер недоступен или модель не скачана |
  | `DEADLINE_EXCEEDED` | 504 | нет ответа до deadline; LLM не уложилась в таймаут |
  | прочие (`INTERNAL`) | 502 | непредвиденная ошибка в RAG Service |

  Строки 409 и 502 добавлены к таблице плана: без них reindex выпуска без текста и внутренняя ошибка RAG превращались бы в 500.
- **Один канал и короткий backoff.** Канал создаётся один раз в `lifespan` API. Опции `grpc.max_reconnect_backoff_ms=5000` и `initial/min=500` ограничивают паузу между попытками переподключения: по умолчанию она растёт до 120 с. Замер: после перезапуска RAG первый успешный `/rag/search` пришёл через 5,7 с после строки «слушает».
- **Выбор LLM.** Сравнение `scripts/compare_llm.py`: одинаковые фрагменты и промпт для каждой модели, 4 вопроса. Последний вопрос — «Какой лазер использовался…» — специально такой, чтобы фрагменты нашлись, а ответа в них не было.

  Таблица 4 — Сравнение LLM-кандидатов (RTX 3060 Ti 8 ГБ, Ollama 0.40.8, контекст 8192)

  | Модель | Прогрев, с | Среднее время ответа, с | Прирост VRAM | Вопрос без ответа во фрагментах | Замечания |
  | --- | --- | --- | --- | --- | --- |
  | qwen2.5:7b-instruct | 6,3* | 0,9 | 5151 МиБ | «нет ответа» — верно | один раз додумала способ формирования пучков |
  | gemma3:4b | 34,3 | 0,7 | 4020 МиБ | выдумала ответ (модулятор света назван лазером) | дублирует ссылки `[1] … [1]` |
  | qwen3:8b | 53,0 | 3,9 | 5295 МиБ | «нет ответа» — верно | точнее всех держится фрагментов; занято 7789 из 8192 МиБ |

  \* qwen2.5 до этого уже загружалась, файл модели был в кэше ОС; первая загрузка с диска в демо заняла 61 с.

  Выбрана **qwen2.5:7b-instruct**: в 4 раза быстрее qwen3:8b при верном отказе и оставляет запас видеопамяти. qwen3:8b — запасной вариант, если важнее точность. Переключается одной переменной `LLM_MODEL` без изменения кода.
- **Контекст Ollama.** Через OpenAI-совместимый `/v1` нельзя передать `num_ctx`. Ollama 0.40.8 по умолчанию берёт контекст от объёма VRAM (в логе `default_num_ctx=4096`), и промпт из 5 фрагментов по 512 токенов плюс инструкция мог бы молча обрезаться. Поэтому в `docker-compose.dev.yml` задано `OLLAMA_CONTEXT_LENGTH=8192`.
- **Транзакции индексации.** Страницы читаются в короткой транзакции. Чанкинг и эмбеддинги (на CPU — минуты для настоящего выпуска) идут вне транзакции, затем в одной транзакции `ChunkRepository.replace_for_pages` и статус `indexed`. Повторный вызов даёт тот же результат (демо: 6 → 6 чанков, тест `test_index_issue_is_idempotent`). Параллельная индексация одного выпуска исключена блокировкой на выпуск.
- **Расширение контракта** (согласовано). СЦ-1 ТЗ требует авторов в источниках, а для перехода к странице (`GET /issues/{id}/pages/{n}`) нужен `issue_id`. Обоих полей нет в черновике `Citation`, поэтому добавлены `int64 issue_id = 9` и `repeated string authors = 10`. Это обратно совместимое изменение: номера новые. Авторы берутся одним запросом `ArticleRepository.author_names`, без N+1. Дубли чанков на странице, общей для двух статей (LEFT JOIN), убираются в `Retriever`.
- **Server streaming `AskStream`** (расширение ТЗ, согласовано). Ответ LLM приходит частями: Ollama отдаёт `/v1/chat/completions` со `stream: true` как Server-Sent Events, `OpenAICompatibleClient.chat_stream` разбирает строки `data: …` и отдаёт части. `RagServicer.AskStream` — async-генератор: первым `yield` уходят источники (сразу после поиска), затем `delta`. Ошибки переводит декоратор `rpc_stream`. Если ошибка случилась посреди потока, клиент уже получил источники, а статус ошибки придёт в trailers. Пользователь видит источники через 1,8 с и первые слова через 2,2 с, а не весь ответ через ~3 с. Контракт расширен обратно совместимо (новый rpc и новые сообщения). REST-эндпоинт для потока не делался: поток проверяется через `RagClient.ask_stream` (`scripts/ask_stream.py`), grpcurl и Postman.
- **Версии библиотек.** `torch==2.6.0` и `transformers==4.46.3` совпадают с требованиями DeepSeek-OCR: в ЛР4 общий `uv.lock` workspace разрешится без конфликта. Jina v3 на этих версиях проверена. `grpcio` 1.85.1, `protobuf` 7.36.
- **Отличия от плана.** Клиент LLM на `httpx`, а не на SDK `openai`: явный `POST /v1/chat/completions` и прямой перевод `ConnectError`/`TimeoutException` в `UNAVAILABLE`/`DEADLINE_EXCEEDED`. Добавлен скрипт `scripts/compare_llm.py`, чтобы сравнение моделей было воспроизводимым. При старте RAG проверяет, что модель скачана (`OpenAICompatibleClient.probe`), и пишет предупреждение в лог.

## 4. Демонстрация работы

Инфраструктура:

```text
$ docker compose -f docker-compose.dev.yml ps
NAME                    IMAGE                    COMMAND                  SERVICE    CREATED          STATUS                    PORTS
rag_optics-nginx-1      nginx:stable-alpine      "/docker-entrypoint.…"   nginx      2 weeks ago      Up 10 minutes             0.0.0.0:80->80/tcp, [::]:80->80/tcp
rag_optics-ollama-1     ollama/ollama:0.40.8     "/bin/ollama serve"      ollama     23 minutes ago   Up 23 minutes (healthy)   0.0.0.0:11434->11434/tcp, [::]:11434->11434/tcp
rag_optics-postgres-1   pgvector/pgvector:pg16   "docker-entrypoint.s…"   postgres   3 weeks ago      Up 42 minutes (healthy)   0.0.0.0:5432->5432/tcp, [::]:5432->5432/tcp

$ docker compose -f docker-compose.dev.yml logs ollama | grep -E "inference compute|default context"
… msg="inference compute" id=0 filter_id=0 library=CUDA compute=8.6 name=CUDA0 description="NVIDIA GeForce RTX 3060 Ti" … total="8.0 GiB" available="7.0 GiB"
… msg="vram-based default context" total_vram="8.0 GiB" default_num_ctx=4096
```

Генерация кода из контракта (повторная генерация не меняет файлы в git):

```text
$ bash scripts/gen_proto.sh && git status --short
сгенерировано в packages/corag-contracts/src/corag_contracts/rag/v1: rag_pb2.py rag_pb2.pyi rag_pb2_grpc.py
?? .claude/
…
```

Запуск двух процессов: терминал 1 — RAG Service, терминал 2 — API Service.

```text
$ uv run --package corag-rag python -m corag_rag.server
2026-11-05 23:52:14,134 INFO corag_rag [rag] БД доступна
2026-11-05 23:52:15,843 INFO corag_rag [rag] загрузка jinaai/jina-embeddings-v3 на cpu (float32), кэш /home/dockhub/rag_optics/models/hf
2026-11-05 23:52:15,844 INFO corag_rag [rag] attention: стандартный PyTorch (flash-attn не используется)
2026-11-05 23:52:20,820 INFO corag_rag [rag] модель эмбеддингов загружена за 5.0 s
2026-11-05 23:52:23,344 INFO corag_rag [rag] прогрев эмбеддера: 2.5 s
2026-11-05 23:52:23,400 INFO corag_rag [rag] LLM qwen2.5:7b-instruct доступна на http://localhost:11434/v1
2026-11-05 23:52:23,403 INFO corag_rag [rag] gRPC-сервер слушает [::]:50051: corag.rag.v1.RagService (+ health, reflection); MIN_SCORE=0.35, TOP_K=5

$ uv run --package corag-api uvicorn corag_api.main:create_app --factory
INFO:     Started server process [17256]
INFO:     Waiting for application startup.
2026-11-05 23:46:03,104 INFO corag_api [api] старт: БД postgresql+asyncpg://corag:***@localhost:5432/corag, RAG Service localhost:50051 (gRPC)
INFO:     Application startup complete.
INFO:     Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)
```

Reflection и health checking:

```text
$ grpcurl -plaintext localhost:50051 list
corag.rag.v1.RagService
grpc.health.v1.Health
grpc.reflection.v1alpha.ServerReflection

$ grpcurl -plaintext localhost:50051 describe corag.rag.v1.RagService
corag.rag.v1.RagService is a service:
service RagService {
  rpc Ask ( .corag.rag.v1.AskRequest ) returns ( .corag.rag.v1.AskResponse );
  rpc IndexIssue ( .corag.rag.v1.IndexIssueRequest ) returns ( .corag.rag.v1.IndexIssueResponse );
  rpc Search ( .corag.rag.v1.SearchRequest ) returns ( .corag.rag.v1.SearchResponse );
}

$ grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
{
  "status": "SERVING"
}

$ grpcurl -plaintext -d '{"question": " "}' localhost:50051 corag.rag.v1.RagService/Ask
ERROR:
  Code: InvalidArgument
  Message: пустой вопрос
```

Сценарий `scripts/demo_lr3.sh` (терминал 3, сокращено):

```text
$ bash scripts/demo_lr3.sh
== 2. Переиндексация исследователем → 403
→ POST /issues/1/reindex
← 403 (0.0 s)
{
  "detail": "недостаточно прав: нужна роль admin"
}

== 3. Переиндексация выпусков с текстом (gRPC IndexIssue): чанки + эмбеддинги Jina
→ POST /issues/3/reindex
← 200 (2.2 s)
{
  "issue_id": 3,
  "pages_indexed": 6,
  "chunks_indexed": 6
}
…
повтор для выпуска 3: 6 чанков; в карточке: {"status":"indexed","chunks_count":6} — дублей нет

== 4. Ошибки IndexIssue: нет выпуска → 404 (NOT_FOUND), нет текста → 409 (FAILED_PRECONDITION)
→ POST /issues/999999/reindex
← 404 (0.0 s)
{
  "detail": "выпуск 999999 не найден"
}
→ POST /issues/23/reindex
← 409 (0.0 s)
{
  "detail": "у выпуска 23 нет текста страниц (нужен OCR)"
}

== 6. Вопрос по архиву: POST /rag/ask (gRPC Ask → эмбеддинг → pgvector → LLM)
→ POST /rag/ask
← 200 (3.2 s)
found: true
answer: Рассматривается задача расчёта дифракционного оптического элемента (ДОЭ), фокусирующего лазерное излучение в кольцо заданного радиуса. Фаза элемента ищется в приближении Френеля, а интенсивность в фокальной плоскости выражается через преобразование Ханкеля функции пропускания. [1]
  [1] Расчёт дифракционного оптического элемента для фокусировки в кольцо, 1998 № 1, с. 1, score 0.8078; Кузнецов Андрей Петрович, Орлова Мария Сергеевна
  [2] Расчёт дифракционного оптического элемента для фокусировки в кольцо, 1998 № 1, с. 3, score 0.5694; Кузнецов Андрей Петрович, Орлова Мария Сергеевна
  [3] Расчёт дифракционного оптического элемента для фокусировки в кольцо, 1998 № 1, с. 2, score 0.3741; Кузнецов Андрей Петрович, Орлова Мария Сергеевна

== 7. Фильтр по годам: только 2015–2024
→ POST /rag/ask
← 200 (2.0 s)
found: true
answer: Топологический заряд пучка определяется числом оборотов фазы вокруг оптической оси [1].
  [1] Острая фокусировка вихревых лазерных пучков, 2015 № 3, с. 2, score 0.6388; Громова Елена Викторовна, Кузнецов Андрей Петрович, Соколов Илья Андреевич

== 8. Вопрос вне архива → found=false, LLM не вызывается
→ POST /rag/ask
← 200 (1.4 s)
{
  "answer": "В архиве не найдено фрагментов, относящихся к вопросу. Попробуйте переформулировать вопрос или изменить фильтр по годам.",
  "found": false,
  "citations": []
}

== 9. Пустой вопрос → 422 (до gRPC не доходит)
→ POST /rag/ask
← 422 (0.0 s)
…
Демо ЛР3 пройдено: проиндексировано выпусков — 3
```

Путь одного запроса — шаги 6 и 8 демо в логах двух процессов (те же секунды):

```text
# Терминал 2 — API Service
2026-11-06 00:05:42,460 INFO corag_api [api] → gRPC Ask question="Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?" top_k=3
2026-11-06 00:05:45,607 INFO corag_api [api] ← gRPC Ask 3.1 s, found=true, 3 citations
…
2026-11-06 00:05:47,601 INFO corag_api [api] → gRPC Ask question="Как приготовить борщ?" top_k=по умолчанию
2026-11-06 00:05:48,995 INFO corag_api [api] ← gRPC Ask 1.4 s, found=false, 0 citations

# Терминал 1 — RAG Service
2026-11-06 00:05:42,461 INFO corag_rag [rag] ← Ask "Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?" top_k=3
2026-11-06 00:05:43,847 INFO corag_rag [rag] retrieved 3 chunks (best 0.81)
2026-11-06 00:05:43,847 INFO corag_rag [rag] → LLM qwen2.5:7b-instruct: 3 фрагментов в контексте
2026-11-06 00:05:45,601 INFO corag_rag [rag] ← LLM qwen2.5:7b-instruct 1.8 s, токены: промпт 602, ответ 106
2026-11-06 00:05:45,601 INFO corag_rag [rag] → AskResponse 3 citations
…
2026-11-06 00:05:47,602 INFO corag_rag [rag] ← Ask "Как приготовить борщ?" top_k=5
2026-11-06 00:05:48,993 INFO corag_rag [rag] нет фрагментов со сходством ≥ MIN_SCORE=0.35 (best 0.08) → found=false, LLM не вызывается
```

Из 3,1 с ответа ~1,4 с занимает эмбеддинг вопроса на CPU и 1,8 с — LLM. Вызов IndexIssue в логах (шаг 3 демо):

```text
2026-11-06 00:05:33,364 INFO corag_api [api] → gRPC IndexIssue issue_id=3
2026-11-06 00:05:33,365 INFO corag_rag [rag] ← IndexIssue issue=3
2026-11-06 00:05:35,487 INFO corag_rag [rag] IndexIssue issue=3: эмбеддинги 1/1 (6 чанков, 2.1 s)
2026-11-06 00:05:35,542 INFO corag_rag [rag] IndexIssue issue=3: 6 pages → 6 chunks (2.2 s)
2026-11-06 00:05:35,544 INFO corag_api [api] ← gRPC IndexIssue 2.2 s: 6 pages → 6 chunks
…
2026-11-06 00:05:41,037 INFO corag_api [api] → gRPC IndexIssue issue_id=23
2026-11-06 00:05:41,041 INFO corag_rag [rag] IndexIssue → FAILED_PRECONDITION: у выпуска 23 нет текста страниц (нужен OCR)
2026-11-06 00:05:41,042 WARNING corag_api [api] ← gRPC IndexIssue FAILED_PRECONDITION: у выпуска 23 нет текста страниц (нужен OCR)
2026-11-06 00:05:41,043 INFO corag_api [api] POST /issues/23/reindex → 409: у выпуска 23 нет текста страниц (нужен OCR)
```

Ответ JSON с источниками (`curl`):

```text
$ curl -s -X POST localhost:8000/rag/ask -H "Authorization: Bearer $TOKEN" \
    -H 'Content-Type: application/json' -d '{"question": "Что такое топологический заряд?"}' | jq
{
  "answer": "Топологический заряд пучка определяется числом оборотов фазы вокруг оптической оси [1].",
  "found": true,
  "citations": [
    {
      "chunk_id": 421,
      "article_id": 3,
      "article_title": "Острая фокусировка вихревых лазерных пучков",
      "authors": [
        "Громова Елена Викторовна",
        "Кузнецов Андрей Петрович",
        "Соколов Илья Андреевич"
      ],
      "issue_id": 2,
      "year": 2015,
      "issue_number": 3,
      "page_number": 2,
      "snippet": "## Топологический заряд Топологический заряд пучка определяется числом оборотов фазы вокруг оптической оси.",
      "score": 0.6095
    }
  ]
}
```

RAG Service остановлен (Ctrl+C / SIGTERM) → API отвечает 503:

```text
# Терминал 1
2026-11-05 23:51:56,127 INFO corag_rag [rag] остановка: NOT_SERVING, ждём активные вызовы до 5 s
2026-11-05 23:51:56,133 INFO corag_rag [rag] остановлен

$ bash scripts/demo_lr3.sh --expect-down
…
== RAG Service остановлен: gRPC UNAVAILABLE → 503
→ POST /rag/ask
← 503 (0.0 s)
{
  "detail": "RAG Service или LLM недоступны: failed to connect to all addresses; last error: UNKNOWN: ipv4:127.0.0.1:50051: Failed to connect to remote host: Connection refused"
}
→ POST /rag/search
← 503 (0.0 s)
…
RAG Service недоступен → API отвечает 503
```

Сравнение LLM (`scripts/compare_llm.py`, сокращено; итог — таблица 4):

```text
$ uv run --package corag-rag python scripts/compare_llm.py qwen2.5:7b-instruct gemma3:4b qwen3:8b
• Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?
  фрагментов: 3, best 0.81
…
• Какой лазер использовался в эксперименте по острой фокусировке вихревого пучка?
  фрагментов: 4, best 0.44

VRAM без LLM: 2494 MiB

=== qwen2.5:7b-instruct
прогрев (загрузка в VRAM): 6.3 s, VRAM 7645 MiB
…
[0.4 s, ссылки —] Какой лазер использовался в эксперименте по острой фокусировке вихревого пучка?
Во фрагментах архива нет ответа на этот вопрос.

среднее время ответа qwen2.5:7b-instruct: 0.9 s

=== gemma3:4b
прогрев (загрузка в VRAM): 34.3 s, VRAM 6514 MiB
…
[0.6 s, ссылки [4]] Какой лазер использовался в эксперименте по острой фокусировке вихревого пучка?
В эксперименте по острой фокусировке вихревого пучка использовался лазер, формируемый с помощью пространственного модулятора света с разрешением 1920×1080 [4].

среднее время ответа gemma3:4b: 0.7 s

=== qwen3:8b
прогрев (загрузка в VRAM): 53.0 s, VRAM 7789 MiB
…
[3.5 s, ссылки —] Какой лазер использовался в эксперименте по острой фокусировке вихревого пучка?
Во фрагментах архива нет ответа на этот вопрос.

среднее время ответа qwen3:8b: 3.9 s
```

Server streaming `AskStream` (2026-11-06). Через grpcurl видно сообщения потока, через `scripts/ask_stream.py` — когда они приходят:

```text
$ grpcurl -plaintext -d '{"question": "Что такое топологический заряд?", "top_k": 2}' localhost:50051 corag.rag.v1.RagService/AskStream
{
  "sources": {
    "found": true,
    "citations": [
      {
        "chunk_id": "447",
        "article_id": "3",
        "article_title": "Острая фокусировка вихревых лазерных пучков",
        "year": 2015,
        …
        "score": 0.6094949,
        …
      }
    ]
  }
}
{
  "delta": "Т"
}
{
  "delta": "оп"
}
…
{
  "delta": "]."
}

$ uv run --package corag-api python scripts/ask_stream.py "Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?"
[1.8 s] источники: found=true
  [1] «Расчёт дифракционного оптического элемента для фокусировки в кольцо», 1998 № 1, с. 1
  [2] «Расчёт дифракционного оптического элемента для фокусировки в кольцо», 1998 № 1, с. 3
  [3] «Расчёт дифракционного оптического элемента для фокусировки в кольцо», 1998 № 1, с. 2
[2.2 s] ответ:
Рассчитывается дифракционный оптический элемент (ДОЭ) в приближении Френеля для фокусировки лазерного излучения в кольцо заданного радиуса [1].
[3.0 s] поток закрыт (OK)

# Терминал 1
2026-11-06 11:45:14,171 INFO corag_rag [rag] ← AskStream "Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?" top_k=5
2026-11-06 11:45:15,931 INFO corag_rag [rag] retrieved 3 chunks (best 0.81)
2026-11-06 11:45:15,932 INFO corag_rag [rag] → LLM qwen2.5:7b-instruct: 3 фрагментов в контексте
2026-11-06 11:45:15,933 INFO corag_rag [rag] → AskStream: отправлены источники (3)
2026-11-06 11:45:16,408 INFO corag_rag [rag] ← LLM qwen2.5:7b-instruct: первый фрагмент через 0.5 s
2026-11-06 11:45:17,175 INFO corag_rag [rag] ← LLM qwen2.5:7b-instruct поток 1.2 s, 54 частей, токены: промпт 602, ответ 55
2026-11-06 11:45:17,176 INFO corag_rag [rag] → AskStream: поток закрыт, 54 частей ответа
```

Асинхронность сервера: `Ask` с выгруженной из видеопамяти LLM и `Search`, отправленный через 2,5 с. Пока `Ask` ждёт LLM, event loop обслуживает `Search`:

```text
Search завершён через 4.373 s (отправлен на 2.5 s)
Ask    завершён через 6.290 s
2026-11-06 11:45:32,700 INFO corag_rag [rag] ← Ask "Как рассчитать ДОЭ для фокусировки в кольцо?" top_k=5
2026-11-06 11:45:34,225 INFO corag_rag [rag] retrieved 3 chunks (best 0.77)
2026-11-06 11:45:34,225 INFO corag_rag [rag] → LLM qwen2.5:7b-instruct: 3 фрагментов в контексте
2026-11-06 11:45:35,202 INFO corag_rag [rag] ← Search "топологический заряд вихревого пучка" top_k=1
2026-11-06 11:45:37,057 INFO corag_rag [rag] → SearchResponse 1 results (best 0.61, MIN_SCORE=0.35)
2026-11-06 11:45:38,973 INFO corag_rag [rag] ← LLM qwen2.5:7b-instruct 4.7 s, токены: промпт 595, ответ 82
2026-11-06 11:45:38,974 INFO corag_rag [rag] → AskResponse 3 citations
```

![Логи API и RAG Service в двух терминалах рядом](img/lr3/two-terminals.png)

Рисунок 4 — Путь запроса `/rag/ask` в логах двух процессов

![Ответ /rag/ask с источниками в Postman](img/lr3/postman-ask.png)

Рисунок 5 — Ответ `/rag/ask` в Postman

## 5. Тестирование

Покрыто тестами:

- **чанкер** — границы и лимит токенов, перекрытие, формулы `$$…$$` и таблицы не режутся, окна длинного блока, склейка короткого хвоста (`tests/rag/test_chunker.py`, двойник токенизатора);
- **промпт** — нумерация [1]..[k] и подписи источников (`test_prompts.py`);
- **сервисер** — настоящий `grpc.aio`-сервер на свободном порту и настоящий `RagClient`, двойники только у эмбеддера и LLM (`test_servicer.py`):
  - `Ask` с найденными фрагментами и вне архива (LLM не вызывается);
  - фильтр по годам, дедупликация;
  - `IndexIssue` идемпотентен;
  - перевод ошибок в статусы и обратно в `RagError`;
  - `AskStream`: источники первым сообщением, склейка частей равна ответу, вне архива LLM не вызывается, ошибка LLM приходит после источников;
- **поток LLM** — разбор SSE (`data: …`, `[DONE]`), вырезание `<think>…</think>` из разорванных частей, 404/503/обрыв соединения → `LLMUnavailable` (`test_llm.py`, `httpx.MockTransport`);
- **API** — `/rag/ask`, `/rag/search`, reindex: 200, 401, 403, 422 от Pydantic, перевод 422/404/409/503/504/502 (`tests/api/test_rag.py`, двойник `RagClient`);
- **БД** — `ArticleRepository.author_names` (`tests/db/test_articles.py`);
- **slow** — настоящая jina-embeddings-v3: размерность, L2-норма, близость вопроса к своему фрагменту, чанкер с токенизатором модели.

```text
$ uv run pytest -q tests/rag
.....................................                                    [100%]
37 passed, 3 deselected in 0.76s

$ uv run pytest tests/rag/test_servicer.py -v
tests/rag/test_servicer.py::test_index_issue_is_idempotent PASSED
tests/rag/test_servicer.py::test_index_unknown_issue_and_issue_without_text PASSED
tests/rag/test_servicer.py::test_ask_found_calls_llm_with_numbered_sources PASSED
tests/rag/test_servicer.py::test_ask_outside_archive_skips_llm PASSED
tests/rag/test_servicer.py::test_ask_year_filter PASSED
tests/rag/test_servicer.py::test_search_returns_page_outside_article_without_source PASSED
tests/rag/test_servicer.py::test_search_dedupes_page_shared_by_two_articles PASSED
…
tests/rag/test_servicer.py::test_llm_errors_map_to_grpc_status[LLMUnavailable-RagUnavailable] PASSED
tests/rag/test_servicer.py::test_llm_errors_map_to_grpc_status[LLMTimeout-RagDeadlineExceeded] PASSED
tests/rag/test_servicer.py::test_unexpected_error_is_internal PASSED
tests/rag/test_servicer.py::test_ask_stream_sends_sources_first_then_answer_parts PASSED
tests/rag/test_servicer.py::test_ask_stream_outside_archive_skips_llm PASSED
tests/rag/test_servicer.py::test_ask_stream_invalid_argument PASSED
tests/rag/test_servicer.py::test_ask_stream_llm_error_after_sources PASSED
tests/rag/test_servicer.py::test_rag_service_down_is_unavailable PASSED

$ uv run pytest -q
…
145 passed, 3 deselected in 6.67s

$ uv run pytest -q -m slow tests/rag
3 passed, 37 deselected in 12.50s

$ uv run ruff check . && uv run ruff format --check .
All checks passed!
128 files already formatted
```

## 6. Выводы

Изучено сервисное взаимодействие через gRPC: контракт в `.proto` и кодогенерация, unary-вызовы и server streaming поверх HTTP/2, deadline, статусы, health checking и reflection. Также освоено двоичное кодирование Protocol Buffers и правила совместимого расширения схемы. RAG Service работает отдельным процессом с настоящей моделью эмбеддингов Jina и локальной LLM. API вызывает его по gRPC, а логи обоих процессов показывают путь запроса. Вопрос вне архива получает `found=false` без обращения к LLM, ошибки gRPC переводятся в коды HTTP, повторная индексация не плодит дублей.

Ограничения:

- наивный RAG без переранжирования, и 7B-модель изредка додумывает сверх фрагментов;
- эмбеддинг вопроса на CPU занимает ~1,4 с;
- при работе OCR (ЛР4) LLM будет выгружаться из видеопамяти, и ответы станут медленнее.

## Приложение А. Листинги ключевых модулей

Листинг А.1 — `proto/rag/v1/rag.proto`

```protobuf
// Контракт RAG Service (ЛР3). Черновик — приложение А ТЗ (docs/LR0_TZ.md).
// Код Python генерирует scripts/gen_proto.sh в packages/corag-contracts/src/corag_contracts/rag/v1/.
//
// Правила эволюции: номера полей не меняем и не переиспользуем; новые поля только добавляем
// с новыми номерами; удалённые номера помечаем reserved.
syntax = "proto3";

package corag.rag.v1;

// Ask, Search, IndexIssue — unary (один запрос → один ответ); AskStream — server streaming.
service RagService {
  // Ответ на вопрос по архиву с источниками (эмбеддинг → top-k → LLM).
  rpc Ask (AskRequest) returns (AskResponse);
  // То же, но потоком (расширение ТЗ, ЛР3): первым сообщением — источники, затем ответ LLM
  // частями по мере генерации. Конец ответа — закрытие потока со статусом OK (в trailers).
  rpc AskStream (AskRequest) returns (stream AskStreamResponse);
  // Только поиск: top-k фрагментов без генерации.
  rpc Search (SearchRequest) returns (SearchResponse);
  // Чанкинг и эмбеддинги страниц выпуска; повторный вызов заменяет чанки (идемпотентно).
  rpc IndexIssue (IndexIssueRequest) returns (IndexIssueResponse);
}

message Filters {
  optional int32 year_from = 1;
  optional int32 year_to = 2;
}

message AskRequest {
  string question = 1;
  int32 top_k = 2;          // 0 — значение по умолчанию сервера (TOP_K=5)
  Filters filters = 3;
}

message Citation {
  int64 chunk_id = 1;
  optional int64 article_id = 2;   // пусто, если страница ещё не размечена
  string article_title = 3;
  int32 year = 4;
  int32 issue_number = 5;
  int32 page_number = 6;
  string snippet = 7;
  float score = 8;                 // косинусное сходство, 1 — совпадение
  // Расширение черновика ТЗ (ЛР3): ссылка на страницу GET /issues/{issue_id}/pages/{n}
  // и авторы статьи (СЦ-1) в порядке author_order.
  int64 issue_id = 9;
  repeated string authors = 10;
}

message AskResponse {
  string answer = 1;
  repeated Citation citations = 2;  // по порядку ссылок [1]..[k] в ответе
  bool found = 3;          // false — релевантных фрагментов нет, LLM не вызывалась
}

// Первое сообщение потока AskStream: найденные фрагменты (до вызова LLM).
message AskSources {
  bool found = 1;                   // false — дальше придёт только стандартный ответ
  repeated Citation citations = 2;  // по порядку ссылок [1]..[k]
}

// Сообщение потока AskStream: ровно одно из полей oneof.
message AskStreamResponse {
  oneof event {
    AskSources sources = 1;  // первое сообщение
    string delta = 2;        // очередной фрагмент текста ответа
  }
}

message SearchRequest {
  string query = 1;
  int32 top_k = 2;
  Filters filters = 3;
}

message SearchResponse {
  repeated Citation results = 1;
}

message IndexIssueRequest {
  int64 issue_id = 1;
}

message IndexIssueResponse {
  int32 pages_indexed = 1;
  int32 chunks_indexed = 2;
}
```

Листинг А.2 — `services/rag/src/corag_rag/servicer.py` (фрагмент: перевод исключений в статусы gRPC; словарь `STATUS_CODES` — в таблице 3)

```python
async def _abort(context: Context, method: str, exc: Exception) -> NoReturn:
    """Исключение → статус gRPC; context.abort бросает исключение и завершает вызов."""
    code = next((c for cls, c in STATUS_CODES.items() if isinstance(exc, cls)), None)
    if code is None:
        log.error("[rag] %s → INTERNAL", method, exc_info=exc)
        await context.abort(grpc.StatusCode.INTERNAL, "внутренняя ошибка RAG Service")
    else:
        log.info("[rag] %s → %s: %s", method, code.name, exc)
        await context.abort(code, str(exc))
    raise AssertionError("context.abort всегда бросает исключение")  # pragma: no cover


def rpc[Req, Resp](method: Handler[Req, Resp]) -> Handler[Req, Resp]:
    """Unary-метод: перевод исключений в статус gRPC (context.abort) — в одном месте."""

    @functools.wraps(method)
    async def wrapper(self: "RagServicer", request: Req, context: Context) -> Resp:
        try:
            return await method(self, request, context)
        except Exception as exc:
            await _abort(context, method.__name__, exc)

    return wrapper


# rpc_stream — то же для async-генератора: async for … yield внутри try
```

Листинг А.3 — `services/rag/src/corag_rag/servicer.py` (фрагмент: `RagServicer.Ask` и `AskStream`; проверка запроса и поиск — общий `_retrieve`)

```python
    @rpc
    async def Ask(self, request: rag_pb2.AskRequest, context: Context) -> rag_pb2.AskResponse:
        question, result = await self._retrieve("Ask", request)
        if not result.hits:
            return rag_pb2.AskResponse(answer=NOT_FOUND_ANSWER, found=False)

        answer = await self.llm.chat(
            build_messages(question, result.hits), timeout_s=self._llm_timeout(context)
        )
        citations = [self._citation(hit) for hit in result.hits]
        log.info("[rag] → AskResponse %d citations", len(citations))
        return rag_pb2.AskResponse(answer=answer, found=True, citations=citations)

    @rpc_stream
    async def AskStream(
        self, request: rag_pb2.AskRequest, context: Context
    ) -> AsyncIterator[rag_pb2.AskStreamResponse]:
        question, result = await self._retrieve("AskStream", request)
        if not result.hits:
            yield rag_pb2.AskStreamResponse(sources=rag_pb2.AskSources(found=False))
            yield rag_pb2.AskStreamResponse(delta=NOT_FOUND_ANSWER)
            return

        citations = [self._citation(hit) for hit in result.hits]
        yield rag_pb2.AskStreamResponse(sources=rag_pb2.AskSources(found=True, citations=citations))
        …
        async for delta in stream:
            parts += 1
            yield rag_pb2.AskStreamResponse(delta=delta)
```

Листинг А.4 — `packages/corag-contracts/src/corag_contracts/rag_client.py` (фрагмент: deadline и ошибки)

```python
def map_rpc_error(exc: grpc.aio.AioRpcError) -> RagError:
    """Статус gRPC → исключение; прочие коды (INTERNAL, UNKNOWN…) — базовый RagError."""
    code = exc.code()
    return ERRORS.get(code, RagError)(code, exc.details() or code.name)

…
    async def ask(
        self,
        question: str,
        *,
        top_k: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> AskResult:
        request = rag_pb2.AskRequest(
            question=question, top_k=top_k or 0, filters=_filters(year_from, year_to)
        )
        try:
            resp = await self._stub.Ask(request, timeout=self.ask_timeout_s)
        except grpc.aio.AioRpcError as exc:
            raise map_rpc_error(exc) from exc
        return AskResult(
            answer=resp.answer, found=resp.found, citations=[_citation(c) for c in resp.citations]
        )
```

Листинг А.5 — `services/rag/src/corag_rag/retriever.py` (фрагмент: `Retriever.search`)

```python
    async def search(
        self,
        query: str,
        top_k: int,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> SearchResult:
        [query_vec] = await self.embedder.embed_queries([query])
        async with self.sessions() as session:
            # Запас ×2: LEFT JOIN по диапазону страниц дублирует чанк, если страница
            # принадлежит двум статьям (одна кончается, другая начинается)
            rows = await ChunkRepository(session).nearest(
                query_vec, top_k * 2, year_from=year_from, year_to=year_to
            )
            unique = _dedupe(rows)[:top_k]
            kept = [row for row in unique if row.score >= self.min_score]
            authors = await ArticleRepository(session).author_names(
                [row.article_id for row in kept if row.article_id is not None]
            )
        hits = [Hit(row, tuple(authors.get(row.article_id or 0, ()))) for row in kept]
        return SearchResult(hits=hits, best_score=unique[0].score if unique else None)
```

Листинг А.6 — `services/rag/src/corag_rag/prompts.py` (фрагмент)

```python
SYSTEM_PROMPT = (
    "Ты — ассистент по архиву научного журнала «Компьютерная оптика».\n"
    "Отвечай на русском языке только по приведённым фрагментам статей, "
    "не используй другие знания.\n"
    "После каждого утверждения ставь ссылку на фрагмент в квадратных скобках: [1], [2][3].\n"
    "Если во фрагментах нет ответа на вопрос, так и скажи: "
    "«Во фрагментах архива нет ответа на этот вопрос» — и ничего не придумывай.\n"
    "Отвечай кратко, 2–6 предложений."
)


def source_label(hit: Hit) -> str:
    """Подпись фрагмента: «статья «…», авторы, 1998, № 1, с. 2»."""
    chunk = hit.chunk
    where = f"{chunk.year}, № {chunk.number}, с. {chunk.page_number}"
    if chunk.article_title is None:
        return f"вне размеченных статей, {where}"
    authors = f", {', '.join(hit.authors)}" if hit.authors else ""
    return f"статья «{chunk.article_title}»{authors}, {where}"


def build_messages(question: str, hits: Sequence[Hit]) -> list[dict[str, str]]:
    """Сообщения chat/completions: инструкция + фрагменты + вопрос."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Фрагменты:\n\n{build_context(hits)}\n\nВопрос: {question}"},
    ]
```

## Приложение Б. Конфигурационные файлы

Листинг Б.1 — `scripts/gen_proto.sh`

```bash
#!/usr/bin/env bash
# Генерация Python-кода из proto/rag/v1/rag.proto (grpcio-tools из dev-группы uv).
#
#   bash scripts/gen_proto.sh
#
# Результат: packages/corag-contracts/src/corag_contracts/rag/v1/
#   rag_pb2.py       — классы сообщений
#   rag_pb2.pyi      — типы для IDE и mypy
#   rag_pb2_grpc.py  — RagServiceStub (клиент) и RagServiceServicer (сервер)
# Сгенерированный код коммитится; руками его не правим.
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
OUT=$ROOT/packages/corag-contracts/src/corag_contracts/rag/v1

mkdir -p "$OUT"
cd "$ROOT"
uv run --no-sync python -m grpc_tools.protoc \
    -I proto \
    --python_out="$OUT" \
    --pyi_out="$OUT" \
    --grpc_python_out="$OUT" \
    proto/rag/v1/rag.proto

# protoc кладёт файлы по пути proto-файла (rag/v1/…) и импортирует rag_pb2 абсолютно:
#   from rag.v1 import rag_pb2
# В пакете corag_contracts такого модуля нет — переписываем импорт на полный путь.
mv "$OUT"/rag/v1/rag_pb2* "$OUT"/
rm -rf "$OUT/rag"
sed -i 's/^from rag\.v1 import rag_pb2/from corag_contracts.rag.v1 import rag_pb2/' "$OUT/rag_pb2_grpc.py"

for pkg in "$OUT/.." "$OUT"; do
    [ -f "$pkg/__init__.py" ] || echo '"""Сгенерировано scripts/gen_proto.sh из proto/rag/v1/rag.proto."""' > "$pkg/__init__.py"
done

echo "сгенерировано в ${OUT#"$ROOT"/}: $(cd "$OUT" && echo rag_pb2*)"
```

Листинг Б.2 — `.env.example` (фрагмент: RAG Service, LLM, Ollama)

```bash
# Адрес RAG Service (gRPC) и deadline вызовов, секунды: Ask, Search, IndexIssue
RAG_ADDR=localhost:50051
RAG_ASK_TIMEOUT_S=120
RAG_SEARCH_TIMEOUT_S=15
RAG_INDEX_TIMEOUT_S=1800

# --- RAG Service (services/rag, RagSettings) ---
RAG_GRPC_PORT=50051
EMBED_MODEL=jinaai/jina-embeddings-v3
EMBED_DEVICE=cpu
EMBED_BATCH=32
HF_HOME=models/hf
CHUNK_TOKENS=512
CHUNK_OVERLAP=64
CHUNK_MIN_TOKENS=50
TOP_K=5
TOP_K_MAX=20
# Порог косинусного сходства: ниже — фрагмент нерелевантен; нет ни одного → found=false без LLM.
# Замер на сиде (jina-embeddings-v3): вопросы по архиву — лучший score 0.44–0.81,
# вне архива — 0.03–0.15, близкие по теме, но которых нет в архиве — 0.22–0.29
MIN_SCORE=0.35
SNIPPET_CHARS=300

# --- LLM (OpenAI-совместимый API; Ollama из docker-compose.dev.yml) ---
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=qwen2.5:7b-instruct
LLM_API_KEY=ollama
LLM_TEMPERATURE=0.1
LLM_TIMEOUT_S=90

# --- Ollama (docker-compose.dev.yml) ---
OLLAMA_PORT=11434
OLLAMA_KEEP_ALIVE=30m
OLLAMA_CONTEXT_LENGTH=8192
```

Листинг Б.3 — `docker-compose.dev.yml` (фрагмент: сервис `ollama`)

```yaml
  # LLM-сервер для RAG Service (ЛР3): OpenAI-совместимый API на http://localhost:11434/v1
  # Модель: docker compose -f docker-compose.dev.yml exec ollama ollama pull "$LLM_MODEL"
  ollama:
    image: ollama/ollama:0.40.8
    ports:
      - "${OLLAMA_PORT:-11434}:11434"
    environment:
      # Модель остаётся в видеопамяти 30 мин после запроса (по умолчанию 5 мин)
      OLLAMA_KEEP_ALIVE: ${OLLAMA_KEEP_ALIVE:-30m}
      # Контекст в токенах: 5 фрагментов по 512 токенов + инструкция и вопрос.
      # Через /v1 num_ctx не передать, а при меньшем контексте промпт молча обрежется
      OLLAMA_CONTEXT_LENGTH: ${OLLAMA_CONTEXT_LENGTH:-8192}
    volumes:
      - ollama:/root/.ollama
    deploy:
      resources:
        reservations:
          devices:
            - driver: nvidia
              count: all
              capabilities: [gpu]
    healthcheck:
      test: ["CMD", "ollama", "list"]
      interval: 10s
      timeout: 5s
      retries: 10
```

---

**TODO для автора перед сдачей:**

- [ ] вписать номер PR в шапку после создания PR `lr3-grpc-rag → main` (после слияния PR #2);
- [ ] скриншот двух терминалов рядом (RAG слева, API справа) во время `bash scripts/demo_lr3.sh`, шаги 6–8 → `docs/reports/img/lr3/two-terminals.png`;
- [ ] добавить в Postman-коллекцию запросы `POST /rag/ask`, `POST /rag/search`, `POST /issues/{id}/reindex`; скриншот ответа `/rag/ask` с `citations` → `docs/reports/img/lr3/postman-ask.png`;
- [ ] прогнать gRPC-запросы в самом Postman (`127.0.0.1:50051`, server reflection): `AskStream` и пара «холодный `Ask` + `Search`» по `defense/lr3-qa.md`, вопрос 11;
- [ ] пройти репетицию по `defense/lr3.md`, раздел 1: прогреть LLM заранее, первый `/rag/ask` после старта Ollama идёт до ~60 с.
