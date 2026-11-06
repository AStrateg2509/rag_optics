# ЛР3 — шпаргалка

## Тезисы

1. `corag.rag.v1.RagService`: **unary** `Ask`, `Search`, `IndexIssue` и **server streaming** `AskStream` (источники, затем текст LLM частями). Контракт — `rag.proto`, код — `scripts/gen_proto.sh`.
2. **Protobuf**: в байтах только номер поля и wire type (`issue_id=12` → `08 0c`). Новые поля получают новые номера: `Citation.issue_id = 9`, `authors = 10`.
3. **Deadline**: Ask 120 с, Search 15 с, IndexIssue 30 мин; LLM ждём не дольше остатка deadline.
4. **Статусы → HTTP**: `INVALID_ARGUMENT` 422, `NOT_FOUND` 404, `FAILED_PRECONDITION` 409, `UNAVAILABLE` 503, `DEADLINE_EXCEEDED` 504, прочие 502.
5. **`found=false` без LLM**: лучший score < `MIN_SCORE=0.35` (по архиву 0,44–0,81, вне архива 0,03–0,15).
6. **Один канал на процесс** — создаётся в `lifespan` API; backoff переподключения ≤ 5 с.
7. **Jina на CPU** (политика GPU): `encode` через `asyncio.to_thread`, блокировка на пачку. **LLM в Ollama на GPU**, контекст 8192.
8. **IndexIssue идемпотентен**: эмбеддинги считаются вне транзакции, затем `replace_for_pages` и статус `indexed` в одной транзакции.

## Команды

```bash
docker compose -f docker-compose.dev.yml up -d                        # postgres + ollama + nginx
docker compose -f docker-compose.dev.yml exec ollama ollama pull qwen2.5:7b-instruct
bash scripts/gen_proto.sh && git status --short                       # генерация воспроизводима
uv run --package corag-rag python -m corag_rag.server                 # терминал 1: [rag] …
uv run --package corag-api uvicorn corag_api.main:create_app --factory  # терминал 2: [api] …
bash scripts/demo_lr3.sh                                              # весь сценарий
bash scripts/demo_lr3.sh --expect-down                                # RAG остановлен → 503
TOKEN=$(curl -s -X POST localhost:8000/auth/login \
  -d 'username=researcher@corag.local&password=researcher12345' | jq -r .access_token)
curl -s -X POST localhost:8000/rag/ask -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' -d '{"question": "Что такое топологический заряд?"}' | jq
grpcurl -plaintext localhost:50051 list                               # reflection
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check        # SERVING
uv run --package corag-api python scripts/ask_stream.py "Что такое топологический заряд?"  # AskStream
uv run pytest -q && uv run pytest -q -m slow tests/rag && uv run ruff check .
```

## Где в коде

| Понятие | Файл | Функция / класс |
| --- | --- | --- |
| Контракт | `proto/rag/v1/rag.proto` | `RagService`, `Citation` |
| gRPC-сервер, health, reflection | `services/rag/src/corag_rag/server.py` | `serve` |
| Методы, исключения → статусы | `services/rag/…/servicer.py` | `RagServicer.Ask`, `AskStream`, `rpc`, `rpc_stream` |
| Эмбеддинги | `services/rag/…/embedder.py` | `JinaEmbedder.load`, `_embed` |
| Чанкинг | `services/rag/…/chunker.py` | `MarkdownChunker.split` |
| Поиск, порог | `services/rag/…/retriever.py` | `Retriever.search` |
| Промпт | `services/rag/…/prompts.py` | `build_messages`, `NOT_FOUND_ANSWER` |
| LLM (целиком и потоком SSE) | `services/rag/…/llm.py` | `OpenAICompatibleClient.chat`, `chat_stream` |
| Индексация | `services/rag/…/indexer.py` | `Indexer.index_issue` |
| Клиент, deadline, ошибки | `packages/corag-contracts/…/rag_client.py` | `RagClient`, `ask_stream`, `map_rpc_error` |
| gRPC → HTTP | `services/api/…/services/rag_service.py` | `RagService._call`, `_translate` |
| REST | `services/api/…/routers/rag.py`, `routers/issues.py` | `ask`, `search`, `reindex_issue` |

## Частые ошибки

- **`ModuleNotFoundError: rag.v1`**: protoc импортирует `rag_pb2` абсолютно. Чинит `sed` в `gen_proto.sh`, руками код не правим.
- **Канал на каждый запрос**: лишние TCP- и HTTP/2-handshake. Канал один, из `lifespan`.
- **Блокировка event loop**: `model.encode` прямо в корутине останавливает все вызовы и health. Вызывать через `asyncio.to_thread`.
- **VRAM и контекст**: Jina на CUDA + LLM + DeepSeek-OCR не помещаются в 8 ГБ. Без `OLLAMA_CONTEXT_LENGTH` промпт молча обрежется. Первый Ask после старта Ollama идёт ~60 с (загрузка модели) — прогреть заранее.
