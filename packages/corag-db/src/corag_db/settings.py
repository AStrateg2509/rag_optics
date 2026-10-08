"""Настройки подключения к БД (читаются из переменных окружения и .env)."""

from functools import lru_cache

from dotenv import find_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


class DbSettings(BaseSettings):
    """Параметры слоя данных. Имена полей совпадают с переменными окружения."""

    # .env ищется вверх от текущего каталога: работает и из корня, и из packages/corag-db
    model_config = SettingsConfigDict(env_file=find_dotenv(usecwd=True) or None, extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://corag:corag@localhost:5432/corag"
    TEST_DATABASE_URL: str = "postgresql+asyncpg://corag:corag@localhost:5432/corag_test"
    ECHO_SQL: bool = False
    EMBED_DIM: int = 1024


@lru_cache
def get_settings() -> DbSettings:
    """Единый экземпляр настроек на процесс."""
    return DbSettings()
