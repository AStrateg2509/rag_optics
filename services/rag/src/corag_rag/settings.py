"""Настройки RAG Service (переменные окружения и .env, см. .env.example)."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings

from corag_db.settings import DbSettings


class RagSettings(BaseSettings):
    """Имена полей совпадают с переменными окружения."""

    # .env ищется вверх от текущего каталога, как в corag-db и API
    model_config = DbSettings.model_config

    DATABASE_URL: str = "postgresql+asyncpg://corag:corag@localhost:5432/corag"
    # Адрес, на котором слушает gRPC-сервер: [::] — все интерфейсы
    RAG_GRPC_HOST: str = "[::]"
    RAG_GRPC_PORT: int = Field(50051, ge=1, le=65535)

    # --- Эмбеддинги (Jina) ---
    EMBED_MODEL: str = "jinaai/jina-embeddings-v3"
    # cpu по умолчанию: видеопамять нужна Ollama и DeepSeek-OCR (политика GPU в CLAUDE.md)
    EMBED_DEVICE: Literal["cpu", "cuda"] = "cpu"
    # Та же переменная задаёт колонку chunks.embedding VECTOR(EMBED_DIM) в corag-db
    EMBED_DIM: int = Field(1024, ge=32)
    EMBED_BATCH: int = Field(32, ge=1)
    # Кэш весов Hugging Face (каталог models/ в .gitignore)
    HF_HOME: str = "models/hf"

    # --- Чанкинг (в токенах токенизатора модели эмбеддингов) ---
    CHUNK_TOKENS: int = Field(512, ge=32)
    CHUNK_OVERLAP: int = Field(64, ge=0)
    CHUNK_MIN_TOKENS: int = Field(50, ge=0)

    # --- Поиск ---
    TOP_K: int = Field(5, ge=1)
    TOP_K_MAX: int = Field(20, ge=1)
    # Порог косинусного сходства: ниже — фрагмент не считается релевантным
    MIN_SCORE: float = Field(0.35, ge=-1, le=1)
    # Длина фрагмента (snippet) в источниках ответа, символов
    SNIPPET_CHARS: int = Field(300, ge=50)
    QUESTION_MAX_CHARS: int = Field(2000, ge=10)

    # --- LLM (OpenAI-совместимый API; Ollama: http://localhost:11434/v1) ---
    LLM_BASE_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "qwen2.5:7b-instruct"
    LLM_API_KEY: str = "ollama"
    LLM_TEMPERATURE: float = Field(0.1, ge=0, le=2)
    LLM_TIMEOUT_S: float = Field(90, gt=0)

    LOG_LEVEL: str = "INFO"

    @model_validator(mode="after")
    def _check(self) -> "RagSettings":
        if self.CHUNK_OVERLAP >= self.CHUNK_TOKENS:
            raise ValueError("CHUNK_OVERLAP должен быть меньше CHUNK_TOKENS")
        if self.TOP_K > self.TOP_K_MAX:
            raise ValueError("TOP_K не может быть больше TOP_K_MAX")
        return self


@lru_cache
def get_settings() -> RagSettings:
    return RagSettings()
