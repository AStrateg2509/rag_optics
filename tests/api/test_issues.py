"""Выпуски: роли, CRUD, загрузка скана, страницы."""

from pathlib import Path

from corag_db.enums import IssueStatus
from corag_db.repositories import IssueRepository, PageRepository


def stored_files(scans_dir: Path) -> list[str]:
    return sorted(p.name for p in scans_dir.iterdir()) if scans_dir.exists() else []


async def test_researcher_cannot_create_issue_403(client, researcher):
    resp = await client.post(
        "/issues", json={"year": 2020, "number": 1}, headers=researcher.headers
    )
    assert resp.status_code == 403


async def test_create_issue(client, issue):
    assert issue["status"] == "created"
    assert (issue["year"], issue["volume"], issue["number"]) == (2020, 44, 1)
    assert issue["scan_path"] is None and issue["page_count"] is None


async def test_create_duplicate_409_and_bad_year_422(client, admin, issue):
    dup = await client.post("/issues", json={"year": 2020, "number": 1}, headers=admin.headers)
    assert dup.status_code == 409
    old = await client.post("/issues", json={"year": 1980, "number": 1}, headers=admin.headers)
    assert old.status_code == 422


async def test_list_filter_by_year(client, admin, researcher, issue):
    for year, number in ((2020, 2), (2021, 1)):
        await client.post("/issues", json={"year": year, "number": number}, headers=admin.headers)
    resp = await client.get("/issues", params={"year": 2020}, headers=researcher.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 2
    assert [(i["year"], i["number"]) for i in body["items"]] == [(2020, 1), (2020, 2)]
    everything = (await client.get("/issues", headers=researcher.headers)).json()
    assert everything["total"] == 3 and everything["items"][0]["year"] == 2021


async def test_issue_card_counts(client, session, researcher, uploaded_issue):
    await PageRepository(session).upsert(uploaded_issue["id"], 1, "# Текст", "test")
    await session.commit()
    resp = await client.get(f"/issues/{uploaded_issue['id']}", headers=researcher.headers)
    assert resp.status_code == 200
    body = resp.json()
    assert (body["pages_count"], body["articles_count"], body["chunks_count"]) == (1, 0, 0)
    assert body["page_count"] == 6


async def test_patch_and_delete_issue(client, admin, issue):
    url = f"/issues/{issue['id']}"
    resp = await client.patch(url, json={"volume": None, "number": 5}, headers=admin.headers)
    assert resp.status_code == 200
    assert (resp.json()["volume"], resp.json()["number"]) == (None, 5)
    assert (await client.patch(url, json={"year": None}, headers=admin.headers)).status_code == 422

    assert (await client.delete(url, headers=admin.headers)).status_code == 204
    assert (await client.get(url, headers=admin.headers)).status_code == 404
    assert (await client.delete(url, headers=admin.headers)).status_code == 404


async def test_committed_before_response(client, session, admin):
    """201 приходит уже после commit: следующий запрос видит выпуск."""
    resp = await client.post("/issues", json={"year": 2022, "number": 7}, headers=admin.headers)
    issue = await IssueRepository(session).get(resp.json()["id"])
    assert issue is not None and issue.number == 7


async def test_upload_scan(client, admin, settings, uploaded_issue):
    assert uploaded_issue["status"] == "uploaded"
    assert uploaded_issue["page_count"] == 6
    assert uploaded_issue["scan_path"] == "2020-1.pdf"
    assert (settings.SCANS_DIR / "2020-1.pdf").read_bytes().startswith(b"%PDF-")
    assert stored_files(settings.SCANS_DIR) == ["2020-1.pdf"]  # .part не остался


async def test_upload_not_pdf_422(client, admin, settings, issue):
    files = {"file": ("scan.pdf", b"hello, not a pdf", "application/pdf")}
    resp = await client.post(f"/issues/{issue['id']}/scan", files=files, headers=admin.headers)
    assert resp.status_code == 422
    assert "%PDF-" in resp.json()["detail"]


async def test_upload_broken_pdf_422(client, admin, settings, issue):
    files = {"file": ("scan.pdf", b"%PDF-1.7\n garbage", "application/pdf")}
    resp = await client.post(f"/issues/{issue['id']}/scan", files=files, headers=admin.headers)
    assert resp.status_code == 422
    assert stored_files(settings.SCANS_DIR) == []


async def test_upload_too_large_413(client, admin, settings, issue, pdf):
    big = pdf(2, pad_bytes=1024 * 1024 + 1)  # MAX_SCAN_MB=1 в тестовых настройках
    files = {"file": ("scan.pdf", big, "application/pdf")}
    resp = await client.post(f"/issues/{issue['id']}/scan", files=files, headers=admin.headers)
    assert resp.status_code == 413
    assert stored_files(settings.SCANS_DIR) == []


async def test_upload_forbidden_and_missing(client, researcher, admin, pdf):
    files = {"file": ("scan.pdf", pdf(1), "application/pdf")}
    assert (
        await client.post("/issues/1/scan", files=files, headers=researcher.headers)
    ).status_code == 403
    assert (
        await client.post("/issues/999999/scan", files=files, headers=admin.headers)
    ).status_code == 404


async def test_upload_during_ocr_409(client, session, admin, uploaded_issue, pdf):
    repo = IssueRepository(session)
    await repo.set_status(await repo.get(uploaded_issue["id"]), IssueStatus.OCR_IN_PROGRESS)
    await session.commit()
    files = {"file": ("scan.pdf", pdf(1), "application/pdf")}
    url = f"/issues/{uploaded_issue['id']}"
    assert (await client.post(f"{url}/scan", files=files, headers=admin.headers)).status_code == 409
    assert (await client.delete(url, headers=admin.headers)).status_code == 409


async def test_get_page(client, session, researcher, uploaded_issue):
    issue_id = uploaded_issue["id"]
    await PageRepository(session).upsert(issue_id, 2, "## Страница 2", "deepseek-ocr")
    await session.commit()
    resp = await client.get(f"/issues/{issue_id}/pages/2", headers=researcher.headers)
    assert resp.status_code == 200
    assert resp.json()["text_md"] == "## Страница 2"
    missing = await client.get(f"/issues/{issue_id}/pages/3", headers=researcher.headers)
    assert missing.status_code == 404
    assert (
        await client.get("/issues/999999/pages/1", headers=researcher.headers)
    ).status_code == 404
