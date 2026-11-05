"""Чанкер: Markdown одной страницы → фрагменты по CHUNK_TOKENS токенов с перекрытием.

Правила:
- текст делится на блоки: абзацы (по пустой строке), формулы $$…$$ и таблицы (строки «|…»);
- блоки набираются в чанк, пока он не превысит лимит; формулу и таблицу, которые помещаются
  в лимит, не разрезаем;
- блок длиннее лимита режется окнами по токенам с шагом CHUNK_TOKENS − CHUNK_OVERLAP;
- следующий чанк начинается с хвоста предыдущего (до CHUNK_OVERLAP токенов, с границы слова);
- короткий последний чанк (новых токенов < CHUNK_MIN_TOKENS) склеивается с предыдущим;
- чанкер получает одну страницу, поэтому чанк не выходит за её пределы.
"""

from dataclasses import dataclass, field

from corag_rag.embedder import TokenCounter

SEPARATOR = "\n\n"


@dataclass(frozen=True, slots=True)
class ChunkText:
    text: str
    token_count: int


@dataclass(frozen=True, slots=True)
class _Piece:
    text: str
    tokens: int
    atomic: bool = False  # формула или таблица
    continued: bool = False  # продолжение окна длинного блока (перекрытие уже внутри)


@dataclass(slots=True)
class _Draft:
    pieces: list[_Piece] = field(default_factory=list)
    tokens: int = 0
    overlap: _Piece | None = None  # хвост предыдущего чанка в начале этого

    def add(self, piece: _Piece) -> None:
        self.pieces.append(piece)
        self.tokens += piece.tokens

    @property
    def new_tokens(self) -> int:
        return self.tokens - (self.overlap.tokens if self.overlap else 0)


def split_blocks(text: str) -> list[tuple[str, bool]]:
    """Markdown → блоки (текст, атомарный ли). Формула может содержать пустые строки."""
    blocks: list[tuple[str, bool]] = []
    para: list[str] = []
    lines = text.replace("\r\n", "\n").split("\n")

    def end_para() -> None:
        if para:
            blocks.append(("\n".join(para).strip(), False))
            para.clear()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if stripped.startswith("$$"):
            end_para()
            formula = [line]
            # $$…$$ в одной строке или закрывающие $$ на одной из следующих
            closed = stripped.count("$$") >= 2
            while not closed and i + 1 < len(lines):
                i += 1
                formula.append(lines[i])
                closed = "$$" in lines[i]
            blocks.append(("\n".join(formula).strip(), True))
        elif stripped.startswith("|"):
            end_para()
            table = [line]
            while i + 1 < len(lines) and lines[i + 1].strip().startswith("|"):
                i += 1
                table.append(lines[i])
            blocks.append(("\n".join(table).strip(), True))
        elif not stripped:
            end_para()
        else:
            para.append(line)
        i += 1
    end_para()
    return [(t, atomic) for t, atomic in blocks if t]


class MarkdownChunker:
    def __init__(
        self,
        tokens: TokenCounter,
        chunk_tokens: int = 512,
        overlap: int = 64,
        min_tokens: int = 50,
    ) -> None:
        if not 0 <= overlap < chunk_tokens:
            raise ValueError("нужно 0 <= overlap < chunk_tokens")
        self.tokens = tokens
        self.chunk_tokens = chunk_tokens
        self.overlap = overlap
        self.min_tokens = min_tokens

    def split(self, text_md: str | None) -> list[ChunkText]:
        if not text_md or not text_md.strip():
            return []
        drafts: list[_Draft] = []
        current = _Draft()
        for piece in self._pieces(text_md):
            if current.pieces and current.tokens + piece.tokens > self.chunk_tokens:
                drafts.append(current)
                current = _Draft()
                tail = None if piece.continued else self._tail(drafts[-1].pieces[-1])
                if tail and tail.tokens + piece.tokens <= self.chunk_tokens:
                    current.overlap = tail
                    current.add(tail)
            current.add(piece)
        drafts.append(current)

        # Короткий хвост страницы — к предыдущему чанку (без дублирования перекрытия)
        if len(drafts) > 1 and drafts[-1].new_tokens < self.min_tokens:
            last = drafts.pop()
            for piece in last.pieces[1:] if last.overlap else last.pieces:
                drafts[-1].add(piece)

        chunks = []
        for draft in drafts:
            text = SEPARATOR.join(p.text for p in draft.pieces)
            chunks.append(ChunkText(text=text, token_count=len(self.tokens.spans(text))))
        return chunks

    def _pieces(self, text: str) -> list[_Piece]:
        """Блоки страницы; блоки длиннее лимита — окнами с перекрытием."""
        pieces: list[_Piece] = []
        for block, atomic in split_blocks(text):
            spans = self.tokens.spans(block)
            if len(spans) <= self.chunk_tokens:
                pieces.append(_Piece(block, len(spans), atomic))
                continue
            step = self.chunk_tokens - self.overlap
            for n, start in enumerate(range(0, len(spans), step)):
                window = spans[start : start + self.chunk_tokens]
                begin, end = window[0][0], max(e for _, e in window)
                pieces.append(_Piece(block[begin:end].strip(), len(window), atomic, n > 0))
                if start + self.chunk_tokens >= len(spans):
                    break
        return pieces

    def _tail(self, piece: _Piece) -> _Piece | None:
        """Хвост блока для перекрытия: до overlap токенов, начиная с границы слова."""
        if self.overlap == 0:
            return None
        if piece.atomic:
            # Формулу или таблицу не режем: целиком, если помещается, иначе без перекрытия
            return piece if piece.tokens <= self.overlap else None
        spans = self.tokens.spans(piece.text)
        if len(spans) <= self.overlap:
            return piece
        start = spans[-self.overlap][0]
        # Начало посреди слова (подслово токенизатора) — сдвигаемся к следующему пробелу
        if start > 0 and not piece.text[start - 1].isspace():
            nxt = next((i for i in range(start, len(piece.text)) if piece.text[i].isspace()), None)
            if nxt is None:
                return None
            start = nxt
        tail = piece.text[start:].strip()
        if not tail:
            return None
        return _Piece(tail, len(self.tokens.spans(tail)))
