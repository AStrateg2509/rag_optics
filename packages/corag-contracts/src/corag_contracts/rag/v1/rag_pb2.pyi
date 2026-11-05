from google.protobuf.internal import containers as _containers
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class Filters(_message.Message):
    __slots__ = ("year_from", "year_to")
    YEAR_FROM_FIELD_NUMBER: _ClassVar[int]
    YEAR_TO_FIELD_NUMBER: _ClassVar[int]
    year_from: int
    year_to: int
    def __init__(self, year_from: _Optional[int] = ..., year_to: _Optional[int] = ...) -> None: ...

class AskRequest(_message.Message):
    __slots__ = ("question", "top_k", "filters")
    QUESTION_FIELD_NUMBER: _ClassVar[int]
    TOP_K_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    question: str
    top_k: int
    filters: Filters
    def __init__(self, question: _Optional[str] = ..., top_k: _Optional[int] = ..., filters: _Optional[_Union[Filters, _Mapping]] = ...) -> None: ...

class Citation(_message.Message):
    __slots__ = ("chunk_id", "article_id", "article_title", "year", "issue_number", "page_number", "snippet", "score", "issue_id", "authors")
    CHUNK_ID_FIELD_NUMBER: _ClassVar[int]
    ARTICLE_ID_FIELD_NUMBER: _ClassVar[int]
    ARTICLE_TITLE_FIELD_NUMBER: _ClassVar[int]
    YEAR_FIELD_NUMBER: _ClassVar[int]
    ISSUE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    PAGE_NUMBER_FIELD_NUMBER: _ClassVar[int]
    SNIPPET_FIELD_NUMBER: _ClassVar[int]
    SCORE_FIELD_NUMBER: _ClassVar[int]
    ISSUE_ID_FIELD_NUMBER: _ClassVar[int]
    AUTHORS_FIELD_NUMBER: _ClassVar[int]
    chunk_id: int
    article_id: int
    article_title: str
    year: int
    issue_number: int
    page_number: int
    snippet: str
    score: float
    issue_id: int
    authors: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, chunk_id: _Optional[int] = ..., article_id: _Optional[int] = ..., article_title: _Optional[str] = ..., year: _Optional[int] = ..., issue_number: _Optional[int] = ..., page_number: _Optional[int] = ..., snippet: _Optional[str] = ..., score: _Optional[float] = ..., issue_id: _Optional[int] = ..., authors: _Optional[_Iterable[str]] = ...) -> None: ...

class AskResponse(_message.Message):
    __slots__ = ("answer", "citations", "found")
    ANSWER_FIELD_NUMBER: _ClassVar[int]
    CITATIONS_FIELD_NUMBER: _ClassVar[int]
    FOUND_FIELD_NUMBER: _ClassVar[int]
    answer: str
    citations: _containers.RepeatedCompositeFieldContainer[Citation]
    found: bool
    def __init__(self, answer: _Optional[str] = ..., citations: _Optional[_Iterable[_Union[Citation, _Mapping]]] = ..., found: _Optional[bool] = ...) -> None: ...

class SearchRequest(_message.Message):
    __slots__ = ("query", "top_k", "filters")
    QUERY_FIELD_NUMBER: _ClassVar[int]
    TOP_K_FIELD_NUMBER: _ClassVar[int]
    FILTERS_FIELD_NUMBER: _ClassVar[int]
    query: str
    top_k: int
    filters: Filters
    def __init__(self, query: _Optional[str] = ..., top_k: _Optional[int] = ..., filters: _Optional[_Union[Filters, _Mapping]] = ...) -> None: ...

class SearchResponse(_message.Message):
    __slots__ = ("results",)
    RESULTS_FIELD_NUMBER: _ClassVar[int]
    results: _containers.RepeatedCompositeFieldContainer[Citation]
    def __init__(self, results: _Optional[_Iterable[_Union[Citation, _Mapping]]] = ...) -> None: ...

class IndexIssueRequest(_message.Message):
    __slots__ = ("issue_id",)
    ISSUE_ID_FIELD_NUMBER: _ClassVar[int]
    issue_id: int
    def __init__(self, issue_id: _Optional[int] = ...) -> None: ...

class IndexIssueResponse(_message.Message):
    __slots__ = ("pages_indexed", "chunks_indexed")
    PAGES_INDEXED_FIELD_NUMBER: _ClassVar[int]
    CHUNKS_INDEXED_FIELD_NUMBER: _ClassVar[int]
    pages_indexed: int
    chunks_indexed: int
    def __init__(self, pages_indexed: _Optional[int] = ..., chunks_indexed: _Optional[int] = ...) -> None: ...
