# ЛР3. Сервисное взаимодействие через gRPC (RAG Service) — подготовка к сдаче

Ветка `lr3-grpc-rag`. RAG Service (`services/rag`) — отдельный процесс с gRPC-интерфейсом `corag.rag.v1.RagService`: `Ask`, `Search`, `IndexIssue`.
Внутри: эмбеддинги jina-embeddings-v3 на CPU, поиск в pgvector, ответ локальной LLM в Ollama на GPU.
API Service вызывает его через `RagClient` (`corag-contracts`) из `POST /rag/ask`, `POST /rag/search` и служебного `POST /issues/{id}/reindex`.
Тесты: 137 (134 по умолчанию и 3 slow с настоящей Jina); демо — `scripts/demo_lr3.sh`.

## 1. Сценарий сдачи (5–7 мин)

### Подготовка до сдачи

1. Запустить Docker Desktop и поднять инфраструктуру. Модель LLM должна быть в списке `ollama list`: скачивается один раз, ~4,7 ГБ.

   ```bash
   docker compose -f docker-compose.dev.yml up -d            # postgres + ollama (GPU) + nginx
   docker compose -f docker-compose.dev.yml exec ollama ollama list
   uv sync
   (cd packages/corag-db && uv run alembic upgrade head)
   uv run python -m corag_db.seed
   ```

2. **Терминал 1** (слева, крупный шрифт) — RAG Service. Ждать строку `gRPC-сервер слушает [::]:50051`: Jina из кэша грузится ~5 с, прогрев ~2,5 с, первый запуск скачивает ~1,1 ГБ в `models/hf`.

   ```bash
   uv run --package corag-rag python -m corag_rag.server
   ```

   Должна быть строка `LLM qwen2.5:7b-instruct доступна`. Если вместо неё `модели … нет на LLM-сервере`, выполнить `ollama pull` (п. 1).
3. **Терминал 2** (справа) — API Service:

   ```bash
   uv run --package corag-api uvicorn corag_api.main:create_app --factory
   ```

4. **Терминал 3** — один раз прогнать `bash scripts/demo_lr3.sh`. Скрипт переиндексирует выпуски сида настоящими эмбеддингами и **прогревает LLM**: первый `/rag/ask` после старта Ollama занимает до ~60 с (модель грузится с диска в видеопамять; повторная загрузка из кэша ОС — ~10 с), дальше — 2–3 с, из них ~1,4 с — эмбеддинг вопроса на CPU. Модель держится в VRAM 30 мин (`OLLAMA_KEEP_ALIVE`), поэтому прогрев — не раньше чем за 30 мин до сдачи.
5. **VS Code**, вкладки:
   - `proto/rag/v1/rag.proto`;
   - `scripts/gen_proto.sh`;
   - `services/rag/src/corag_rag/servicer.py` (`RagServicer.Ask`, декоратор `rpc`);
   - `packages/corag-contracts/src/corag_contracts/rag_client.py` (`RagClient`, `map_rpc_error`);
   - `services/api/src/corag_api/services/rag_service.py` (`_translate`).

   Схемы — раздел 3.2 отчёта `docs/reports/lr3.md` (превью Markdown).
6. Вопросы наготове:
   - по архиву: «Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?»;
   - вне архива: «Как приготовить борщ?».

### Тайминг

| Время | Говорю | Показываю (окно / команда) |
| --- | --- | --- |
| 0:00–0:40 | RAG вынесен в отдельный процесс. Он держит тяжёлые модели: Jina грузится 5 с, LLM в Ollama — на GPU. API остаётся лёгким и быстро стартует. RAG масштабируется отдельно, а `IndexIssue` вызывает ещё и OCR Worker (ЛР4). Внутри системы — gRPC (бинарный Protobuf, HTTP/2, deadline), наружу — REST | Рис. 1 отчёта (компоненты) |
| 0:40–1:40 | Контракт — `rag.proto`: 3 unary-метода, номера полей. Поля 9 и 10 в `Citation` — обратно совместимое расширение черновика ТЗ. `gen_proto.sh` → `rag_pb2.py` (сообщения), `rag_pb2_grpc.py` (stub и servicer), `.pyi`; `sed` чинит абсолютный импорт. Генерация воспроизводима | VS Code: `rag.proto`, `gen_proto.sh`; терминал 3: `bash scripts/gen_proto.sh && git status --short` (пусто) |
| 1:40–4:20 | Путь запроса. `/rag/ask`: справа `[api] → gRPC Ask`, слева `[rag] ← Ask` → `retrieved 3 chunks (best 0.81)` → `→ LLM` → `← LLM … токены` → `AskResponse 3 citations`, справа `← gRPC Ask`. В JSON — ответ с [1] и источники: статья, авторы, выпуск, страница, score. Вопрос вне архива: best 0.08 < `MIN_SCORE` 0.35 → `found=false`, LLM не вызывается. Ctrl+C в терминале 1: NOT_SERVING → остановлен; `--expect-down` → **503**. Перезапуск: сервер готов через ~9 с, канал API переподключается ещё за ≤ 6 с — снова 200 | Терминал 3: `bash scripts/demo_lr3.sh` (шаги 5–8), затем `bash scripts/demo_lr3.sh --expect-down`; терминалы 1–2 — логи |
| 4:20–5:20 | Код. Декоратор `rpc` переводит доменные исключения в статусы gRPC. `RagClient`: один канал на процесс, deadline (Ask 120 с), `AioRpcError` → `RagError`. `RagService._translate` → 422/404/409/503/504. Модель вызывается через `asyncio.to_thread`, чтобы не блокировать event loop. Reflection: `grpcurl` видит методы без `.proto` | VS Code: `servicer.py`, `rag_client.py`; терминал 3: `grpcurl -plaintext localhost:50051 list` и `describe corag.rag.v1.RagService` |
| 5:20–7:00 | Резерв на вопросы | — |

## 2. Минимальная теория

### 2.1. RPC и REST

RPC (Remote Procedure Call) — вызов функции в другом процессе так, будто она локальная: клиент вызывает `stub.Ask(request)`, библиотека сериализует аргументы, передаёт их по сети и возвращает результат или ошибку. Контракт — набор **процедур** с типизированными параметрами.

REST — архитектурный стиль, где контракт — **ресурсы** (URL) и стандартные методы HTTP (`GET`, `POST`…), а состояние передаётся представлениями (обычно JSON).

| | REST (API CO-RAG) | gRPC (RAG Service) |
| --- | --- | --- |
| Контракт | ресурсы и методы HTTP, OpenAPI генерируется из кода | процедуры в `.proto`, код генерируется из контракта |
| Формат | JSON, текст | Protocol Buffers, двоичный |
| Транспорт | HTTP/1.1 или 2 | только HTTP/2 |
| Клиенты | любые, включая браузер | stub из сгенерированного кода; браузер — только через gRPC-Web |

Поэтому в CO-RAG наружу смотрит REST, а между сервисами используется gRPC.

### 2.2. gRPC поверх HTTP/2

HTTP/2 (RFC 9113) передаёт данные **двоичными фреймами** (HEADERS, DATA, …). Каждый фрейм принадлежит **потоку** (stream) с номером. В одном TCP-соединении одновременно идут сотни потоков — это **мультиплексирование**, без блокировки «один запрос за другим». Заголовки сжимаются HPACK, у каждого потока своё управление потоком данных (flow control).

Один gRPC-вызов — один поток:

- HEADERS: `:path /corag.rag.v1.RagService/Ask`, `content-type: application/grpc`, `grpc-timeout`;
- DATA: сообщение Protobuf с 5-байтовым префиксом (флаг сжатия и длина);
- в конце — **trailers** `grpc-status` и `grpc-message`.

Поэтому `RagClient` держит **один канал** на процесс: все запросы API идут по одному HTTP/2-соединению. Канал на каждый запрос означал бы TCP + HTTP/2-handshake каждый раз.

**4 типа вызовов:**

- unary — запрос → ответ (все три метода CO-RAG);
- server streaming — запрос → поток ответов (например, ответ LLM по токенам);
- client streaming — поток запросов → ответ (загрузка частями);
- bidirectional streaming — два независимых потока (чат).

**Кодогенерация.** `grpc_tools.protoc` по `.proto` создаёт:

- `rag_pb2.py` — классы сообщений;
- `rag_pb2_grpc.py` — `RagServiceStub` для клиента, базовый `RagServiceServicer` для сервера и функцию регистрации;
- `.pyi` — типы для IDE.

**Deadline** — абсолютный срок, до которого клиент ждёт ответа. Он передаётся серверу заголовком `grpc-timeout`, а сервер видит остаток через `context.time_remaining()`. В CO-RAG: Ask 120 с, Search 15 с, IndexIssue 30 мин. `RagServicer` не ждёт LLM дольше остатка deadline.

**Статусы** — `OK`, `INVALID_ARGUMENT`, `NOT_FOUND`, `FAILED_PRECONDITION`, `UNAVAILABLE`, `DEADLINE_EXCEEDED`, `INTERNAL` и другие (всего 17). API переводит их в HTTP:

| gRPC | HTTP | Когда в CO-RAG |
| --- | --- | --- |
| `INVALID_ARGUMENT` | 422 | пустой вопрос, `top_k` вне диапазона |
| `NOT_FOUND` | 404 | reindex несуществующего выпуска |
| `FAILED_PRECONDITION` | 409 | у страниц выпуска нет текста |
| `UNAVAILABLE` | 503 | RAG Service остановлен или LLM недоступна |
| `DEADLINE_EXCEEDED` | 504 | ответа нет до deadline |
| прочие (`INTERNAL`) | 502 | ошибка внутри RAG Service |

**Health checking** — стандартный сервис `grpc.health.v1.Health/Check`: `SERVING` или `NOT_SERVING`. При остановке сервер сначала переходит в NOT_SERVING, затем ждёт активные вызовы (`server.stop(grace=5)`). В ЛР5 на этом строится healthcheck контейнера.

**Reflection** — сервис, по которому клиент вроде `grpcurl` узнаёт методы и типы без `.proto`-файла.

### 2.3. Protocol Buffers

Сообщение кодируется как последовательность пар «ключ — значение». Ключ = `(номер поля << 3) | wire type`, где wire type — способ кодирования: 0 — varint, 2 — длина + байты. **Имя поля в байты не попадает** — только номер. Настоящий вывод (`SerializeToString`):

```text
IndexIssueRequest(issue_id=12)       -> 08 0c                                    2 байта
AskRequest(question='ДОЭ', top_k=5)  -> 0a 06 d0 94 d0 9e d0 ad 10 05            10 байт
JSON того же: {"question": "ДОЭ", "top_k": 5}                                    34 байт
```

Как читать:

- `08` — поле 1, varint; `0c` — 12;
- `0a` — поле 1, строка длиной `06` байт (UTF-8 «ДОЭ»);
- `10 05` — поле 2 = 5.

Поля со значением по умолчанию (0, пустая строка) не передаются вовсе. Поэтому `top_k=0` в CO-RAG означает «не задано» → `TOP_K=5`. Для `year_from` нужен `optional`: так 0 отличается от «не задано» (`HasField`).

**Эволюция схемы:**

- номера полей не меняют и не переиспользуют (удалённые — `reserved`);
- новые поля добавляют с новыми номерами;
- тип поля не меняют несовместимо.

Старый клиент пропускает неизвестное поле: по wire type парсер знает его длину. Так в CO-RAG добавлены `Citation.issue_id = 9` и `authors = 10`: `Citation(chunk_id=7, authors=[…])` кодируется как `08 07 52 14 …`, где `52` — поле 10, тип 2.

### 2.4. Минимум по RAG

- **Наивный RAG** (retrieval-augmented generation): вопрос → эмбеддинг → top-k ближайших фрагментов → промпт «инструкция + фрагменты + вопрос» → ответ LLM со ссылками. Модель отвечает по найденному тексту архива, а не «по памяти».
- **Эмбеддинг** — вектор, у которого близкие по смыслу тексты оказываются рядом. jina-embeddings-v3 (XLM-RoBERTa, 570 млн параметров, 1024 измерения) мультиязычна. У неё LoRA-адаптеры под задачу: `retrieval.query` для вопроса и `retrieval.passage` для фрагмента — поиск асимметричный.
- **Косинусное сходство** `cos = a·b / (|a||b|)`; для L2-нормированных векторов это скалярное произведение. pgvector `<=>` — косинусное расстояние `1 − cos`. В CO-RAG `score = 1 − distance`.
- **ANN, HNSW** — приближённый поиск ближайших соседей по многоуровневому графу (`m=16`, `ef_construction=64`): почти логарифмическое время вместо полного перебора. Цена — приближённость. Кроме того, фильтр `WHERE year…` применяется к уже найденным кандидатам, поэтому строк может прийти меньше `top_k`.
- **Чанкинг.** Фрагмент — 512 токенов с перекрытием 64, в пределах одной страницы (ссылка ведёт на точную страницу). Формулы `$$…$$` и таблицы не режутся. Мелкий чанк теряет контекст, крупный размывает вектор и съедает окно LLM.
- **Промпт с источниками** — фрагменты нумеруются `[1]..[k]` с подписью «статья, авторы, год, №, с.», инструкция требует ссылок и отказа, если ответа нет. `citations` в ответе идут в том же порядке.
- **Порог и `found=false`.** Замер на сиде:
  - вопросы по архиву — лучший score 0,44–0,81;
  - вне архива — 0,03–0,15;
  - близкие по теме, но отсутствующие в архиве (голография, квантовый компьютер) — 0,22–0,29.

  `MIN_SCORE=0.35` лежит в зазоре. Если ничего не прошло порог, ответ без вызова LLM: ей нечего «додумывать».
- **Ограничения наивного RAG:**
  - нет переранжирования и переформулировки вопроса;
  - лексические промахи (формулы, аббревиатуры);
  - top-k может включать слабые фрагменты выше порога;
  - LLM на 7B может проигнорировать инструкцию;
  - нет памяти диалога.

### 2.5. Асинхронный сервис и блокирующий код

`grpc.aio` и FastAPI работают в одном event loop. `model.encode` на CPU занимает секунды, и если вызвать его прямо в корутине, на это время встанут все запросы и health-check. Поэтому encode идёт через `asyncio.to_thread` (в пуле потоков) под `asyncio.Lock` на **пачку**. Модель одна на процесс, а `Ask` может вклиниться между пачками долгой индексации.

### Ссылки

1. gRPC — Core concepts, architecture and lifecycle: <https://grpc.io/docs/what-is-grpc/core-concepts/>
2. gRPC — Deadlines; Status codes; Health checking: <https://grpc.io/docs/guides/deadlines/>, <https://grpc.github.io/grpc/core/md_doc_statuscodes.html>, <https://grpc.io/docs/guides/health-checking/>
3. Protocol Buffers — Encoding; Updating a message type: <https://protobuf.dev/programming-guides/encoding/>, <https://protobuf.dev/programming-guides/proto3/#updating>
4. RFC 9113 — HTTP/2: <https://www.rfc-editor.org/rfc/rfc9113>
5. pgvector — HNSW, операторы расстояний: <https://github.com/pgvector/pgvector>; jina-embeddings-v3: <https://huggingface.co/jinaai/jina-embeddings-v3>; Lewis et al., Retrieval-Augmented Generation (2020): <https://arxiv.org/abs/2005.11401>

## 3. Вопросы преподавателя с ответами

1. **Чем RPC отличается от REST?**
   В REST контракт — ресурсы и методы HTTP, данные обычно в JSON, клиентом может быть кто угодно. В RPC контракт — процедуры с типами; в gRPC это `.proto`, из которого генерируются клиент и сервер, а данные идут в двоичном Protobuf. CO-RAG использует оба: REST для внешних клиентов и браузера, gRPC между API и RAG Service.

2. **Что даёт HTTP/2 для gRPC?**
   Двоичные фреймы и мультиплексирование: много вызовов параллельно в одном TCP-соединении, без блокировки очереди на уровне HTTP. Плюс сжатие заголовков HPACK, flow control и trailers — в них передаётся `grpc-status`. Поэтому в `RagClient` один канал на процесс (`packages/corag-contracts/src/corag_contracts/rag_client.py` → `RagClient.__init__`).

3. **Зачем номера полей в Protobuf и как сохранить обратную совместимость?**
   В байты попадает номер поля и wire type, а не имя: `IndexIssueRequest(issue_id=12)` — это `08 0c`. Номера нельзя менять и переиспользовать, удалённые помечают `reserved`. Новые поля добавляют с новыми номерами, а старые клиенты их пропускают. Так в `Citation` добавлены `issue_id = 9` и `authors = 10` (`proto/rag/v1/rag.proto`).

4. **Какие бывают типы вызовов gRPC? Какой у вас?**
   Unary, server streaming, client streaming и bidirectional streaming. В CO-RAG все три метода unary: вопрос → полный ответ, выпуск → итог индексации. Стриминг токенов LLM (server streaming) — возможное развитие.

5. **Что такое deadline и что будет, если он истёк?**
   Это срок, до которого клиент ждёт ответа. Он передаётся серверу в `grpc-timeout`, и по истечении клиент получает `DEADLINE_EXCEEDED`, а вызов на сервере отменяется. У нас Ask — 120 с, Search — 15 с, IndexIssue — 30 мин (`RagClient`). `RagServicer._llm_timeout` не ждёт LLM дольше остатка deadline, а API отвечает 504.

6. **Как ошибки gRPC превращаются в HTTP-коды?**
   В RAG декоратор `rpc` (`services/rag/src/corag_rag/servicer.py`) ловит доменные исключения и вызывает `context.abort` с нужным статусом. `map_rpc_error` в `RagClient` превращает `AioRpcError` в `RagUnavailable`, `RagNotFound` и т. п. `RagService._translate` в API даёт `ServiceUnavailable` → 503, `UpstreamTimeout` → 504 и так далее, а exception handlers — JSON `{"detail": …}`.

7. **Что генерирует protoc и почему вы правите импорт?**
   `rag_pb2.py` — классы сообщений, `rag_pb2_grpc.py` — `RagServiceStub` и `RagServiceServicer` с функцией регистрации, `.pyi` — типы. Сгенерированный `rag_pb2_grpc.py` импортирует `from rag.v1 import rag_pb2` по пути proto-файла, а в пакете это `corag_contracts.rag.v1`. Поэтому `scripts/gen_proto.sh` правит импорт `sed`-ом; руками код не трогаем, генерацию повторяем скриптом.

8. **Почему RAG — отдельный процесс, а не модуль API?**
   В нём тяжёлые модели: процесс с Jina занимает 3,2 ГБ ОЗУ и грузится 5 с, а инференс нагружает CPU. API должен стартовать быстро и масштабироваться воркерами Gunicorn, не загружая модель в каждый воркер. Отдельный процесс масштабируется и перезапускается независимо, а `IndexIssue` из ЛР4 вызывает и OCR Worker. Это требование методички: API и gRPC-сервис — разные процессы.

9. **Можно ли вызвать gRPC из браузера?**
   Напрямую нет: браузерный JS не управляет фреймами HTTP/2 и не читает trailers. Нужен gRPC-Web и прокси (Envoy) или REST-шлюз. В CO-RAG браузер ходит в REST API, а API — в RAG по gRPC.

10. **Как работает наивный RAG в вашем проекте?**
    `RagServicer.Ask`: валидация → `Retriever.search`. Ретривер делает эмбеддинг вопроса (`retrieval.query`), вызывает `ChunkRepository.nearest` (pgvector `<=>`, HNSW, LEFT JOIN статьи по диапазону страниц), убирает дубли и отсекает по `MIN_SCORE`. Затем `build_messages` нумерует фрагменты [1]..[k], LLM отвечает со ссылками, `citations` идут в том же порядке.

11. **Что такое эмбеддинги и почему косинусное сходство?**
    Эмбеддинг — вектор смысла текста; у jina-embeddings-v3 — 1024 числа. Косинус меряет угол между векторами и не зависит от длины текста. Для L2-нормированных векторов это просто скалярное произведение. Для вопроса про вихревые пучки сходство со своим фрагментом 0,59, с фрагментом про нейросети — 0,05.

12. **Как выбран размер чанка?**
    512 токенов с перекрытием 64 — компромисс. Мелкий фрагмент теряет контекст, крупный размывает вектор и съедает окно LLM: 5 × 512 токенов плюс инструкция укладываются в `OLLAMA_CONTEXT_LENGTH=8192`. Чанк не выходит за страницу, чтобы ссылка вела на точную страницу; формулы и таблицы не режутся (`MarkdownChunker` в `services/rag/src/corag_rag/chunker.py`).

13. **Как вы боретесь с галлюцинациями?**
    Первое — порог `MIN_SCORE=0.35`, подобранный замером: вне архива лучший score 0,03–0,15, по архиву 0,44–0,81. Если ни один фрагмент не прошёл порог, сразу `found=false` и стандартный ответ без вызова LLM. Второе — инструкция «отвечать только по фрагментам, ставить [n], иначе сказать, что ответа нет». Третье — источники в ответе, их можно проверить. Полностью это галлюцинации не исключает — ограничение наивного RAG.

14. **Почему Jina на CPU?**
    Политика GPU: 8 ГБ VRAM на всех. qwen2.5:7b-instruct с контекстом 8192 по `ollama ps` занимает 5,1 ГБ, всего на карте занято 7,5 из 8 ГБ, а в ЛР4 нужен ещё DeepSeek-OCR. Jina на CPU тратит ~1,5 с на эмбеддинг вопроса, что терпимо при бюджете ответа ≤ 30 с. `EMBED_DEVICE=cuda` (fp16) включается переменной, если память позволяет.

15. **Зачем `asyncio.to_thread` и один канал на процесс?**
    `model.encode` синхронный и занимает секунды: в корутине он остановил бы event loop, и встали бы все вызовы и health-check. `to_thread` уносит его в поток (`JinaEmbedder._embed`). Канал gRPC — это долгоживущее HTTP/2-соединение с переподключением. Создавать его на запрос — лишние TCP-handshake и потеря мультиплексирования, поэтому `RagClient` создаётся один раз в `lifespan` API.
