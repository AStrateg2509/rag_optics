"""Настройки API Service (переменные окружения и .env)."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

from corag_db.settings import DbSettings


class ApiSettings(BaseSettings):
    """Имена полей совпадают с переменными окружения (см. .env.example)."""

    # .env ищется вверх от текущего каталога, как в corag-db
    model_config = DbSettings.model_config

    DATABASE_URL: str = "postgresql+asyncpg://corag:corag@localhost:5432/corag"
    # Каталог сканов; scan_path выпуска хранится относительно него
    SCANS_DIR: Path = Path("data/scans")
    MAX_SCAN_MB: int = Field(200, ge=1)
    # Секрет подписи JWT (HS256), общий с Notification Service; не короче 32 символов
    JWT_SECRET: str = Field(min_length=32)
    JWT_TTL_MIN: int = Field(30, ge=1)
    # Префикс, под которым API опубликован за reverse proxy (за Nginx — /api)
    API_ROOT_PATH: str = ""
    LOG_LEVEL: str = "INFO"

    @property
    def max_scan_bytes(self) -> int:
        return self.MAX_SCAN_MB * 1024 * 1024


@lru_cache
def get_settings() -> ApiSettings:
    """Единый экземпляр настроек на процесс (в тестах подменяется через dependency_overrides)."""
    return ApiSettings()
