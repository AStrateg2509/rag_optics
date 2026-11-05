"""Сравнение LLM-кандидатов на одних и тех же фрагментах архива (выбор LLM_MODEL, ЛР3).

    uv run --package corag-rag python scripts/compare_llm.py qwen2.5:7b-instruct gemma3:4b qwen3:8b

Для каждого вопроса фрагменты ищутся один раз (Jina + pgvector, как в Ask), затем каждой модели
отправляется одинаковый промпт (prompts.build_messages). Первый вызов модели — прогрев
(загрузка в видеопамять), он не входит в среднее. Видеопамять — по nvidia-smi после прогрева.
Нужны: PostgreSQL с проиндексированным сидом и Ollama с моделями (ollama pull …).
"""

import asyncio
import re
import subprocess
import sys
import time

import httpx

from corag_db.session import dispose_engine, get_session
from corag_rag.embedder import JinaEmbedder
from corag_rag.llm import OpenAICompatibleClient
from corag_rag.prompts import build_messages
from corag_rag.retriever import Retriever
from corag_rag.settings import get_settings

QUESTIONS = [
    "Как рассчитать ДОЭ для фокусировки лазерного излучения в кольцо?",
    "Что такое топологический заряд вихревого пучка и как формируются такие пучки?",
    "Какая точность классификации гиперспектральных изображений и на каком наборе данных?",
    # Фрагменты найдутся (сходство выше порога), но ответа на вопрос в них нет
    "Какой лазер использовался в эксперименте по острой фокусировке вихревого пучка?",
]


def vram_mib() -> str:
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout.strip() + " MiB"
    except (OSError, subprocess.CalledProcessError):
        return "?"


async def unload(base_url: str, model: str) -> None:
    """Выгрузить модель из видеопамяти Ollama (keep_alive: 0), чтобы замер VRAM был честным."""
    root = base_url.removesuffix("/v1")
    async with httpx.AsyncClient() as http:
        await http.post(f"{root}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60)


async def main(models: list[str]) -> None:
    settings = get_settings()
    embedder = JinaEmbedder.load(settings)
    retriever = Retriever(embedder, get_session, settings.MIN_SCORE)
    contexts = []
    for question in QUESTIONS:
        result = await retriever.search(question, settings.TOP_K)
        contexts.append((question, result.hits))
        print(f"• {question}\n  фрагментов: {len(result.hits)}, best {result.best_score:.2f}")

    for model in models:  # видеопамять свободна от всех кандидатов перед замерами
        await unload(settings.LLM_BASE_URL, model)
    print(f"\nVRAM без LLM: {vram_mib()}")
    for model in models:
        llm = OpenAICompatibleClient(
            settings.LLM_BASE_URL, model, settings.LLM_API_KEY, settings.LLM_TEMPERATURE
        )
        print(f"\n=== {model}")
        started = time.perf_counter()
        await llm.chat(build_messages(*contexts[0]), timeout_s=300)
        warmup = time.perf_counter() - started
        print(f"прогрев (загрузка в VRAM): {warmup:.1f} s, VRAM {vram_mib()}")
        times = []
        for question, hits in contexts:
            started = time.perf_counter()
            answer = await llm.chat(build_messages(question, hits), timeout_s=300)
            times.append(time.perf_counter() - started)
            refs = sorted(set(re.findall(r"\[\d+\]", answer)))
            print(f"\n[{times[-1]:.1f} s, ссылки {' '.join(refs) or '—'}] {question}\n{answer}")
        print(f"\nсреднее время ответа {model}: {sum(times) / len(times):.1f} s")
        await llm.close()
        await unload(settings.LLM_BASE_URL, model)
    await dispose_engine()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:]))
