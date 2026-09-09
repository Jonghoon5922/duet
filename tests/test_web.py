"""대시보드가 폴더를 그대로 읽어 내보내는지, 화면에서 정한 상태가 파일에 적히는지."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from duet import core
from duet.web import create_app


@pytest.fixture()
def client():
    return TestClient(create_app())


def test_보드가_타스크를_내려준다(client):
    task = core.create_task("pc101pm 전환", "NEFSS→BXM")
    s = core.register_session("Claude Code")
    core.join(s, task.id, "DBIO 계층 전환")
    core.report(s, "DBIO 5개 전환", 40)

    body = client.get("/api/tasks").json()
    assert [t["id"] for t in body["tasks"]] == [task.id]
    assert body["tasks"][0]["status"] == core.RUNNING
    assert body["tasks"][0]["counts"] == {"진행중": 1, "완료": 0, "중지": 0}


def test_상세에_세션과_진행_로그가_들어온다(client):
    task = core.create_task("전환")
    s = core.register_session("Claude Code")
    core.join(s, task.id, "DBIO 계층 전환")
    core.report(s, "설계서 훑음", 20)

    body = client.get(f"/api/tasks/{task.id}").json()
    assert body["sessions"][0]["title"] == "DBIO 계층 전환"
    assert body["sessions"][0]["progress"][0]["msg"] == "설계서 훑음"
    assert body["sessions"][0]["alive"] is True


def test_상태로_거른다(client):
    done = core.create_task("끝난 것")
    core.set_status(done.id, core.DONE)
    core.create_task("안 끝난 것")

    body = client.get("/api/tasks", params={"status": core.DONE}).json()
    assert [t["id"] for t in body["tasks"]] == [done.id]


def test_화면에서_정한_상태가_task_md에_적힌다(client):
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)

    body = client.post(f"/api/tasks/{task.id}/status", json={"status": core.DONE}).json()
    assert body["status"] == core.DONE
    assert "상태: 완료" in (task.dir / "task.md").read_text(encoding="utf-8")

    # 다시 자동으로 — 그 줄이 사라지고 세션에서 센 값이 돌아온다
    body = client.post(f"/api/tasks/{task.id}/status", json={"status": None}).json()
    assert body["status"] == core.RUNNING
    assert "상태:" not in (task.dir / "task.md").read_text(encoding="utf-8")


def test_없는_타스크와_모르는_상태는_거부한다(client):
    task = core.create_task("전환")
    assert client.get("/api/tasks/T999").status_code == 404
    assert client.post(f"/api/tasks/{task.id}/status", json={"status": "아무거나"}).status_code == 400


def test_화면에서_타스크를_만든다(client):
    body = client.post("/api/tasks", json={"title": "pc101pm 전환", "description": "NEFSS→BXM"}).json()
    assert body["id"] == "T001"
    assert body["status"] == core.WAITING
    assert core.get_task("T001").description == "NEFSS→BXM"

    assert client.post("/api/tasks", json={"title": "  "}).status_code == 400


def test_제목과_설명을_인라인으로_고친다(client):
    task = core.create_task("옛 제목", "옛 설명")
    core.set_status(task.id, core.WAITING)  # 사람이 정해 둔 상태

    body = client.patch(f"/api/tasks/{task.id}", json={"title": "새 제목"}).json()
    assert body["title"] == "새 제목"
    assert body["description"] == "옛 설명", "안 준 것은 안 바뀐다"
    assert body["override"] == core.WAITING, "상태는 건드리지 않는다"

    assert client.patch(f"/api/tasks/{task.id}", json={"title": ""}).status_code == 400


def test_목록에_세션과_로그가_함께_온다(client):
    task = core.create_task("전환")
    s = core.register_session("Claude Code")
    core.join(s, task.id, "DBIO 계층 전환")
    core.report(s, "설계서 훑음", 30)

    row = client.get("/api/tasks").json()["tasks"][0]
    assert row["sessions"][0]["progress"][0]["msg"] == "설계서 훑음"
    assert row["folder"].endswith(task.dir.name)
