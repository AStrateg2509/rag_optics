"""DTO вопросов к архиву (RAG): запросы /rag/ask и /rag/search, ответы с источниками."""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from corag_api.schemas.common import NonEmptyStr

Year = Annotated[int, Field(ge=1987, le=2100, description="Год выпуска (включительно)")]
TopK = Annotated[int, Field(ge=1, le=20, description="Сколько фрагментов искать (по умолчанию 5)")]


class _RagQuery(BaseModel):
    top_k: TopK | None = None
    year_from: Year | None = None
    year_to: Year | None = None

    @model_validator(mode="after")
    def _years_order(self) -> Self:
        years = (self.year_from, self.year_to)
        if None not in years and years[0] > years[1]:
            raise ValueError("year_from больше year_to")
        return self


class AskIn(_RagQuery):
    question: Annotated[
        NonEmptyStr,
        Field(max_length=2000, examples=["Какие методы расчёта ДОЭ предлагались в 1990-х?"]),
    ]


class SearchIn(_RagQuery):
    query: Annotated[NonEmptyStr, Field(max_length=2000, examples=["вихревые лазерные пучки"])]


class CitationOut(BaseModel):
    """Источник: фрагмент страницы выпуска и статья, если страница размечена."""

    model_config = ConfigDict(from_attributes=True)

    chunk_id: int
    article_id: int | None = Field(description="Пусто, если страница вне размеченных статей")
    article_title: str | None
    authors: list[str] = Field(description="Авторы статьи по порядку")
    issue_id: int = Field(description="Страница: GET /issues/{issue_id}/pages/{page_number}")
    year: int
    issue_number: int
    page_number: int
    snippet: str
    score: float = Field(description="Косинусное сходство с вопросом (1 — совпадение)")


class AskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    answer: str
    found: bool = Field(
        description="false — в архиве нет релевантных фрагментов, LLM не вызывалась"
    )
    citations: list[CitationOut] = Field(description="Источники по порядку ссылок [1]..[k]")


class SearchOut(BaseModel):
    results: list[CitationOut]


class IndexOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    issue_id: int
    pages_indexed: int
    chunks_indexed: int
