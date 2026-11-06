"""Клиент LLM: протокол LLMClient и реализация для OpenAI-совместимого API (Ollama, vLLM).

Модель меняется переменной LLM_MODEL без изменения кода (ТЗ, 4.8).
"""

import json
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Protocol

import httpx

from corag_rag.errors import LLMTimeout, LLMUnavailable

log = logging.getLogger("corag_rag")

# Модели с «рассуждениями» (qwen3 и т. п.) могут вернуть их в тексте ответа
THINK = re.compile(r"<think>.*?</think>", re.DOTALL)
THINK_OPEN, THINK_CLOSE = "<think>", "</think>"


class LLMClient(Protocol):
    model: str

    async def chat(self, messages: list[dict[str, str]], timeout_s: float) -> str: ...

    def chat_stream(self, messages: list[dict[str, str]], timeout_s: float) -> AsyncIterator[str]:
        """Ответ частями по мере генерации (async-генератор)."""
        ...


class OpenAICompatibleClient:
    """POST {LLM_BASE_URL}/chat/completions; один HTTP-клиент (пул соединений) на процесс."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        temperature: float,
        *,
        transport: httpx.AsyncBaseTransport | None = None,  # подмена сети в тестах
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self._http = httpx.AsyncClient(
            base_url=self.base_url + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            transport=transport,
        )

    def _payload(self, messages: list[dict[str, str]], *, stream: bool) -> dict:
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "stream": stream,
        }
        if stream:  # последний чанк потока принесёт счётчики токенов
            payload["stream_options"] = {"include_usage": True}
        return payload

    def _check_status(self, resp: httpx.Response, body: str) -> None:
        if resp.status_code == 404:
            raise LLMUnavailable(f"модель {self.model} не найдена на LLM-сервере: {body}")
        if resp.status_code >= 500:
            raise LLMUnavailable(f"LLM-сервер ответил {resp.status_code}: {body[:200]}")
        resp.raise_for_status()

    async def chat(self, messages: list[dict[str, str]], timeout_s: float) -> str:
        payload = self._payload(messages, stream=False)
        started = time.perf_counter()
        try:
            resp = await self._http.post("chat/completions", json=payload, timeout=timeout_s)
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"LLM {self.model} не ответила за {timeout_s:.0f} s") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable(f"LLM-сервер {self.base_url} недоступен: {exc}") from exc
        self._check_status(resp, resp.text)
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

    async def chat_stream(
        self, messages: list[dict[str, str]], timeout_s: float
    ) -> AsyncIterator[str]:
        """Ответ потоком: Server-Sent Events `data: {chunk}` … `data: [DONE]`.

        timeout_s — срок на весь ответ: проверяется после каждой строки потока, а httpx
        прерывает ожидание, если очередная строка не пришла за оставшееся время.
        """
        started = time.perf_counter()
        deadline = started + timeout_s
        first: float | None = None
        parts = 0
        usage: dict = {}
        think = ThinkFilter()
        try:
            async with self._http.stream(
                "POST",
                "chat/completions",
                json=self._payload(messages, stream=True),
                timeout=timeout_s,
            ) as resp:
                if resp.status_code >= 400:
                    self._check_status(resp, (await resp.aread()).decode(errors="replace"))
                async for line in resp.aiter_lines():
                    if time.perf_counter() > deadline:
                        raise LLMTimeout(
                            f"LLM {self.model} не закончила ответ за {timeout_s:.0f} s"
                        )
                    if not line.startswith("data: "):
                        continue  # пустые строки-разделители событий SSE
                    data = line.removeprefix("data: ")
                    if data == "[DONE]":
                        break
                    chunk = json.loads(data)
                    usage = chunk.get("usage") or usage
                    for choice in chunk.get("choices") or []:
                        delta = think.feed((choice.get("delta") or {}).get("content") or "")
                        if not delta:
                            continue
                        if first is None:
                            first = time.perf_counter() - started
                            log.info(
                                "[rag] ← LLM %s: первый фрагмент через %.1f s", self.model, first
                            )
                        parts += 1
                        yield delta
                if rest := think.flush():
                    parts += 1
                    yield rest
        except httpx.TimeoutException as exc:
            raise LLMTimeout(f"LLM {self.model} не ответила за {timeout_s:.0f} s") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable(f"LLM-сервер {self.base_url} недоступен: {exc}") from exc
        log.info(
            "[rag] ← LLM %s поток %.1f s, %d частей, токены: промпт %s, ответ %s",
            self.model,
            time.perf_counter() - started,
            parts,
            usage.get("prompt_tokens", "?"),
            usage.get("completion_tokens", "?"),
        )

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


class ThinkFilter:
    """Вырезает из потока «рассуждения» <think>…</think> в начале ответа.

    Тег может прийти разорванным на части («<thi» + «nk>»), поэтому начало ответа
    копится, пока не станет ясно, есть ли там <think>.
    """

    def __init__(self) -> None:
        self._buf = ""
        self._passthrough = False
        self._skip_space = False  # после </think>: пропустить пробелы перед ответом

    def feed(self, piece: str) -> str:
        if self._passthrough:
            return self._after_think(piece)
        self._buf += piece
        head = self._buf.lstrip()
        if len(head) < len(THINK_OPEN) and THINK_OPEN.startswith(head):
            return ""  # пока неясно: копим
        if not head.startswith(THINK_OPEN):
            self._passthrough = True
            return self._buf
        if THINK_CLOSE not in head:
            return ""  # внутри рассуждений
        self._passthrough = self._skip_space = True
        return self._after_think(head.split(THINK_CLOSE, 1)[1])

    def flush(self) -> str:
        """Конец потока: отдать накопленное, если это оказались не рассуждения (ответ «<»)."""
        if self._passthrough or self._buf.lstrip().startswith(THINK_OPEN):
            return ""
        return self._buf

    def _after_think(self, piece: str) -> str:
        if self._skip_space:
            piece = piece.lstrip()
            self._skip_space = not piece
        return piece
