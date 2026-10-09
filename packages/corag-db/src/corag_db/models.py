"""ORM-модели CO-RAG: 8 сущностей, 10 таблиц (DDL — раздел 5.2 ТЗ)."""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from corag_db.base import Base
from corag_db.enums import IssueStatus, JobStatus, UserRole, pg_enum
from corag_db.settings import get_settings

EMBED_DIM = get_settings().EMBED_DIM


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))  # bcrypt
    full_name: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(
        pg_enum(UserRole, "user_role"), server_default=UserRole.RESEARCHER.value
    )
    is_active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return f"User(id={self.id}, email={self.email!r}, role={self.role})"


class Issue(Base):
    """Выпуск журнала."""

    __tablename__ = "issues"
    __table_args__ = (
        UniqueConstraint("year", "number"),
        CheckConstraint("year >= 1987", name="year_min"),
        CheckConstraint("page_count > 0", name="page_count_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger)
    volume: Mapped[int | None] = mapped_column(SmallInteger)  # у ранних выпусков может не быть
    number: Mapped[int] = mapped_column(SmallInteger)
    scan_path: Mapped[str | None] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[IssueStatus] = mapped_column(
        pg_enum(IssueStatus, "issue_status"), server_default=IssueStatus.CREATED.value
    )
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    creator: Mapped[User] = relationship()
    # Удаление каскадом выполняет БД (ON DELETE CASCADE), ORM не грузит дочерние строки
    pages: Mapped[list["Page"]] = relationship(
        back_populates="issue", passive_deletes=True, order_by="Page.page_number"
    )
    articles: Mapped[list["Article"]] = relationship(
        back_populates="issue", passive_deletes=True, order_by="Article.page_start"
    )
    ocr_jobs: Mapped[list["OcrJob"]] = relationship(back_populates="issue", passive_deletes=True)

    def __repr__(self) -> str:
        return f"Issue(id={self.id}, {self.year} №{self.number}, status={self.status})"


class Page(Base):
    """Страница выпуска и её текст в Markdown после OCR."""

    __tablename__ = "pages"
    __table_args__ = (
        UniqueConstraint("issue_id", "page_number"),
        CheckConstraint("page_number > 0", name="page_number_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    page_number: Mapped[int] = mapped_column(Integer)
    text_md: Mapped[str | None] = mapped_column(Text)
    ocr_model: Mapped[str | None] = mapped_column(String(100))
    recognized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issue: Mapped[Issue] = relationship(back_populates="pages")
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="page", passive_deletes=True, order_by="Chunk.chunk_index"
    )


# M:N статья ↔ ключевое слово: обычная таблица-связка без своих полей
article_keywords = Table(
    "article_keywords",
    Base.metadata,
    Column(
        "article_id", BigInteger, ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    ),
    Column(
        "keyword_id", BigInteger, ForeignKey("keywords.id", ondelete="CASCADE"), primary_key=True
    ),
)


class Article(Base):
    """Статья. Со страницами связана диапазоном page_start..page_end внутри выпуска."""

    __tablename__ = "articles"
    __table_args__ = (
        CheckConstraint("page_start <= page_end", name="page_range"),
        Index("ix_articles_issue_pages", "issue_id", "page_start", "page_end"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(String(100), unique=True)
    language: Mapped[str] = mapped_column(CHAR(2), server_default="ru")
    page_start: Mapped[int] = mapped_column(Integer)
    page_end: Mapped[int] = mapped_column(Integer)
    udc: Mapped[str | None] = mapped_column(String(50))  # УДК статьи (миграция 0002)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    issue: Mapped[Issue] = relationship(back_populates="articles")
    # Association object: у связи есть своё поле author_order
    authors_assoc: Mapped[list["ArticleAuthor"]] = relationship(
        back_populates="article",
        order_by="ArticleAuthor.author_order",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    keywords: Mapped[list["Keyword"]] = relationship(
        secondary=article_keywords, back_populates="articles", order_by="Keyword.value"
    )

    @property
    def authors(self) -> list["Author"]:
        """Авторы в порядке author_order."""
        return [a.author for a in self.authors_assoc]

    def set_authors(self, authors: list["Author"]) -> None:
        """Задать авторов; порядок в списке становится author_order (с 1).

        Существующие связи обновляются на месте, чтобы не было DELETE+INSERT с тем же PK.
        """
        current = {a.author_id: a for a in self.authors_assoc if a.author_id is not None}
        new_assoc = []
        for order, author in enumerate(authors, start=1):
            assoc = current.get(author.id) or ArticleAuthor(author=author)
            assoc.author_order = order
            new_assoc.append(assoc)
        self.authors_assoc = new_assoc

    def __repr__(self) -> str:
        return f"Article(id={self.id}, title={self.title[:40]!r})"


class Author(Base):
    __tablename__ = "authors"
    __table_args__ = (Index("ix_authors_full_name", "full_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    affiliation: Mapped[str | None] = mapped_column(String(500))
    orcid: Mapped[str | None] = mapped_column(CHAR(19), unique=True)

    # passive_deletes="all": ORM не обнуляет author_id у связок, удаление решает БД (RESTRICT)
    articles_assoc: Mapped[list["ArticleAuthor"]] = relationship(
        back_populates="author", passive_deletes="all"
    )
    # Обратная сторона M:N только для чтения: изменяем связь через Article.set_authors
    articles: Mapped[list[Article]] = relationship(
        secondary="article_authors", viewonly=True, order_by=Article.id
    )

    def __repr__(self) -> str:
        return f"Author(id={self.id}, {self.full_name!r})"


class ArticleAuthor(Base):
    """Связка M:N статья ↔ автор с порядком автора."""

    __tablename__ = "article_authors"
    __table_args__ = (CheckConstraint("author_order > 0", name="author_order_positive"),)

    article_id: Mapped[int] = mapped_column(
        ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    author_id: Mapped[int] = mapped_column(
        ForeignKey("authors.id", ondelete="RESTRICT"), primary_key=True
    )
    author_order: Mapped[int] = mapped_column(SmallInteger)

    article: Mapped[Article] = relationship(back_populates="authors_assoc")
    author: Mapped[Author] = relationship(back_populates="articles_assoc")


class Keyword(Base):
    __tablename__ = "keywords"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    value: Mapped[str] = mapped_column(String(100), unique=True)

    articles: Mapped[list[Article]] = relationship(
        secondary=article_keywords, back_populates="keywords", order_by=Article.id
    )

    def __repr__(self) -> str:
        return f"Keyword({self.value!r})"


class Chunk(Base):
    """Фрагмент текста страницы и его эмбеддинг."""

    __tablename__ = "chunks"
    __table_args__ = (
        UniqueConstraint("page_id", "chunk_index"),
        # Приближённый поиск ближайших соседей по косинусному расстоянию
        Index(
            "ix_chunks_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    page_id: Mapped[int] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    chunk_index: Mapped[int] = mapped_column(SmallInteger)
    text: Mapped[str] = mapped_column(Text)
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBED_DIM))

    page: Mapped[Page] = relationship(back_populates="chunks")


class OcrJob(Base):
    """Задача распознавания выпуска."""

    __tablename__ = "ocr_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    issue_id: Mapped[int] = mapped_column(ForeignKey("issues.id", ondelete="CASCADE"))
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    status: Mapped[JobStatus] = mapped_column(
        pg_enum(JobStatus, "job_status"), server_default=JobStatus.QUEUED.value
    )
    pages_total: Mapped[int | None] = mapped_column(Integer)
    pages_done: Mapped[int] = mapped_column(Integer, server_default="0")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    issue: Mapped[Issue] = relationship(back_populates="ocr_jobs")

    def __repr__(self) -> str:
        return f"OcrJob(id={self.id}, issue_id={self.issue_id}, status={self.status})"


# Не более одной активной задачи на выпуск (иначе API отвечает 409)
Index(
    "ux_ocr_jobs_active",
    OcrJob.issue_id,
    unique=True,
    postgresql_where=OcrJob.status.in_([JobStatus.QUEUED, JobStatus.RUNNING]),
)
