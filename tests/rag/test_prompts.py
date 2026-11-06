"""Сборка промпта: нумерация фрагментов [1]..[k] и подписи источников."""

from corag_db.repositories import ChunkHit
from corag_rag.prompts import SYSTEM_PROMPT, build_messages, source_label
from corag_rag.retriever import Hit


def hit(chunk_id: int, text: str, title: str | None, authors=(), page=2) -> Hit:
    chunk = ChunkHit(
        chunk_id=chunk_id,
        text=text,
        page_number=page,
        issue_id=7,
        year=1998,
        number=1,
        article_id=chunk_id if title else None,
        article_title=title,
        score=0.8,
    )
    return Hit(chunk, tuple(authors))


def test_source_label_with_article_and_authors():
    h = hit(1, "…", "Фокусировка в кольцо", ["Иванов И. И.", "Петров П. П."])
    assert source_label(h) == (
        "статья «Фокусировка в кольцо», Иванов И. И., Петров П. П., 1998, № 1, с. 2"
    )


def test_source_label_outside_articles():
    assert source_label(hit(2, "…", None, page=7)) == "вне размеченных статей, 1998, № 1, с. 7"


def test_messages_number_fragments_in_order():
    hits = [hit(10, "Первый фрагмент.", "А"), hit(11, "Второй фрагмент.", None, page=3)]
    system, user = build_messages("Что такое ДОЭ?", hits)
    assert system == {"role": "system", "content": SYSTEM_PROMPT}
    assert "[n]" not in SYSTEM_PROMPT and "[1]" in SYSTEM_PROMPT
    content = user["content"]
    assert user["role"] == "user"
    assert content.index("[1] (статья «А»") < content.index("Первый фрагмент.")
    assert content.index("Первый фрагмент.") < content.index("[2] (вне размеченных статей")
    assert content.index("Второй фрагмент.") < content.index("Вопрос: Что такое ДОЭ?")
    assert content.endswith("Вопрос: Что такое ДОЭ?")
