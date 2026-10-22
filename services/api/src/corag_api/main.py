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
    users,
)
from corag_api.settings import get_settings
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
    {"name": "service", "description": "Служебные эндпоинты"},
]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Пул соединений с БД живёт столько же, сколько процесс (воркер Gunicorn)."""
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    app.state.engine = engine
    app.state.sessionmaker = create_sessionmaker(engine)
    log.info("[api] старт: БД %s", engine.url.render_as_string(hide_password=True))
    yield
    await engine.dispose()
    log.info("[api] остановка: пул соединений закрыт")


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(
        level=settings.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    app = FastAPI(
        title="CO-RAG API",
        version="0.2.0",
        description="REST API архива журнала «Компьютерная оптика»",
        lifespan=lifespan,
        root_path=settings.API_ROOT_PATH,
        openapi_tags=TAGS,
    )
    register_exception_handlers(app)
    for module in (auth, users, issues, pages, articles, authors, keywords, ocr_jobs, health):
        app.include_router(module.router)
    # Заглушка брокера; в ЛР4 здесь будет AmqpPublisher
    app.state.publisher = LoggingPublisher()
    return app
