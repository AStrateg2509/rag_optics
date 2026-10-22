"""Доменные исключения слоя данных. В API они переводятся в HTTP 404 и 409."""


class NotFound(Exception):
    """Объект не найден."""

    def __init__(self, entity: str, key: object) -> None:
        super().__init__(f"{entity} {key!r} не найден")
        self.entity = entity
        self.key = key


class Conflict(Exception):
    """Операция нарушает ограничение уникальности или бизнес-правило."""
