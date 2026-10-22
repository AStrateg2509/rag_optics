"""DTO статей. Авторы задаются списком id: порядок в списке = author_order."""

from collections.abc import Sequence
from datetime import datetime
from typing import Annotated, Any, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from corag_api.schemas.author import AuthorShort
from corag_api.schemas.common import NonEmptyStr, PatchModel
from corag_api.schemas.issue import IssueShort
from corag_api.schemas.page import PageOut
from corag_db.models import Article, Page

PageNumber = Annotated[int, Field(ge=1)]
Language = Annotated[str, Field(pattern=r"^[a-z]{2}$")]
Doi = Annotated[NonEmptyStr, Field(max_length=100, examples=["10.18287/2412-6179-CO-1234"])]
Udc = Annotated[NonEmptyStr, Field(max_length=50)]
KeywordValue = Annotated[NonEmptyStr, Field(max_length=100)]


def _unique_ids(ids: list[int] | None) -> list[int] | None:
    if ids is not None and len(set(ids)) != len(ids):
        raise ValueError("author_ids содержит повторы")
    return ids


class ArticleCreate(BaseModel):
    issue_id: int
    title: NonEmptyStr
    page_start: PageNumber
    page_end: PageNumber
    author_ids: list[int] = Field(default_factory=list, description="Порядок = порядок авторов")
    keywords: list[KeywordValue] = Field(default_factory=list)
    abstract: str | None = None
    doi: Doi | None = None
    language: Language = "ru"
    udc: Udc | None = None

    _check_ids = field_validator("author_ids")(_unique_ids)

    @model_validator(mode="after")
    def _page_range(self) -> Self:
        if self.page_start > self.page_end:
            raise ValueError("page_start больше page_end")
        return self


class ArticleUpdate(PatchModel):
    """Частичное изменение; author_ids и keywords, если переданы, заменяют весь набор."""

    nullable = frozenset({"abstract", "doi", "udc"})

    title: NonEmptyStr | None = None
    page_start: PageNumber | None = None
    page_end: PageNumber | None = None
    author_ids: list[int] | None = None
    keywords: list[KeywordValue] | None = None
    abstract: str | None = None
    doi: Doi | None = None
    language: Language | None = None
    udc: Udc | None = None

    _check_ids = field_validator("author_ids")(_unique_ids)


class ArticleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    issue_id: int
    title: str
    abstract: str | None
    doi: str | None
    language: str
    page_start: int
    page_end: int
    udc: str | None
    created_at: datetime
    issue: IssueShort
    authors: list[AuthorShort]  # в порядке author_order (свойство Article.authors)
    keywords: list[str]

    @field_validator("keywords", mode="before")
    @classmethod
    def _keyword_values(cls, value: Any) -> Any:
        # ORM отдаёт объекты Keyword, наружу — только строки
        return [getattr(k, "value", k) for k in value]


class ArticleDetailOut(ArticleOut):
    """Карточка статьи с текстом её страниц (диапазон page_start..page_end)."""

    pages: list[PageOut]

    @classmethod
    def build(cls, article: Article, pages: Sequence[Page]) -> Self:
        return cls(
            **ArticleOut.model_validate(article).model_dump(),
            pages=[PageOut.model_validate(p) for p in pages],
        )
