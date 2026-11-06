"""Общие DTO: пагинация, частичное обновление, служебные ответы."""

from collections.abc import Sequence
from typing import Annotated, Any, ClassVar, Self

from pydantic import BaseModel, StringConstraints, model_validator

# Непустая строка без пробелов по краям
NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Paginated[T](BaseModel):
    """Страница списка: элементы, общее число под фильтром, параметры выборки."""

    items: list[T]
    total: int
    limit: int
    offset: int

    @classmethod
    def of(cls, items: Sequence[Any], total: int, limit: int, offset: int) -> Self:
        """Страница из ORM-объектов: элементы превращаются в DTO по атрибутам."""
        data = {"items": items, "total": total, "limit": limit, "offset": offset}
        return cls.model_validate(data, from_attributes=True)


class PatchModel(BaseModel):
    """База схем PATCH: все поля необязательны, применяются только переданные.

    Явный null разрешён только для полей из nullable (у остальных в БД NOT NULL).
    """

    nullable: ClassVar[frozenset[str]] = frozenset()

    @model_validator(mode="after")
    def _forbid_null(self) -> Self:
        bad = sorted(
            f for f in self.model_fields_set if getattr(self, f) is None and f not in self.nullable
        )
        if bad:
            raise ValueError(f"поля не могут быть null: {', '.join(bad)}")
        return self

    def changes(self) -> dict[str, Any]:
        """Только поля, переданные в запросе."""
        return self.model_dump(exclude_unset=True)


class HealthOut(BaseModel):
    status: str
    db: str
