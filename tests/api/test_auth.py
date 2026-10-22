"""Регистрация, вход, JWT: 401 и 403."""

import base64
import json
from datetime import timedelta

from corag_contracts.auth import create_access_token

NEW_USER = {"email": "Ivan@Example.com", "password": "secret-pass", "full_name": "Иван Иванов"}


async def test_register(client):
    resp = await client.post("/auth/register", json=NEW_USER)
    assert resp.status_code == 201
    body = resp.json()
    assert body["email"] == "ivan@example.com"
    assert body["role"] == "researcher" and body["is_active"] is True
    assert "password_hash" not in body and "password" not in body


async def test_register_duplicate_409(client):
    assert (await client.post("/auth/register", json=NEW_USER)).status_code == 201
    dup = {**NEW_USER, "email": "IVAN@example.com"}
    resp = await client.post("/auth/register", json=dup)
    assert resp.status_code == 409
    assert "уже зарегистрирован" in resp.json()["detail"]


async def test_register_validation_422(client):
    short = await client.post("/auth/register", json={**NEW_USER, "password": "1234567"})
    assert short.status_code == 422
    too_long = await client.post("/auth/register", json={**NEW_USER, "password": "я" * 40})
    assert too_long.status_code == 422  # 80 байт > 72 (bcrypt)
    bad_email = await client.post("/auth/register", json={**NEW_USER, "email": "not-an-email"})
    assert bad_email.status_code == 422


async def test_login_returns_jwt(client, researcher):
    resp = await client.post(
        "/auth/login", data={"username": researcher.email, "password": researcher.password}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer" and body["expires_in"] == 30 * 60
    payload = json.loads(base64.urlsafe_b64decode(body["access_token"].split(".")[1] + "=="))
    assert payload["sub"] == str(researcher.id) and payload["role"] == "researcher"
    assert payload["exp"] - payload["iat"] == 30 * 60


async def test_login_wrong_password_401(client, researcher):
    resp = await client.post("/auth/login", data={"username": researcher.email, "password": "x"})
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    unknown = await client.post("/auth/login", data={"username": "no@one.ru", "password": "x"})
    assert unknown.status_code == 401


async def test_blocked_user_403(client, admin, researcher):
    resp = await client.patch(
        f"/users/{researcher.id}", json={"is_active": False}, headers=admin.headers
    )
    assert resp.status_code == 200
    # Старый токен перестаёт работать сразу: статус проверяется по БД
    assert (await client.get("/users/me", headers=researcher.headers)).status_code == 403
    login = await client.post(
        "/auth/login", data={"username": researcher.email, "password": researcher.password}
    )
    assert login.status_code == 403
    assert login.json()["detail"] == "пользователь заблокирован"


async def test_me(client, researcher):
    resp = await client.get("/users/me", headers=researcher.headers)
    assert resp.status_code == 200
    assert resp.json()["email"] == researcher.email


async def test_no_token_401(client):
    resp = await client.get("/users/me")
    assert resp.status_code == 401
    assert resp.headers["www-authenticate"] == "Bearer"
    assert (await client.get("/issues")).status_code == 401


async def test_bad_tokens_401(client, settings, researcher):
    expired = create_access_token(
        researcher.id, "researcher", settings.JWT_SECRET, timedelta(seconds=-1)
    )
    forged = create_access_token(researcher.id, "admin", "x" * 40, timedelta(minutes=5))
    for token in (expired, forged, "garbage"):
        resp = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 401, token
        assert resp.json()["detail"].startswith("недействительный токен")


async def test_tampered_role_401(client, researcher):
    """Подменить role в payload без секрета нельзя: подпись не сойдётся."""
    header, payload, signature = researcher.headers["Authorization"][7:].split(".")
    data = json.loads(base64.urlsafe_b64decode(payload + "=="))
    data["role"] = "admin"
    forged = base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()
    headers = {"Authorization": f"Bearer {header}.{forged}.{signature}"}
    assert (await client.get("/users", headers=headers)).status_code == 401


async def test_token_of_deleted_user_401(client, settings):
    token = create_access_token(999_999, "admin", settings.JWT_SECRET, timedelta(minutes=5))
    resp = await client.get("/users/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
