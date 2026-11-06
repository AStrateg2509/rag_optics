"""Точка входа RAG Service: асинхронный gRPC-сервер.

Запуск: uv run --package corag-rag python -m corag_rag.server
Проверка: grpcurl -plaintext localhost:50051 list   (reflection)
"""

import asyncio
import logging
import signal

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from corag_contracts.rag.v1 import rag_pb2, rag_pb2_grpc
from corag_db.session import dispose_engine, get_session, ping
from corag_rag.chunker import MarkdownChunker
from corag_rag.embedder import JinaEmbedder
from corag_rag.indexer import Indexer
from corag_rag.llm import OpenAICompatibleClient
from corag_rag.retriever import Retriever
from corag_rag.servicer import RagServicer
from corag_rag.settings import RagSettings, get_settings

log = logging.getLogger("corag_rag")

SERVICE_NAME = rag_pb2.DESCRIPTOR.services_by_name[
    "RagService"
].full_name  # corag.rag.v1.RagService
# Сколько секунд при остановке ждём завершения активных вызовов
GRACE_S = 5


async def serve(settings: RagSettings) -> None:
    async with get_session() as session:
        await ping(session)
    log.info("[rag] БД доступна")

    # Модель загружается один раз, до открытия порта: первый запрос не ждёт загрузки
    embedder = await asyncio.to_thread(JinaEmbedder.load, settings)
    await asyncio.to_thread(embedder.warmup)
    chunker = MarkdownChunker(
        embedder, settings.CHUNK_TOKENS, settings.CHUNK_OVERLAP, settings.CHUNK_MIN_TOKENS
    )
    llm = OpenAICompatibleClient(
        settings.LLM_BASE_URL, settings.LLM_MODEL, settings.LLM_API_KEY, settings.LLM_TEMPERATURE
    )
    await llm.probe()
    servicer = RagServicer(
        retriever=Retriever(embedder, get_session, settings.MIN_SCORE),
        indexer=Indexer(embedder, chunker, get_session, settings.EMBED_BATCH),
        llm=llm,
        settings=settings,
    )

    server = grpc.aio.server()
    rag_pb2_grpc.add_RagServiceServicer_to_server(servicer, server)
    # Health checking (grpc.health.v1) — для healthcheck контейнера в ЛР5
    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    # Reflection — grpcurl узнаёт методы и типы без .proto-файла
    reflection.enable_server_reflection(
        [SERVICE_NAME, health.SERVICE_NAME, reflection.SERVICE_NAME], server
    )
    address = f"{settings.RAG_GRPC_HOST}:{settings.RAG_GRPC_PORT}"
    server.add_insecure_port(address)
    await server.start()
    for name in ("", SERVICE_NAME):
        await health_servicer.set(name, health_pb2.HealthCheckResponse.SERVING)
    log.info(
        "[rag] gRPC-сервер слушает %s: %s (+ health, reflection); MIN_SCORE=%.2f, TOP_K=%d",
        address,
        SERVICE_NAME,
        settings.MIN_SCORE,
        settings.TOP_K,
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    # Корректная остановка: health → NOT_SERVING, новые вызовы отклоняются,
    # активные получают до GRACE_S секунд на завершение
    log.info("[rag] остановка: NOT_SERVING, ждём активные вызовы до %d s", GRACE_S)
    await health_servicer.enter_graceful_shutdown()
    await server.stop(grace=GRACE_S)
    await llm.close()
    await dispose_engine()
    log.info("[rag] остановлен")


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    # Каждый HTTP-запрос к LLM httpx пишет в INFO — в логе оставляем только наши строки
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
