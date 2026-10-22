"""Выпуски: CRUD, загрузка PDF-скана, страницы."""

import logging
from collections.abc import Sequence

import anyio
import pymupdf
from fastapi import UploadFile

from corag_api.errors import PayloadTooLarge, ValidationFailed
from corag_api.schemas.issue import IssueCreate, IssueUpdate
from corag_api.settings import ApiSettings
from corag_db.enums import IssueStatus
from corag_db.exceptions import Conflict, NotFound
from corag_db.models import Issue, Page, User
from corag_db.repositories import IssueRepository, IssueWithCounts, PageRepository

log = logging.getLogger("corag_api")

CHUNK_SIZE = 1024 * 1024  # запись скана на диск порциями по 1 МиБ
PDF_SIGNATURE = b"%PDF-"


def count_pdf_pages(path: str) -> int:
    """Число страниц PDF (PyMuPDF). Битый, зашифрованный или пустой файл → ValidationFailed."""
    try:
        with pymupdf.open(path, filetype="pdf") as doc:
            if doc.needs_pass:
                raise ValidationFailed("PDF защищён паролем")
            pages = doc.page_count
    except RuntimeError as exc:  # FileDataError и прочие ошибки разбора MuPDF
        raise ValidationFailed(f"PDF не читается: {exc}") from exc
    if pages < 1:
        raise ValidationFailed("в PDF нет страниц")
    return pages


class IssueService:
    def __init__(self, issues: IssueRepository, pages: PageRepository, settings: ApiSettings):
        self.issues = issues
        self.pages = pages
        self.settings = settings

    async def list(self, year: int | None, limit: int, offset: int) -> tuple[Sequence[Issue], int]:
        return await self.issues.list(limit, offset, year=year), await self.issues.count(year=year)

    async def get(self, issue_id: int) -> IssueWithCounts:
        return await self.issues.get_with_counts(issue_id)

    async def create(self, data: IssueCreate, user: User) -> Issue:
        await self._ensure_unique(data.year, data.number)
        issue = await self.issues.add(Issue(**data.model_dump(), created_by=user.id))
        log.info("[api] issue_id=%d создан: %d №%d", issue.id, issue.year, issue.number)
        return issue

    async def update(self, issue_id: int, data: IssueUpdate) -> Issue:
        issue = await self.issues.get_or_raise(issue_id)
        changes = data.changes()
        year, number = changes.get("year", issue.year), changes.get("number", issue.number)
        if (year, number) != (issue.year, issue.number):
            await self._ensure_unique(year, number)
        return await self.issues.update(issue, **changes)

    async def delete(self, issue_id: int) -> None:
        """Удаление выпуска; страницы, статьи, чанки и задачи удаляет БД каскадом."""
        issue = await self.issues.get_or_raise(issue_id)
        if issue.status is IssueStatus.OCR_IN_PROGRESS:
            raise Conflict(f"выпуск {issue_id}: идёт OCR, удалять нельзя")
        scan = issue.scan_path
        await self.issues.delete(issue)
        if scan:
            await anyio.Path(self.settings.SCANS_DIR / scan).unlink(missing_ok=True)
        log.info("[api] issue_id=%d удалён (скан: %s)", issue_id, scan or "—")

    async def upload_scan(self, issue_id: int, file: UploadFile) -> Issue:
        """Сохранить PDF-скан выпуска и посчитать страницы; статус выпуска → uploaded.

        Файл копируется на диск порциями (целиком в память не читается) во временный *.part;
        после проверки PyMuPDF он атомарно переименовывается в SCANS_DIR/{year}-{number}.pdf.
        """
        issue = await self.issues.get_or_raise(issue_id)
        if issue.status is IssueStatus.OCR_IN_PROGRESS:
            raise Conflict(f"выпуск {issue_id}: идёт OCR, скан заменить нельзя")
        limit = self.settings.max_scan_bytes
        if file.size is not None and file.size > limit:
            raise PayloadTooLarge(f"скан больше {self.settings.MAX_SCAN_MB} МБ")
        head = await file.read(len(PDF_SIGNATURE))
        if head != PDF_SIGNATURE:
            raise ValidationFailed("файл не является PDF (нет сигнатуры %PDF-)")

        scans_dir = anyio.Path(self.settings.SCANS_DIR)
        await scans_dir.mkdir(parents=True, exist_ok=True)
        name = f"{issue.year}-{issue.number}.pdf"
        target, part = scans_dir / name, scans_dir / f"{name}.part"
        size = len(head)
        try:
            async with await anyio.open_file(part, "wb") as out:
                await out.write(head)
                while chunk := await file.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > limit:
                        raise PayloadTooLarge(f"скан больше {self.settings.MAX_SCAN_MB} МБ")
                    await out.write(chunk)
            # PyMuPDF — синхронная библиотека: считаем страницы в пуле потоков
            page_count = await anyio.to_thread.run_sync(count_pdf_pages, str(part))
            await part.replace(target)
        except BaseException:
            await part.unlink(missing_ok=True)
            raise

        await self.issues.update(
            issue, scan_path=name, page_count=page_count, status=IssueStatus.UPLOADED
        )
        log.info(
            "[api] scan issue_id=%d → %s: %.1f МБ, %d стр., статус uploaded",
            issue.id,
            target,
            size / 2**20,
            page_count,
        )
        return issue

    async def get_page(self, issue_id: int, page_number: int) -> Page:
        await self.issues.get_or_raise(issue_id)
        page = await self.pages.get_by_number(issue_id, page_number)
        if page is None:
            raise NotFound("Page", f"{issue_id}/{page_number}")
        return page

    async def _ensure_unique(self, year: int, number: int) -> None:
        if await self.issues.get_by_year_number(year, number) is not None:
            raise Conflict(f"выпуск {year} №{number} уже существует")
