"""규칙이 지켜지는지만 본다. 자동 완료, 중지 처리, 사람이 정한 상태."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from duet import core


def _stale(session):
    """하트비트를 90초 밖으로 밀어 죽은 세션으로 만든다."""
    session.heartbeat = (datetime.now() - timedelta(seconds=200)).isoformat()
    session.save()


def test_새_타스크는_대기고_폴더가_생긴다():
    task = core.create_task("pc101pm 전환", "NEFSS→BXM")

    assert task.id == "T001"
    assert task.status == core.WAITING
    assert task.dir.name == "T001-pc101pm-전환"
    assert (task.dir / "task.md").read_text(encoding="utf-8").startswith("# pc101pm 전환")
    assert core.create_task("다음 일").id == "T002"


def test_참여하면_세션_파일이_타스크로_옮겨간다():
    task = core.create_task("전환")
    s = core.register_session()
    assert s.path.parent.name == core.IDLE_DIRNAME

    after = core.join(s, task.id, "DBIO 계층 전환")
    assert after.status == core.RUNNING
    assert s.path.parent == task.dir
    assert not list((core.home() / core.IDLE_DIRNAME).glob("s-*.json"))


def test_세션이_전부_완료면_타스크가_완료로_계산된다():
    task = core.create_task("전환")
    a, b = core.register_session(), core.register_session()
    core.join(a, task.id, "DBIO")
    core.join(b, task.id, "Bean")

    assert core.finish(a, core.DONE, "DBIO 끝").status == core.RUNNING, "아직 b가 남았다"
    assert core.finish(b, core.DONE, "Bean 끝").status == core.DONE


def test_중지가_섞이면_완료로_계산하지_않는다():
    task = core.create_task("전환")
    a, b = core.register_session(), core.register_session()
    core.join(a, task.id)
    core.join(b, task.id)

    core.finish(a, core.DONE, "끝")
    after = core.finish(b, core.STOPPED, "여기까지")

    assert after.status == core.RUNNING, "사람이 판단하도록 열어 둔다"
    assert "중지된 세션 1개" in after.warning


def test_하트비트가_끊기면_중지로_친다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    _stale(s)

    assert core.get_task(task.id).counts[core.STOPPED] == 1, "걷어내기 전에도 사실대로 센다"
    assert core.reap() == [s.id]
    assert core.get_task(task.id).sessions[0].status == core.STOPPED
    assert core.reap() == [], "두 번 걷어내지 않는다"


def test_사람이_적은_상태가_이긴다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)

    assert core.set_status(task.id, core.WAITING).status == core.WAITING
    core.finish(s, core.DONE, "끝")
    assert core.get_task(task.id).status == core.WAITING, "자동 규칙이 덮지 않는다"

    assert core.set_status(task.id, None).status == core.DONE, "지우면 다시 센다"


def test_사람이_손으로_고쳐도_읽는다():
    task = core.create_task("전환", "설명 줄")
    (task.dir / "task.md").write_text("# 새 제목\n상태: 완료\n\n고친 설명\n", encoding="utf-8")

    after = core.get_task(task.id)
    assert (after.title, after.status, after.description) == ("새 제목", "완료", "고친 설명")


def test_먼저_적힌_종료가_남는다():
    """complete_session 뒤에 종료 훅이 돌아도, 창을 닫을 때 사람이 고친 것도 덮지 않는다."""
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.finish(s, core.DONE, "끝")

    core.finish(s, core.STOPPED, "창 닫힘")
    assert core.get_task(task.id).sessions[0].status == core.DONE


def test_진행_로그는_세션_파일에_쌓인다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.report(s, " 스키마 훑음 ", 20)
    core.report(s, "DBIO 3개 전환", 60)

    logged = core.get_task(task.id).sessions[0].progress
    assert [e["msg"] for e in logged] == ["스키마 훑음", "DBIO 3개 전환"]

    with pytest.raises(core.DuetError):
        core.report(s, "  ")


def test_끝난_세션은_더_쓰지_못한다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.finish(s, core.DONE, "끝")

    with pytest.raises(core.DuetError):
        core.report(s, "하나 더")
    with pytest.raises(core.DuetError):
        core.join(s, task.id)


def test_id_표기가_흔들려도_찾는다():
    task = core.create_task("전환")
    for given in ("T001", "t001", "1", 1):
        assert core.get_task(given).id == task.id
    with pytest.raises(core.DuetError):
        core.get_task("없는것")


def test_깨진_파일_하나가_목록을_막지_않는다():
    good = core.create_task("멀쩡한 것")
    (core.home() / core.IDLE_DIRNAME / "s-broken.json").write_text("{반쪽", encoding="utf-8")

    assert [t.id for t in core.list_tasks()] == [good.id]


def test_상세에_다른_세션의_로그가_들어온다():
    """세션 B가 A의 일을 읽고 이어받는 재료. get_task와 대시보드가 같이 쓴다."""
    task = core.create_task("전환", "NEFSS→BXM")
    a = core.register_session("Claude Code")
    core.join(a, task.id, "DBIO 계층 전환")
    core.report(a, "설계서 훑음", 30)
    core.finish(a, core.STOPPED, "여기까지 — Service는 다음 세션에서")

    detail = core.get_task(task.id).to_detail()
    assert detail["description"] == "NEFSS→BXM"
    assert detail["folder"] == str(task.dir)
    assert detail["counts"] == {"진행중": 0, "완료": 0, "중지": 1}
    assert detail["sessions"][0]["title"] == "DBIO 계층 전환"
    assert detail["sessions"][0]["progress"][0]["msg"] == "설계서 훑음"
    assert detail["sessions"][0]["summary"] == "여기까지 — Service는 다음 세션에서"
    assert detail["sessions"][0]["alive"] is False
