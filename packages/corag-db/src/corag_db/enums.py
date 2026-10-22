"""Перечисления предметной области; в PostgreSQL хранятся как ENUM-типы."""

from enum import StrEnum

from sqlalchemy import Enum


class UserRole(StrEnum):
    RESEARCHER = "researcher"
    ADMIN = "admin"


class IssueStatus(StrEnum):
    CREATED = "created"
    UPLOADED = "uploaded"
    OCR_IN_PROGRESS = "ocr_in_progress"
    OCR_DONE = "ocr_done"
    INDEXED = "indexed"
    FAILED = "failed"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


def pg_enum(enum_cls: type[StrEnum], name: str) -> Enum:
    """ENUM-тип PostgreSQL по значениям (а не именам) членов Python-перечисления."""
    return Enum(enum_cls, name=name, values_callable=lambda e: [m.value for m in e])
