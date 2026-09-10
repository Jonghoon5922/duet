"""규칙이 지켜지는지만 본다. 자동 완료, 중지 처리, 사람이 정한 상태."""

from __future__ import annotations

import pathlib
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


def test_제목과_설명을_고쳐도_폴더와_상태는_그대로다():
    task = core.create_task("옛 제목", "옛 설명")
    core.set_status(task.id, core.WAITING)  # 사람이 정해 둔 상태

    after = core.update_task(task.id, title="새 제목")
    assert (after.title, after.description) == ("새 제목", "옛 설명"), "안 준 것은 안 바뀐다"
    assert after.dir.name == task.dir.name, "id가 판별자다"
    assert after.status == core.WAITING and after.override == core.WAITING

    assert core.update_task(task.id, description="새 설명").description == "새 설명"
    with pytest.raises(core.DuetError):
        core.update_task(task.id, title="   ")


def test_끝난_세션은_사람이_상태를_바꾼다():
    """중지 세션을 완료로 바꾸면 타스크가 스스로 닫힌다 — 상태를 덮지 않고."""
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.finish(s, core.STOPPED, "여기까지")
    assert core.get_task(task.id).status == core.RUNNING

    after = core.set_session_status(task.id, s.id, core.DONE)
    assert after.status == core.DONE, "자동 규칙이 다시 센다"
    assert after.override is None, "타스크 상태를 덮지 않았다"
    assert after.sessions[0].by_human is True
    assert after.sessions[0].summary == "여기까지", "그 세션이 남긴 말은 지우지 않는다"

    back = core.set_session_status(task.id, s.id, core.STOPPED)
    assert back.status == core.RUNNING


def test_살아있는_세션은_사람이_못_바꾼다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)

    with pytest.raises(core.DuetError, match="살아 있는"):
        core.set_session_status(task.id, s.id, core.DONE)


def test_잘못된_세션_변경은_거부한다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.finish(s, core.STOPPED, "끝")

    with pytest.raises(core.DuetError):
        core.set_session_status(task.id, s.id, core.RUNNING)
    with pytest.raises(core.DuetError):
        core.set_session_status(task.id, "s-없는것", core.DONE)


def test_타스크를_닫으면_남은_세션이_중지로_적힌다():
    task = core.create_task("전환")
    a, b = core.register_session(), core.register_session()
    core.join(a, task.id, "끝낸 것")
    core.join(b, task.id, "안 끝낸 것")
    core.finish(a, core.DONE, "끝")
    _stale(b)  # 죽었는데 아직 안 걷힌 세션

    after = core.close_task(task.id)
    assert after.status == core.DONE
    assert after.override == core.DONE, "닫는 것은 사람이 정하는 일이다"
    stopped = [s for s in after.sessions if s.title == "안 끝낸 것"][0]
    assert stopped.status == core.STOPPED and stopped.by_human is True


def test_닫아도_살아있는_세션은_건드리지_않는다():
    """남의 프로세스를 죽이지 않는다. 그래도 타스크는 사람이 정한 완료로 남는다."""
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id, "도는 세션")

    after = core.close_task(task.id)
    assert after.status == core.DONE
    assert after.sessions[0].status == core.RUNNING, "그 파일의 주인은 그 프로세스다"


def test_보관하면_보드에서_빠지고_되돌리면_돌아온다():
    task = core.create_task("끝난 일")
    core.set_status(task.id, core.DONE)

    archived = core.archive_task(task.id)
    assert archived.dir.parent.name == core.ARCHIVE_DIRNAME
    assert core.list_tasks() == [], "보드에서 빠진다"
    assert [t.id for t in core.list_archived()] == [task.id]
    assert archived.title == "끝난 일", "파일은 그대로다"

    back = core.unarchive_task(task.id)
    assert back.dir.parent == core.home()
    assert [t.id for t in core.list_tasks()] == [task.id]
    assert core.list_archived() == []


def test_보관은_지우는_것이_아니다():
    task = core.create_task("끝난 일", "설명 줄")
    s = core.register_session()
    core.join(s, task.id, "세션 하나")
    core.report(s, "한 줄 남김")
    core.finish(s, core.DONE, "끝")

    archived = core.archive_task(task.id)
    assert (archived.dir / "task.md").is_file()
    assert archived.sessions[0].progress[0]["msg"] == "한 줄 남김"


def test_보관함에_없는_것은_못_꺼낸다():
    with pytest.raises(core.DuetError):
        core.unarchive_task(99)


def test_살아있는_세션이_있으면_보관하지_않는다():
    """폴더를 옮기면 그 프로세스가 기억하는 경로가 끊긴다."""
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)

    with pytest.raises(core.DuetError, match="먼저 닫고"):
        core.archive_task(task.id)


def test_닫은_타스크는_확인_필요에서_빠진다():
    """판단이 끝난 일이 계속 손을 요구하면 안 된다."""
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.id)
    core.finish(s, core.STOPPED, "여기까지")
    assert core.get_task(task.id).warning, "닫기 전에는 경고가 뜬다"

    assert core.close_task(task.id).warning == ""


def test_프로젝트는_세션이_뜬_폴더에서_저절로_정해진다():
    task = core.create_task("전환")
    assert task.project == "", "세션이 없으면 모른다"

    s = core.register_session("Claude Code", cwd=r"C:\project\nefss")
    core.join(s, task.id)
    assert core.get_task(task.id).project == "nefss"
    assert core.get_task(task.id).project_override == "", "파일에 적지 않았다"


def test_참여할_때_task_md를_건드리지_않는다():
    """프로젝트도 계산이다. 세션이 붙는다고 파일을 쓰면 여럿이 부딪힌다."""
    task = core.create_task("전환")
    before = (task.dir / "task.md").read_text(encoding="utf-8")

    s = core.register_session("Claude Code", cwd=r"C:\project\nefss")
    core.join(s, task.id)
    assert (task.dir / "task.md").read_text(encoding="utf-8") == before


def test_사람이_적은_프로젝트가_이긴다():
    task = core.create_task("전환", project="nefss")
    s = core.register_session("Claude Code", cwd=r"C:\project\다른것")
    core.join(s, task.id)

    assert core.get_task(task.id).project == "nefss"
    assert "프로젝트: nefss" in (task.dir / "task.md").read_text(encoding="utf-8")

    # 빈 문자열을 주면 그 줄이 지워지고 다시 센다
    after = core.update_task(task.id, project="")
    assert after.project == "다른것" and after.project_override == ""


def test_프로젝트로_볼_수_없는_폴더는_모른다고_둔다():
    assert core.project_name(r"C:\project\duet") == "duet"
    assert core.project_name("") == ""
    assert core.project_name(r"C:\Windows\System32") == ""
    assert core.project_name(str(pathlib.Path.home() / ".cache")) == ""


def test_제목을_고쳐도_프로젝트_줄은_남는다():
    task = core.create_task("옛 제목", project="nefss")
    after = core.update_task(task.id, title="새 제목")
    assert after.project_override == "nefss"

    core.set_status(task.id, core.DONE)
    assert core.get_task(task.id).project_override == "nefss"
