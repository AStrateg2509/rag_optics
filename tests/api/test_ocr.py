"""Запуск OCR: 202, повтор 409, статус задачи, публикация строго после commit."""


async def test_ocr_without_scan_409(client, admin, issue, publisher):
    resp = await client.post(f"/issues/{issue['id']}/ocr", headers=admin.headers)
    assert resp.status_code == 409
    assert "created" in resp.json()["detail"]
    assert publisher.jobs == []


async def test_start_ocr_202_then_409(client, admin, uploaded_issue, publisher):
    url = f"/issues/{uploaded_issue['id']}/ocr"
    resp = await client.post(url, headers=admin.headers)
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "queued" and job["pages_total"] == 6 and job["pages_done"] == 0

    issue = (await client.get(f"/issues/{uploaded_issue['id']}", headers=admin.headers)).json()
    assert issue["status"] == "ocr_in_progress"

    [message] = publisher.jobs
    assert message.model_dump() == {
        "job_id": job["id"],
        "issue_id": uploaded_issue["id"],
        "scan_path": "2020-1.pdf",
        "requested_by": job["requested_by"],
    }

    again = await client.post(url, headers=admin.headers)
    assert again.status_code == 409
    assert len(publisher.jobs) == 1


async def test_active_job_conflict_409(client, session, admin, uploaded_issue):
    """Даже если статус выпуска сбросили, вторую активную задачу не даст создать индекс БД."""
    from corag_db.enums import IssueStatus
    from corag_db.repositories import IssueRepository

    url = f"/issues/{uploaded_issue['id']}/ocr"
    assert (await client.post(url, headers=admin.headers)).status_code == 202
    repo = IssueRepository(session)
    await repo.set_status(await repo.get(uploaded_issue["id"]), IssueStatus.FAILED)
    await session.commit()
    resp = await client.post(url, headers=admin.headers)
    assert resp.status_code == 409
    assert "активная задача" in resp.json()["detail"]


async def test_publish_after_commit(client, session, admin, uploaded_issue, publisher, monkeypatch):
    real_commit = session.commit

    async def spy_commit() -> None:
        publisher.events.append("commit")
        await real_commit()

    monkeypatch.setattr(session, "commit", spy_commit)
    resp = await client.post(f"/issues/{uploaded_issue['id']}/ocr", headers=admin.headers)
    assert resp.status_code == 202
    assert publisher.events[:2] == ["commit", "publish"]


async def test_job_status(client, admin, researcher, uploaded_issue):
    job = (await client.post(f"/issues/{uploaded_issue['id']}/ocr", headers=admin.headers)).json()
    resp = await client.get(f"/ocr-jobs/{job['id']}", headers=admin.headers)
    assert resp.status_code == 200 and resp.json()["status"] == "queued"
    assert (
        await client.get(f"/ocr-jobs/{job['id']}", headers=researcher.headers)
    ).status_code == 403
    assert (await client.get("/ocr-jobs/999999", headers=admin.headers)).status_code == 404
    assert (await client.post("/issues/999999/ocr", headers=admin.headers)).status_code == 404
