"""Служебный эндпоинт /health."""

from httpx import ASGITransport, AsyncClient

from corag_api.deps import get_session
from corag_api.main import create_app
from corag_api.settings import get_settings
from corag_db.session import create_engine, create_sessionmaker


async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "db": "ok"}


async def test_health_db_down_503(settings):
    """БД недоступна (никто не слушает порт) → 503, а не 500."""
    engine = create_engine("postgresql+asyncpg://corag:corag@127.0.0.1:1/corag")
    app = create_app()

    async def dead_session():
        async with create_sessionmaker(engine)() as session:
            yield session

    app.dependency_overrides[get_session] = dead_session
    app.dependency_overrides[get_settings] = lambda: settings
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/health")
    await engine.dispose()
    assert resp.status_code == 503
    assert resp.json() == {"detail": "БД недоступна"}
