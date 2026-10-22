"""Внедрение зависимостей (Depends): сессия, репозитории, сервисы, публикатор."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from corag_api.publishers import JobPublisher
from corag_api.services.article_service import ArticleService
from corag_api.services.auth_service import AuthService
from corag_api.services.author_service import AuthorService
from corag_api.services.issue_service import IssueService
from corag_api.services.keyword_service import KeywordService
from corag_api.services.ocr_service import OcrService
from corag_api.services.user_service import UserService
from corag_api.settings import ApiSettings, get_settings
from corag_db.repositories import (
    ArticleRepository,
    AuthorRepository,
    IssueRepository,
    KeywordRepository,
    OcrJobRepository,
    PageRepository,
    UserRepository,
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Одна сессия = одна транзакция на запрос: commit при успехе, rollback при исключении."""
    async with request.app.state.sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise


# scope="function": код после yield (commit) выполняется до отправки ответа.
# По умолчанию FastAPI завершает yield-зависимость после ответа, и клиент получил бы 201
# раньше, чем данные зафиксированы (а ошибку commit уже не увидел бы)
SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[ApiSettings, Depends(get_settings)]


@dataclass(frozen=True, slots=True)
class Pagination:
    limit: int
    offset: int


def get_pagination(
    limit: Annotated[int, Query(ge=1, le=100, description="Размер страницы")] = 20,
    offset: Annotated[int, Query(ge=0, description="Сколько элементов пропустить")] = 0,
) -> Pagination:
    return Pagination(limit, offset)


PaginationDep = Annotated[Pagination, Depends(get_pagination)]


# --- Репозитории: все получают одну и ту же сессию запроса (Depends кеширует get_session) ---


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


def get_issue_repository(session: SessionDep) -> IssueRepository:
    return IssueRepository(session)


def get_page_repository(session: SessionDep) -> PageRepository:
    return PageRepository(session)


def get_article_repository(session: SessionDep) -> ArticleRepository:
    return ArticleRepository(session)


def get_author_repository(session: SessionDep) -> AuthorRepository:
    return AuthorRepository(session)


def get_keyword_repository(session: SessionDep) -> KeywordRepository:
    return KeywordRepository(session)


def get_ocr_job_repository(session: SessionDep) -> OcrJobRepository:
    return OcrJobRepository(session)


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
IssueRepoDep = Annotated[IssueRepository, Depends(get_issue_repository)]
PageRepoDep = Annotated[PageRepository, Depends(get_page_repository)]
ArticleRepoDep = Annotated[ArticleRepository, Depends(get_article_repository)]
AuthorRepoDep = Annotated[AuthorRepository, Depends(get_author_repository)]
KeywordRepoDep = Annotated[KeywordRepository, Depends(get_keyword_repository)]
OcrJobRepoDep = Annotated[OcrJobRepository, Depends(get_ocr_job_repository)]


# --- Публикатор задач: создаётся в create_app, в тестах подменяется ---


def get_publisher(request: Request) -> JobPublisher:
    return request.app.state.publisher


PublisherDep = Annotated[JobPublisher, Depends(get_publisher)]


# --- Сервисы ---


def get_auth_service(users: UserRepoDep, settings: SettingsDep) -> AuthService:
    return AuthService(users, settings)


def get_user_service(users: UserRepoDep) -> UserService:
    return UserService(users)


def get_issue_service(
    issues: IssueRepoDep, pages: PageRepoDep, settings: SettingsDep
) -> IssueService:
    return IssueService(issues, pages, settings)


def get_article_service(articles: ArticleRepoDep, issues: IssueRepoDep) -> ArticleService:
    return ArticleService(articles, issues)


def get_author_service(authors: AuthorRepoDep) -> AuthorService:
    return AuthorService(authors)


def get_keyword_service(keywords: KeywordRepoDep) -> KeywordService:
    return KeywordService(keywords)


def get_ocr_service(
    session: SessionDep, issues: IssueRepoDep, jobs: OcrJobRepoDep, publisher: PublisherDep
) -> OcrService:
    return OcrService(session, issues, jobs, publisher)


AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
UserServiceDep = Annotated[UserService, Depends(get_user_service)]
IssueServiceDep = Annotated[IssueService, Depends(get_issue_service)]
ArticleServiceDep = Annotated[ArticleService, Depends(get_article_service)]
AuthorServiceDep = Annotated[AuthorService, Depends(get_author_service)]
KeywordServiceDep = Annotated[KeywordService, Depends(get_keyword_service)]
OcrServiceDep = Annotated[OcrService, Depends(get_ocr_service)]
