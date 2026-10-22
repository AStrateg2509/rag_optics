"""Публикация задач OCR в брокер.

В ЛР2 — заглушка LoggingPublisher; в ЛР4 её заменит AmqpPublisher (aio-pika, очередь ocr.jobs).
"""

import logging
from typing import Protocol

from corag_contracts.events import OcrJobMessage

log = logging.getLogger("corag_api")


class JobPublisher(Protocol):
    async def publish_job(self, job: OcrJobMessage) -> None: ...


class LoggingPublisher:
    """Ничего не отправляет: пишет в лог сообщение, которое ушло бы в очередь ocr.jobs."""

    async def publish_job(self, job: OcrJobMessage) -> None:
        log.info(
            "[api] → publish ocr.jobs %s (LoggingPublisher, брокер — в ЛР4)", job.model_dump_json()
        )
