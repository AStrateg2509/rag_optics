"""Декларативная база ORM-моделей."""

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Шаблоны имён constraint'ов: имена стабильны, Alembic генерирует аккуратные миграции,
# а downgrade может удалить constraint по имени.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Базовый класс всех моделей CO-RAG."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)
