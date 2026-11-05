"""Эмбеддинги текста: протоколы Embedder/TokenCounter и реализация на jina-embeddings-v3.

Модель загружается один раз при старте сервера (JinaEmbedder.load) и обслуживает
и вопросы (task="retrieval.query"), и фрагменты статей (task="retrieval.passage").
Тестовые двойники протоколов — только в tests/rag.
"""

import asyncio
import logging
import os
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol

import numpy as np

from corag_rag.settings import RagSettings

log = logging.getLogger("corag_rag")

QUERY_TASK = "retrieval.query"
PASSAGE_TASK = "retrieval.passage"
# Контекст модели — 8192 токена; чанки короче (CHUNK_TOKENS), вопрос — тем более
MAX_LENGTH = 8192


class TokenCounter(Protocol):
    """Токенизатор для чанкера: символьные границы токенов в тексте."""

    def spans(self, text: str) -> list[tuple[int, int]]: ...


class Embedder(Protocol):
    """Векторизация текста; векторы L2-нормированы, длина — dim."""

    dim: int

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]: ...

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]: ...


class JinaEmbedder:
    """jina-embeddings-v3 через transformers (trust_remote_code: код модели в репозитории HF)."""

    def __init__(self, model: Any, tokenizer: Any, *, name: str, dim: int, batch_size: int) -> None:
        self._model = model
        self._tokenizer = tokenizer
        self.name = name
        self.dim = dim
        self.batch_size = batch_size
        # Модель одна на процесс: пачки идут по очереди, а Ask может вклиниться
        # между пачками долгой индексации (блокировка — на пачку, а не на выпуск)
        self._lock = asyncio.Lock()

    @classmethod
    def load(cls, settings: RagSettings) -> "JinaEmbedder":
        """Скачать (при первом запуске) и загрузить модель. Блокирует поток — вызывать до старта."""
        # Кэш Hugging Face задаётся до импорта transformers/huggingface_hub
        os.environ.setdefault("HF_HOME", str(Path(settings.HF_HOME).resolve()))
        import torch
        from transformers import AutoModel, AutoTokenizer

        device = settings.EMBED_DEVICE
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("EMBED_DEVICE=cuda, но CUDA недоступна")
        # В config.json модели bfloat16; на CPU считаем в float32, на GPU — в float16
        dtype = torch.float16 if device == "cuda" else torch.float32
        log.info(
            "[rag] загрузка %s на %s (%s), кэш %s",
            settings.EMBED_MODEL,
            device,
            str(dtype).removeprefix("torch."),
            os.environ["HF_HOME"],
        )
        # Код модели предупреждает об отсутствии flash-attn на каждом из 24 слоёв — одной строкой
        logging.getLogger("transformers_modules").setLevel(logging.ERROR)
        log.info("[rag] attention: стандартный PyTorch (flash-attn не используется)")
        started = time.perf_counter()
        model = AutoModel.from_pretrained(
            settings.EMBED_MODEL, trust_remote_code=True, torch_dtype=dtype
        )
        model.to(device).eval()
        tokenizer = AutoTokenizer.from_pretrained(settings.EMBED_MODEL)
        embedder = cls(
            model,
            tokenizer,
            name=settings.EMBED_MODEL,
            dim=settings.EMBED_DIM,
            batch_size=settings.EMBED_BATCH,
        )
        log.info("[rag] модель эмбеддингов загружена за %.1f s", time.perf_counter() - started)
        return embedder

    def warmup(self) -> None:
        """Первый encode на CPU заметно медленнее остальных — делаем его до приёма запросов."""
        started = time.perf_counter()
        self._encode(["прогрев модели эмбеддингов"], QUERY_TASK)
        log.info("[rag] прогрев эмбеддера: %.1f s", time.perf_counter() - started)

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Символьные границы токенов (fast-токенизатор XLM-RoBERTa), без служебных токенов."""
        enc = self._tokenizer(
            text, add_special_tokens=False, return_offsets_mapping=True, verbose=False
        )
        return [(int(a), int(b)) for a, b in enc["offset_mapping"]]

    async def embed_queries(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, QUERY_TASK)

    async def embed_passages(self, texts: Sequence[str]) -> list[list[float]]:
        return await self._embed(texts, PASSAGE_TASK)

    async def _embed(self, texts: Sequence[str], task: str) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            async with self._lock:
                # encode блокирует поток на секунды — уводим его из event loop
                arr = await asyncio.to_thread(self._encode, batch, task)
            vectors.extend(arr.tolist())
        return vectors

    def _encode(self, texts: list[str], task: str) -> np.ndarray:
        arr = self._model.encode(
            texts,
            task=task,
            batch_size=len(texts),
            max_length=MAX_LENGTH,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        arr = np.asarray(arr, dtype=np.float32).reshape(len(texts), -1)
        if arr.shape[1] != self.dim:
            raise RuntimeError(f"модель вернула векторы длины {arr.shape[1]}, EMBED_DIM={self.dim}")
        return arr
