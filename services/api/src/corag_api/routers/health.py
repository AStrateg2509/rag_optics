"""Проверка живости сервиса и доступности БД."""

import logging

from fastapi import APIRouter, HTTPException
from sqlalchemy.exc import SQLAlchemyError

from corag_api.deps import SessionDep
from corag_api.schemas.common import HealthOut
from corag_db.session import ping

log = logging.getLogger("corag_api")
router = APIRouter(tags=["service"])


@router.get("/health", response_model=HealthOut, summary="Живость сервиса и доступность БД")
async def health(session: SessionDep) -> HealthOut:
    try:
        await ping(session)
    except (OSError, SQLAlchemyError) as exc:
        log.error("[api] health: БД недоступна: %s", exc)
        raise HTTPException(503, "БД недоступна") from exc
    return HealthOut(status="ok", db="ok")
