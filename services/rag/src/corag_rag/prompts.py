"""Промпт для LLM: системная инструкция и контекст из пронумерованных фрагментов [1]..[k]."""

from collections.abc import Sequence

from corag_rag.retriever import Hit

SYSTEM_PROMPT = (
    "Ты — ассистент по архиву научного журнала «Компьютерная оптика».\n"
    "Отвечай на русском языке только по приведённым фрагментам статей, "
    "не используй другие знания.\n"
    "После каждого утверждения ставь ссылку на фрагмент в квадратных скобках: [1], [2][3].\n"
    "Если во фрагментах нет ответа на вопрос, так и скажи: "
    "«Во фрагментах архива нет ответа на этот вопрос» — и ничего не придумывай.\n"
    "Отвечай кратко, 2–6 предложений."
)

# Ответ без вызова LLM, когда ни один фрагмент не прошёл порог MIN_SCORE (found=false)
NOT_FOUND_ANSWER = (
    "В архиве не найдено фрагментов, относящихся к вопросу. "
    "Попробуйте переформулировать вопрос или изменить фильтр по годам."
)


def source_label(hit: Hit) -> str:
    """Подпись фрагмента: «статья «…», авторы, 1998, № 1, с. 2»."""
    chunk = hit.chunk
    where = f"{chunk.year}, № {chunk.number}, с. {chunk.page_number}"
    if chunk.article_title is None:
        return f"вне размеченных статей, {where}"
    authors = f", {', '.join(hit.authors)}" if hit.authors else ""
    return f"статья «{chunk.article_title}»{authors}, {where}"


def build_context(hits: Sequence[Hit]) -> str:
    return "\n\n".join(
        f"[{n}] ({source_label(hit)})\n{hit.chunk.text}" for n, hit in enumerate(hits, start=1)
    )


def build_messages(question: str, hits: Sequence[Hit]) -> list[dict[str, str]]:
    """Сообщения chat/completions: инструкция + фрагменты + вопрос."""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Фрагменты:\n\n{build_context(hits)}\n\nВопрос: {question}"},
    ]
