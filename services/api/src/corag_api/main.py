"""Точка входа API Service: фабрика приложения FastAPI.

Разработка: uvicorn corag_api.main:create_app --factory --reload
Продакшен:  gunicorn 'corag_api.main:create_app()' -k uvicorn_worker.UvicornWorker -w 2
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from corag_api.errors import register_exception_handlers
from corag_api.publishers import LoggingPublisher
from corag_api.routers import (
    articles,
    auth,
    authors,
    health,
    issues,
    keywords,
    ocr_jobs,
    pages,
    rag,
    users,
)
from corag_api.settings import get_settings
from corag_contracts.rag_client import RagClient
from corag_db.session import create_engine, create_sessionmaker

log = logging.getLogger("corag_api")

TAGS = [
    {"name": "auth", "description": "Регистрация и вход (JWT)"},
    {"name": "users", "description": "Профиль; управление пользователями — admin"},
    {"name": "issues", "description": "Выпуски, сканы и страницы"},
    {"name": "articles", "description": "Каталог и разметка статей"},
    {"name": "authors", "description": "Авторы"},
    {"name": "keywords", "description": "Ключевые слова"},
    {"name": "ocr", "description": "Запуск распознавания и статус задач — admin"},
    {"name": "rag", "description": "Вопросы к архиву: ответ LLM с источниками (RAG Service, gRPC)"},
    {"name": "service", "description": "Служебные эндпоинты"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Пул соединений с БД и gRPC-канал к RAG Service живут столько же, сколько процесс."""
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    # Один канал на процесс: HTTP/2-соединение переиспользуется всеми запросами
    app.state.rag_client = RagClient(
        settings.RAG_ADDR,
        ask_timeout_s=settings.RAG_ASK_TIMEOUT_S,
        search_timeout_s=settings.RAG_SEARCH_TIMEOUT_S,
        index_timeout_s=settings.RAG_INDEX_TIMEOUT_S,
    )
    log.info(
        "[api] старт: БД %s, RAG Service %s (gRPC)",
        engine.url.render_as_string(hide_password=True),
        settings.RAG_ADDR,
    )
    yield
    await app.state.rag_client.close()
    await engine.dispose()
    log.info("[api] остановка: gRPC-канал и пул соединений закрыты")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    app = FastAPI(
        title="CO-RAG API",
        version="0.3.0",
        description="REST API архива журнала «Компьютерная оптика»",
        lifespan=lifespan,
        root_path=settings.API_ROOT_PATH,
        openapi_tags=TAGS,
    )
    register_exception_handlers(app)
    routers = (auth, users, issues, pages, articles, authors, keywords, ocr_jobs, rag, health)
    for module in routers:
        app.include_router(module.router)
    # Заглушка брокера; в ЛР4 здесь будет AmqpPublisher
    app.state.publisher = LoggingPublisher()
    return app
