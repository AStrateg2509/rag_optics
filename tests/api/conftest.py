"""Фикстуры тестов API: приложение на тестовой сессии, пользователи и токены, PDF."""

import importlib.util
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path

import bcrypt
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from corag_api.deps import get_publisher, get_session
from corag_api.main import create_app
from corag_api.settings import ApiSettings, get_settings
from corag_contracts.events import OcrJobMessage
from corag_db.enums import UserRole
from corag_db.repositories import UserRepository

TEST_SECRET = "test-secret-0123456789abcdef0123456789"
PASSWORD = "password123"

_SCRIPT = Path(__file__).parents[2] / "scripts" / "make_test_pdf.py"
_spec = importlib.util.spec_from_file_location("make_test_pdf", _SCRIPT)
make_test_pdf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(make_test_pdf)


@pytest.fixture(scope="session", autouse=True)
def fast_bcrypt():
    """bcrypt с минимальной стоимостью: по умолчанию (12 раундов) хеш занимает ~0,25 с."""
    real_gensalt = bcrypt.gensalt
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(bcrypt, "gensalt", lambda rounds=4, prefix=b"2b": real_gensalt(4, prefix))
        yield


class RecordingPublisher:
    """Публикатор-шпион: запоминает задачи вместо отправки в брокер."""

    def __init__(self) -> None:
        self.jobs: list[OcrJobMessage] = []
        self.events: list[str] = []  # общий журнал с шпионом commit (тест порядка)

    async def publish_job(self, job: OcrJobMessage) -> None:
        self.jobs.append(job)
        self.events.append("publish")


@pytest.fixture
def settings(tmp_path: Path) -> ApiSettings:
    return ApiSettings(SCANS_DIR=tmp_path / "scans", MAX_SCAN_MB=1, JWT_SECRET=TEST_SECRET)


@pytest.fixture
def publisher() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
async def client(
    session: AsyncSession, settings: ApiSettings, publisher: RecordingPublisher
) -> AsyncIterator[AsyncClient]:
    """HTTP-клиент к приложению в памяти (без сети и без lifespan)."""
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        # Как get_session, но в тестовой транзакции: commit/rollback — это SAVEPOINT.
        # expunge_all: следующий запрос читает из БД, как в новой сессии
        try:
            yield session
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            session.expunge_all()

    app.dependency_overrides[get_session] = test_session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_publisher] = lambda: publisher
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@dataclass(frozen=True)
class TestUser:
    id: int
    email: str
    password: str
    headers: dict[str, str]


async def _make_user(
    session: AsyncSession, client: AsyncClient, email: str, role: UserRole
) -> TestUser:
    user = await UserRepository(session).create(email, PASSWORD, email.split("@")[0], role)
    user_id = user.id
    await session.commit()
    resp = await client.post("/auth/login", data={"username": email, "password": PASSWORD})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return TestUser(user_id, email, PASSWORD, {"Authorization": f"Bearer {token}"})


@pytest.fixture
async def admin(session, client) -> TestUser:
    return await _make_user(session, client, "admin@test.local", UserRole.ADMIN)


@pytest.fixture
async def researcher(session, client) -> TestUser:
    return await _make_user(session, client, "researcher@test.local", UserRole.RESEARCHER)


@pytest.fixture
def pdf() -> Callable[..., bytes]:
    """Фабрика PDF: pdf(pages=3, pad_bytes=0) → байты корректного PDF."""
    return make_test_pdf.make_pdf


@pytest.fixture
async def issue(client, admin) -> dict:
    resp = await client.post(
        "/issues", json={"year": 2020, "volume": 44, "number": 1}, headers=admin.headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
async def uploaded_issue(client, admin, issue, pdf) -> dict:
    """Выпуск с загруженным сканом на 6 страниц (статус uploaded)."""
    files = {"file": ("scan.pdf", pdf(6), "application/pdf")}
    resp = await client.post(f"/issues/{issue['id']}/scan", files=files, headers=admin.headers)
    assert resp.status_code == 200, resp.text
    return resp.json()
