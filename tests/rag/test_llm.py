"""OpenAICompatibleClient без сети: httpx.MockTransport отдаёт ответы в формате Ollama /v1."""

import json

import httpx
import pytest

from corag_rag.errors import LLMUnavailable
from corag_rag.llm import OpenAICompatibleClient, ThinkFilter

MESSAGES = [{"role": "user", "content": "вопрос"}]


def sse(*pieces: str, usage: dict | None = None) -> str:
    """Тело потока как у Ollama: data: {chunk} на каждую часть, usage, затем [DONE]."""
    events = [{"choices": [{"index": 0, "delta": {"content": p}}]} for p in pieces]
    events.append({"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})
    if usage:
        events.append({"choices": [], "usage": usage})
    lines = [f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in events]
    return (
        "".join(lines) + "data: [DONE]\n\n" + 'data: {"choices": [{"delta": {"content": "x"}}]}\n\n'
    )


def client(handler) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        "http://llm/v1", "test-model", "key", 0.1, transport=httpx.MockTransport(handler)
    )


async def collect(llm: OpenAICompatibleClient) -> list[str]:
    return [part async for part in llm.chat_stream(MESSAGES, timeout_s=5)]


async def test_chat_stream_yields_deltas_until_done():
    requests: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = sse("Топологический", " заряд", " [1].", usage={"prompt_tokens": 9})
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    llm = client(handler)
    # Строка после [DONE] не читается
    assert await collect(llm) == ["Топологический", " заряд", " [1]."]
    assert requests[0]["stream"] is True
    assert requests[0]["stream_options"] == {"include_usage": True}
    assert requests[0]["model"] == "test-model"
    await llm.close()


async def test_chat_stream_strips_think_split_across_parts():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse("<thi", "nk>рассуждаю", "…</think>", "\nОтвет", " [1]"))

    llm = client(handler)
    assert "".join(await collect(llm)) == "Ответ [1]"
    await llm.close()


def test_think_filter_passes_plain_text_and_flushes_short_answer():
    plain = ThinkFilter()
    assert [plain.feed(p) for p in ["Отв", "ет"]] == ["Отв", "ет"]
    short = ThinkFilter()
    assert short.feed("<") == ""  # пока похоже на начало <think>
    assert short.flush() == "<"
    thinking = ThinkFilter()
    assert thinking.feed("<think>не закончено") == ""
    assert thinking.flush() == ""


@pytest.mark.parametrize(("status", "message"), [(404, "не найдена"), (503, "ответил 503")])
async def test_chat_stream_server_errors_are_unavailable(status, message):
    llm = client(lambda request: httpx.Response(status, text="model not found"))
    with pytest.raises(LLMUnavailable, match=message):
        await collect(llm)
    await llm.close()


async def test_chat_stream_connection_error_is_unavailable():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    llm = client(handler)
    with pytest.raises(LLMUnavailable, match="недоступен"):
        await collect(llm)
    await llm.close()


async def test_chat_without_stream_strips_think():
    def handler(request: httpx.Request) -> httpx.Response:
        content = "<think>рассуждаю</think>\nОтвет [1]."
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    llm = client(handler)
    assert await llm.chat(MESSAGES, timeout_s=5) == "Ответ [1]."
    await llm.close()
