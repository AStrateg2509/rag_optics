"""MarkdownChunker: лимит, перекрытие, формулы и таблицы, окна длинных блоков, короткий хвост."""

import pytest

from corag_rag.chunker import MarkdownChunker, split_blocks


def words(prefix: str, n: int) -> str:
    return " ".join(f"{prefix}{i}" for i in range(n))


@pytest.fixture
def chunker(tokens) -> MarkdownChunker:
    return MarkdownChunker(tokens, chunk_tokens=100, overlap=10, min_tokens=20)


def test_empty_page(chunker):
    assert chunker.split("") == []
    assert chunker.split("  \n\n ") == []
    assert chunker.split(None) == []


def test_short_page_is_one_chunk(chunker):
    text = "# Заголовок\n\nКороткий абзац текста страницы."
    [chunk] = chunker.split(text)
    assert chunk.text == text
    assert chunk.token_count == 6


def test_chunks_respect_limit_and_overlap(chunker):
    paras = [words(f"p{n}w", 30) for n in range(10)]
    chunks = chunker.split("\n\n".join(paras))
    assert len(chunks) > 1
    assert all(c.token_count <= 100 for c in chunks)
    # Каждый следующий чанк начинается с последних слов предыдущего (перекрытие ≤ 10 токенов)
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        head = nxt.text.split("\n\n")[0].split()
        assert 0 < len(head) <= 10
        assert prev.text.split()[-len(head) :] == head
    # Ни один абзац не потерян
    joined = "\n\n".join(c.text for c in chunks)
    assert all(p in joined for p in paras)


def test_formula_is_not_split(chunker):
    formula = "$$\n" + words("f", 40) + "\n\n" + words("g", 5) + "\n$$"
    text = "\n\n".join([words("a", 70), formula, words("b", 20)])
    chunks = chunker.split(text)
    assert any(formula in c.text for c in chunks)
    for c in chunks:
        assert c.text.count("$$") % 2 == 0


def test_formula_with_blank_lines_is_one_block():
    blocks = split_blocks("До формулы\n$$\na = b\n\nc = d\n$$\nПосле")
    assert blocks == [("До формулы", False), ("$$\na = b\n\nc = d\n$$", True), ("После", False)]


def test_table_is_not_split(chunker):
    table = "\n".join(f"| {words(f'r{i}c', 8)} |" for i in range(5))
    text = "\n\n".join([words("a", 60), table, words("b", 30)])
    chunks = chunker.split(text)
    assert any(table in c.text for c in chunks)
    # Таблица не попадает в перекрытие по частям
    for c in chunks:
        if "| r0c0" in c.text:
            assert table in c.text


def test_long_block_is_cut_into_windows(chunker):
    text = words("w", 250)
    chunks = chunker.split(text)
    # Окна с шагом 100 − 10 = 90: [0, 100), [90, 190), [180, 250)
    assert [c.token_count for c in chunks] == [100, 100, 70]
    first, second, third = (c.text.split() for c in chunks)
    assert first[-10:] == second[:10]
    assert second[-10:] == third[:10]
    assert third[-1] == "w249"


def test_short_tail_is_merged(tokens, chunker):
    text = "\n\n".join([words("a", 60), words("b", 35), words("c", 8)])
    # Без склейки: второй чанк — перекрытие (10 токенов) + 8 новых
    plain = MarkdownChunker(tokens, chunk_tokens=100, overlap=10, min_tokens=0).split(text)
    assert [c.token_count for c in plain] == [95, 18]
    # 8 новых токенов < min_tokens=20 → хвост приклеен, перекрытие не дублируется
    [chunk] = chunker.split(text)
    assert chunk.token_count == 103
    assert chunk.text.endswith(words("c", 8))
    assert chunk.text.count("b34") == 1


def test_overlap_validation(tokens):
    with pytest.raises(ValueError):
        MarkdownChunker(tokens, chunk_tokens=64, overlap=64)
