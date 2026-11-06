"""Вопрос к RAG Service потоком (AskStream, server streaming): текст печатается по мере генерации.

    uv run --package corag-api python scripts/ask_stream.py "Что такое топологический заряд?"

Адрес RAG Service — RAG_ADDR из .env (как у API). Отметки времени — от отправки запроса.
"""

import asyncio
import sys
import time

from corag_api.settings import get_settings
from corag_contracts.rag_client import AskSources, RagClient


async def main(question: str) -> None:
    async with RagClient(get_settings().RAG_ADDR) as rag:
        started = time.perf_counter()
        first_text: float | None = None
        async for event in rag.ask_stream(question):
            elapsed = time.perf_counter() - started
            if isinstance(event, AskSources):
                print(f"[{elapsed:.1f} s] источники: found={str(event.found).lower()}")
                for n, c in enumerate(event.citations, 1):
                    title = c.article_title or "вне статьи"
                    print(f"  [{n}] «{title}», {c.year} № {c.issue_number}, с. {c.page_number}")
                continue
            if first_text is None:
                first_text = elapsed
                print(f"[{elapsed:.1f} s] ответ:")
            print(event, end="", flush=True)
        print(f"\n[{time.perf_counter() - started:.1f} s] поток закрыт (OK)")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
