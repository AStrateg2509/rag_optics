"""Управление пользователями: только admin."""


async def test_researcher_cannot_list_users_403(client, researcher):
    resp = await client.get("/users", headers=researcher.headers)
    assert resp.status_code == 403
    assert "admin" in resp.json()["detail"]


async def test_admin_lists_users(client, admin, researcher):
    resp = await client.get("/users", params={"limit": 1}, headers=admin.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2 and body["limit"] == 1 and len(body["items"]) == 1
    assert "password_hash" not in body["items"][0]


async def test_admin_promotes_user(client, admin, researcher):
    resp = await client.patch(
        f"/users/{researcher.id}", json={"role": "admin"}, headers=admin.headers
    )
    assert resp.status_code == 200 and resp.json()["role"] == "admin"
    # Новая роль действует сразу, без перевыпуска токена
    assert (await client.get("/users", headers=researcher.headers)).status_code == 200


async def test_admin_cannot_demote_or_block_self_409(client, admin):
    for change in ({"role": "researcher"}, {"is_active": False}):
        resp = await client.patch(f"/users/{admin.id}", json=change, headers=admin.headers)
        assert resp.status_code == 409


async def test_update_user_404_and_null_422(client, admin, researcher):
    assert (
        await client.patch("/users/999999", json={"role": "admin"}, headers=admin.headers)
    ).status_code == 404
    resp = await client.patch(f"/users/{researcher.id}", json={"role": None}, headers=admin.headers)
    assert resp.status_code == 422
