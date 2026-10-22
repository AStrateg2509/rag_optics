"""Статьи: CRUD с авторами и ключевыми словами, фильтры, проверки диапазона страниц."""

import pytest

from corag_db.repositories import PageRepository


@pytest.fixture
async def authors(client, admin) -> list[int]:
    ids = []
    for name in ("Сойфер Виктор Александрович", "Хонина Светлана Николаевна"):
        resp = await client.post("/authors", json={"full_name": name}, headers=admin.headers)
        ids.append(resp.json()["id"])
    return ids


@pytest.fixture
def article_in(uploaded_issue, authors) -> dict:
    return {
        "issue_id": uploaded_issue["id"],
        "title": "Дифракционные оптические элементы",
        "page_start": 2,
        "page_end": 4,
        "author_ids": [authors[1], authors[0]],
        "keywords": ["ДОЭ", " Дифракция "],
        "doi": "10.5555/co-2020-1-2",
    }


async def test_article_crud(client, admin, researcher, authors, article_in):
    resp = await client.post("/articles", json=article_in, headers=admin.headers)
    assert resp.status_code == 201, resp.text
    article = resp.json()
    # Порядок авторов = порядок author_ids; ключевые слова нормализованы
    assert [a["id"] for a in article["authors"]] == [authors[1], authors[0]]
    assert article["keywords"] == ["дифракция", "доэ"]
    assert article["issue"]["year"] == 2020

    url = f"/articles/{article['id']}"
    patch = {"author_ids": [authors[0]], "keywords": ["голография"], "page_end": 5}
    resp = await client.patch(url, json=patch, headers=admin.headers)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [a["id"] for a in body["authors"]] == [authors[0]]
    assert body["keywords"] == ["голография"] and body["page_end"] == 5

    card = await client.get(url, headers=researcher.headers)
    assert card.status_code == 200
    assert card.json()["authors"][0]["full_name"].startswith("Сойфер")

    assert (await client.delete(url, headers=researcher.headers)).status_code == 403
    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (await client.get(url, headers=researcher.headers)).status_code == 404


async def test_article_card_has_pages(client, session, admin, article_in):
    issue_id = article_in["issue_id"]
    for n in (1, 2, 3):
        await PageRepository(session).upsert(issue_id, n, f"Текст {n}", "test")
    await session.commit()
    created = (await client.post("/articles", json=article_in, headers=admin.headers)).json()
    card = (await client.get(f"/articles/{created['id']}", headers=admin.headers)).json()
    # Диапазон 2..4, распознаны пока только страницы 2 и 3
    assert [(p["page_number"], p["text_md"]) for p in card["pages"]] == [
        (2, "Текст 2"),
        (3, "Текст 3"),
    ]


@pytest.mark.parametrize(
    ("change", "fragment"),
    [
        ({"page_end": 7}, "больше числа страниц выпуска"),
        ({"page_start": 5, "page_end": 3}, "page_start больше page_end"),
        ({"author_ids": [999_999]}, "авторы не найдены"),
        ({"author_ids": [1, 1]}, "повторы"),
        ({"issue_id": 999_999}, "не существует"),
    ],
)
async def test_article_validation_422(client, admin, article_in, change, fragment):
    resp = await client.post("/articles", json={**article_in, **change}, headers=admin.headers)
    assert resp.status_code == 422
    assert fragment in str(resp.json()["detail"])


async def test_patch_range_checked_against_issue_422(client, admin, article_in):
    created = (await client.post("/articles", json=article_in, headers=admin.headers)).json()
    url = f"/articles/{created['id']}"
    over = await client.patch(url, json={"page_end": 99}, headers=admin.headers)
    assert over.status_code == 422
    inverted = await client.patch(url, json={"page_start": 5}, headers=admin.headers)
    assert inverted.status_code == 422  # 5 > page_end=4 из БД


async def test_duplicate_doi_409(client, admin, article_in):
    assert (
        await client.post("/articles", json=article_in, headers=admin.headers)
    ).status_code == 201
    dup = {**article_in, "title": "Другая статья"}
    resp = await client.post("/articles", json=dup, headers=admin.headers)
    assert resp.status_code == 409


async def test_filters(client, admin, researcher, authors, article_in):
    await client.post("/articles", json=article_in, headers=admin.headers)
    other = {
        **article_in,
        "title": "Голография",
        "page_start": 5,
        "page_end": 6,
        "author_ids": [authors[1]],
        "keywords": ["голография"],
        "doi": None,
    }
    await client.post("/articles", json=other, headers=admin.headers)

    async def titles(**params) -> list[str]:
        resp = await client.get("/articles", params=params, headers=researcher.headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == len(body["items"])
        return [a["title"] for a in body["items"]]

    assert await titles(author="сойфер") == ["Дифракционные оптические элементы"]
    assert await titles(author="Хонина") == ["Дифракционные оптические элементы", "Голография"]
    assert await titles(keyword="Голография") == ["Голография"]
    assert await titles(year=2020, keyword="доэ") == ["Дифракционные оптические элементы"]
    assert len(await titles(issue_id=article_in["issue_id"])) == 2
    assert await titles(year=2019) == []
    paged = await client.get("/articles", params={"limit": 1}, headers=researcher.headers)
    assert paged.json()["total"] == 2 and len(paged.json()["items"]) == 1
