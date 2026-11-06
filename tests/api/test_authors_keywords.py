"""Авторы и ключевые слова: поиск, CRUD, ограничения."""


async def test_author_crud_and_search(client, admin, researcher):
    data = {"full_name": "Сойфер В. А.", "orcid": "0000-0002-1825-0097"}
    resp = await client.post("/authors", json=data, headers=admin.headers)
    assert resp.status_code == 201
    author_id = resp.json()["id"]
    url = f"/authors/{author_id}"

    found = (await client.get("/authors", params={"q": "СОЙФ"}, headers=researcher.headers)).json()
    assert found["total"] == 1 and found["items"][0]["orcid"] == data["orcid"]

    resp = await client.patch(
        url, json={"affiliation": "Самарский университет"}, headers=admin.headers
    )
    assert resp.status_code == 200 and resp.json()["affiliation"] == "Самарский университет"
    assert (await client.get(url, headers=researcher.headers)).status_code == 200
    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (await client.get(url, headers=researcher.headers)).status_code == 404


async def test_author_validation(client, admin, researcher):
    orcid = {"full_name": "А", "orcid": "0000-0002-1825-0097"}
    assert (await client.post("/authors", json=orcid, headers=admin.headers)).status_code == 201
    assert (await client.post("/authors", json=orcid, headers=admin.headers)).status_code == 409
    bad = {"full_name": "Б", "orcid": "12345"}
    assert (await client.post("/authors", json=bad, headers=admin.headers)).status_code == 422
    blank = {"full_name": "   "}
    assert (await client.post("/authors", json=blank, headers=admin.headers)).status_code == 422
    resp = await client.post("/authors", json={"full_name": "В"}, headers=researcher.headers)
    assert resp.status_code == 403


async def test_delete_author_with_articles_409(client, admin, uploaded_issue):
    author = (await client.post("/authors", json={"full_name": "А"}, headers=admin.headers)).json()
    article = {
        "issue_id": uploaded_issue["id"],
        "title": "Статья",
        "page_start": 1,
        "page_end": 1,
        "author_ids": [author["id"]],
    }
    assert (await client.post("/articles", json=article, headers=admin.headers)).status_code == 201
    resp = await client.delete(f"/authors/{author['id']}", headers=admin.headers)
    assert resp.status_code == 409
    assert "указан в статьях" in resp.json()["detail"]


async def test_keywords(client, admin, researcher):
    resp = await client.post("/keywords", json={"value": "  Дифракция "}, headers=admin.headers)
    assert resp.status_code == 201 and resp.json()["value"] == "дифракция"
    dup = await client.post("/keywords", json={"value": "ДИФРАКЦИЯ"}, headers=admin.headers)
    assert dup.status_code == 409
    await client.post("/keywords", json={"value": "голография"}, headers=admin.headers)

    listed = (await client.get("/keywords", headers=researcher.headers)).json()
    assert [k["value"] for k in listed["items"]] == ["голография", "дифракция"]
    found = (await client.get("/keywords", params={"q": "ДИФ"}, headers=researcher.headers)).json()
    assert found["total"] == 1

    url = f"/keywords/{resp.json()['id']}"
    assert (await client.delete(url, headers=researcher.headers)).status_code == 403
    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (await client.delete(url, headers=admin.headers)).status_code == 404
