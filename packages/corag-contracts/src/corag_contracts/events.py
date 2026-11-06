"""Сообщения RabbitMQ в формате JSON (приложение Б ТЗ).

Модели проверяются и при публикации, и при получении. В ЛР4 сюда добавится конверт события.
"""

from pydantic import BaseModel


class OcrJobMessage(BaseModel):
    """Задача распознавания выпуска в очереди ocr.jobs."""

    job_id: int
    issue_id: int
    scan_path: str  # путь относительно SCANS_DIR (общий том API и OCR Worker)
    requested_by: int
