"""Настоящая jina-embeddings-v3 (скачивание весов при первом запуске): uv run pytest -m slow."""

import numpy as np
import pytest

from corag_rag.chunker import MarkdownChunker
from corag_rag.embedder import JinaEmbedder
from corag_rag.settings import RagSettings

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def jina() -> JinaEmbedder:
    return JinaEmbedder.load(RagSettings(EMBED_DEVICE="cpu", EMBED_BATCH=4))


async def test_vectors_are_normalized_and_have_embed_dim(jina):
    [vec] = await jina.embed_queries(["Что такое дифракционный оптический элемент?"])
    assert len(vec) == jina.dim == 1024
    assert np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-4)


async def test_query_is_closer_to_its_passage(jina):
    [query] = await jina.embed_queries(["Как формируются вихревые лазерные пучки?"])
    vortex, cnn = await jina.embed_passages(
        [
            "Пучки с орбитальным угловым моментом формируются спиральной фазовой пластинкой.",
            "Свёрточная нейронная сеть классифицирует гиперспектральные изображения.",
        ]
    )
    assert np.dot(query, vortex) > np.dot(query, cnn) + 0.2


def test_chunker_with_model_tokenizer(jina):
    text = "\n\n".join(["Дифракционный оптический элемент фокусирует излучение. " * 40] * 3)
    chunks = MarkdownChunker(jina, chunk_tokens=128, overlap=16, min_tokens=20).split(text)
    assert len(chunks) > 3
    assert all(c.token_count <= 128 + 20 for c in chunks)
