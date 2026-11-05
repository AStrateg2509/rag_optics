"""Клиент LLM: протокол LLMClient и реализация для OpenAI-совместимого API (Ollama, vLLM).

Модель меняется переменной LLM_MODEL без изменения кода (ТЗ, 4.8).
"""

import logging
import re
import time
from typing import Protocol

import httpx

from corag_rag.errors import LLMTimeout, LLMUnavailable

log = logging.getLogger("corag_rag")

# Модели с «рассуждениями» (qwen3 и т. п.) могут вернуть их в тексте ответа
THINK = re.compile(r"<think>.*?</think>", re.DOTALL)


class LLMClient(Protocol):
    model: str

    async def chat(self, messages: list[dict[str, str]], timeout_s: float) -> str: ...


class OpenAICompatibleClient:
    """POST {LLM_BASE_URL}/chat/completions; один HTTP-клиент (пул соединений) на процесс."""

    def __init__(self, base_url: str, model: str, api_key: str, temperature: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._http = httpx.AsyncClient(
            base_url=self.base_url + "/", headers={"Authorization": f"Bearer {api_key}"}
        )

    async def chat(self, messages: list[dict[str, str]], timeout_s: float) -> str:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": False,
        }
        started = time.perf_counter()
        try:
            resp = await self._http.post("chat/completions", json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"LLM {self.model} не ответила за {timeout_s:.0f} s") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable(f"LLM-сервер {self.base_url} недоступен: {exc}") from exc
        if resp.status_code == 404:
            raise LLMUnavailable(f"модель {self.model} не найдена на LLM-сервере: {resp.text}")
        if resp.status_code >= 500:
            raise LLMUnavailable(f"LLM-сервер ответил {resp.status_code}: {resp.text[:200]}")
        resp.raise_for_status()
        data = resp.json()
        text = THINK.sub("", data["choices"][0]["message"].get("content") or "").strip()
        usage = data.get("usage") or {}
        log.info(
            "[rag] ← LLM %s %.1f s, токены: промпт %s, ответ %s",
            self.model,
            time.perf_counter() - started,
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )
        return text

    async def probe(self) -> None:
        """Проверка при старте: LLM-сервер отвечает и модель скачана (только предупреждение)."""
        try:
            resp = await self._http.get("models", timeout=5)
            resp.raise_for_status()
            # Ollama без скачанных моделей отвечает "data": null
            models = {m["id"] for m in resp.json().get("data") or []}
        except (httpx.HTTPError, ValueError) as exc:
            log.warning(
                "[rag] LLM-сервер %s недоступен: %s — Ask вернёт UNAVAILABLE", self.base_url, exc
            )
            return
        if self.model in models:
            log.info("[rag] LLM %s доступна на %s", self.model, self.base_url)
        else:
            log.warning(
                "[rag] модели %s нет на LLM-сервере (есть: %s) — ollama pull %s",
                self.model,
                ", ".join(sorted(models)) or "—",
                self.model,
            )

    async def close(self) -> None:
        await self._http.aclose()
