"""규칙이 지켜지는지만 본다. 프로젝트 폴더, 자동 완료, 중지 처리, 사람이 정한 상태."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from duet import core


def _stale(session):
    """하트비트를 90초 밖으로 밀어 죽은 세션으로 만든다."""
    session.heartbeat = (datetime.now() - timedelta(seconds=200)).isoformat()
    session.save()


# --- 프로젝트 폴더와 번호 ---------------------------------------------------


def test_타스크는_프로젝트_폴더_안에_번호가_매겨진다():
    a = core.create_task("하나", project="duet")
    b = core.create_task("둘", project="duet")
    c = core.create_task("남의 것", project="nefss")

    assert (a.id, b.id, c.id) == ("T001", "T002", "T001"), "번호는 프로젝트 안에서 1부터"
    assert a.ref == "duet/T001" and c.ref == "nefss/T001"
    assert a.dir.parent == core.home() / "duet"
    assert (a.dir / "task.md").read_text(encoding="utf-8").startswith("# 하나")


def test_프로젝트를_모르면_미분류다():
    task = core.create_task("어디 것인지 모름")
    assert task.project == "" and task.ref == "_미분류/T001"
    assert task.dir.parent.name == core.UNSORTED_DIRNAME


def test_프로젝트가_붙은_이름은_거기서만_찾는다():
    core.create_task("하나", project="duet")
    core.create_task("하나", project="nefss")

    assert core.get_task("duet/T001").project == "duet"
    assert core.get_task("nefss/1").project == "nefss"
    with pytest.raises(core.DuetError, match="여러 프로젝트"):
        core.get_task("T001")
    assert core.get_task("T001", hint="nefss").project == "nefss", "힌트가 있으면 거기 것"
    with pytest.raises(core.DuetError):
        core.get_task("duet/T009")


def test_하나뿐이면_번호만으로도_찾는다():
    task = core.create_task("하나", project="duet")
    for given in ("T001", "t001", "1", 1):
        assert core.get_task(given).ref == task.ref
    with pytest.raises(core.DuetError):
        core.get_task("없는것")


def test_프로젝트_이름으로_못_쓰는_것():
    with pytest.raises(core.DuetError):
        core.create_task("x", project="_보관")
    with pytest.raises(core.DuetError):
        core.create_task("x", project="a/b")


def test_다른_프로젝트로_옮기면_번호가_새로_난다():
    core.create_task("먼저", project="nefss")
    task = core.create_task("옮길 것", project="duet")

    moved = core.update_task(task.ref, project="nefss")
    assert moved.ref == "nefss/T002"
    assert moved.title == "옮길 것"
    assert core.project_tasks("duet") == []


# --- 세션과 자동 규칙 -------------------------------------------------------


def test_참여하면_세션_파일이_타스크로_옮겨간다():
    task = core.create_task("전환", project="duet")
    s = core.register_session(cwd="C:/project/duet")
    assert s.path.parent.name == core.IDLE_DIRNAME

    after = core.join(s, "T001", "DBIO 계층 전환")  # 자기 폴더 안이라 번호만으로
    assert after.status == core.RUNNING
    assert s.path.parent == task.dir
    assert not list((core.home() / core.IDLE_DIRNAME).glob("s-*.json"))


def test_세션이_전부_완료면_타스크가_완료로_계산된다():
    task = core.create_task("전환")
    a = core.register_session()
    core.join(a, task.ref, "DBIO")
    assert core.finish(a, core.DONE, "DBIO 끝").status == core.DONE

    b = core.register_session()
    assert core.join(b, task.ref, "Bean").status == core.RUNNING, "이어받으면 다시 진행중"
    assert core.finish(b, core.DONE, "Bean 끝").status == core.DONE


def test_끊긴_세션이_남으면_확인_필요다():
    task = core.create_task("전환")
    a = core.register_session()
    core.join(a, task.ref)
    core.finish(a, core.DONE, "끝")
    b = core.register_session()
    core.join(b, task.ref)
    after = core.finish(b, core.STOPPED, "여기까지")

    assert after.status == core.ATTENTION
    assert "끊긴 세션 1개" in after.warning


def test_하트비트가_끊기면_중지로_친다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)
    _stale(s)

    assert core.get_task(task.ref).counts[core.STOPPED] == 1, "걷어내기 전에도 사실대로 센다"
    assert core.reap() == [s.id]
    assert core.reap() == [], "두 번 걷어내지 않는다"


def test_진행중인_타스크에는_같이_못_붙는다():
    task = core.create_task("전환")
    a = core.register_session("Claude Code")
    core.join(a, task.ref, "DBIO")
    b = core.register_session("Claude Desktop")
    with pytest.raises(core.DuetError, match="이미 살아 있는 세션"):
        core.join(b, task.ref, "Bean")

    core.finish(a, core.DONE, "끝")
    assert core.join(b, task.ref, "Bean").status == core.RUNNING


def test_보고_전이면_다른_타스크로_옮길_수_있다():
    first, second = core.create_task("하나"), core.create_task("둘")
    s = core.register_session()
    core.join(s, first.ref)
    core.join(s, second.ref)
    assert core.get_task(first.ref).sessions == []

    core.report(s, "둘에서 한 줄")
    with pytest.raises(core.DuetError, match="보고까지 했다"):
        core.join(s, first.ref)


def test_먼저_적힌_종료가_남는다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)
    core.finish(s, core.DONE, "끝")
    core.finish(s, core.STOPPED, "창 닫힘")
    assert core.get_task(task.ref).sessions[0].status == core.DONE


def test_진행_로그는_세션_파일에_쌓인다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)
    core.report(s, " 스키마 훑음 ", 20)
    core.report(s, "DBIO 3개 전환", 60)
    assert [e["msg"] for e in core.get_task(task.ref).sessions[0].progress] == ["스키마 훑음", "DBIO 3개 전환"]
    with pytest.raises(core.DuetError):
        core.report(s, "  ")


# --- 사람이 정하는 것 -------------------------------------------------------


def test_사람이_적은_상태가_이긴다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)

    assert core.set_status(task.ref, core.HOLD).status == core.HOLD
    core.finish(s, core.DONE, "끝")
    assert core.get_task(task.ref).status == core.HOLD, "자동 규칙이 덮지 않는다"
    assert core.set_status(task.ref, None).status == core.DONE, "지우면 다시 센다"


def test_사람은_완료_보류_취소만_고른다():
    task = core.create_task("전환")
    for bad in (core.WAITING, core.RUNNING, core.ATTENTION, "아무거나"):
        with pytest.raises(core.DuetError):
            core.set_status(task.ref, bad)


def test_사람이_손으로_고쳐도_읽는다():
    task = core.create_task("전환", "설명 줄")
    (task.dir / "task.md").write_text("# 새 제목\n상태: 완료\n\n고친 설명\n", encoding="utf-8")
    after = core.get_task(task.ref)
    assert (after.title, after.status, after.description) == ("새 제목", "완료", "고친 설명")


def test_끝난_세션은_사람이_상태를_바꾼다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)
    core.finish(s, core.STOPPED, "여기까지")
    assert core.get_task(task.ref).status == core.ATTENTION

    after = core.set_session_status(task.ref, s.id, core.DONE)
    assert after.status == core.DONE and after.override is None, "덮지 않고 다시 센 것"
    assert after.sessions[0].by_human is True


def test_살아있는_세션은_사람이_못_바꾼다():
    task = core.create_task("전환")
    s = core.register_session()
    core.join(s, task.ref)
    with pytest.raises(core.DuetError, match="살아 있는"):
        core.set_session_status(task.ref, s.id, core.DONE)


def test_제목을_고쳐도_폴더와_상태는_그대로다():
    task = core.create_task("옛 제목", "옛 설명", project="duet")
    core.set_status(task.ref, core.HOLD)
    after = core.update_task(task.ref, title="새 제목")
    assert (after.title, after.description, after.dir.name) == ("새 제목", "옛 설명", task.dir.name)
    assert after.override == core.HOLD
    with pytest.raises(core.DuetError):
        core.update_task(task.ref, title="   ")


# --- 보관은 프로젝트 폴더째 ---------------------------------------------------


def test_보관은_프로젝트_폴더째다():
    a = core.create_task("하나", project="nefss")
    b = core.create_task("둘", project="nefss")
    other = core.create_task("남의 것", project="duet")

    moved = core.archive_project("nefss")
    assert sorted(t.id for t in moved) == ["T001", "T002"]
    assert [t.ref for t in core.list_tasks()] == [other.ref]
    assert (core.archive_dir() / "nefss" / a.dir.name).is_dir()
    assert sorted(t.ref for t in core.list_archived()) == [a.ref, b.ref]

    back = core.unarchive_project("nefss")
    assert len(back) == 2 and core.list_archived() == []


def test_살아있는_세션이_있으면_프로젝트를_보관하지_않는다():
    core.create_task("끝난 것", project="nefss")
    live = core.create_task("도는 것", project="nefss")
    s = core.register_session()
    core.join(s, live.ref)
    with pytest.raises(core.DuetError, match="살아 있는 세션"):
        core.archive_project("nefss")
    assert len(core.list_tasks()) == 2


def test_프로젝트를_지운다():
    core.create_task("하나", project="nefss")
    live = core.create_task("도는 것", project="nefss")
    core.create_task("남의 것", project="duet")
    s = core.register_session()
    core.join(s, live.ref)
    with pytest.raises(core.DuetError, match="살아 있는 세션"):
        core.delete_project("nefss")
    core.finish(s, core.DONE, "끝")
    assert core.delete_project("nefss") == "nefss"
    assert core.projects() == ["duet"]


def test_타스크를_지운다():
    task = core.create_task("잘못 만든 것", project="nefss")
    keep = core.create_task("남길 것", project="nefss")
    assert core.delete_task(task.ref) == "nefss/T001"
    assert not task.dir.exists()
    assert [t.ref for t in core.list_tasks()] == [keep.ref]


def test_살아있는_세션이_붙은_타스크는_못_지운다():
    task = core.create_task("도는 것", project="nefss")
    s = core.register_session()
    core.join(s, task.ref)
    with pytest.raises(core.DuetError, match="살아 있는 세션"):
        core.delete_task(task.ref)
    core.finish(s, core.DONE, "끝")
    core.delete_task(task.ref)
    assert not task.dir.exists()


# --- 그 밖 ------------------------------------------------------------------


def test_비슷한_타스크를_찾아준다():
    core.create_task("대시보드 만들기", project="duet")
    done = core.create_task("세션 타임라인 붙이기", project="duet")
    core.set_status(done.ref, core.DONE)
    assert [t.title for t in core.similar_tasks("대시보드 필터 만들기")] == ["대시보드 만들기"]
    assert core.similar_tasks("세션 타임라인 고치기") == [], "닫힌 것은 후보가 아니다"


def test_깨진_파일_하나가_목록을_막지_않는다():
    good = core.create_task("멀쩡한 것", project="duet")
    (core.home() / core.IDLE_DIRNAME / "s-broken.json").write_text("{반쪽", encoding="utf-8")
    assert [t.ref for t in core.list_tasks()] == [good.ref]


def test_프로젝트_이름은_세션이_뜬_폴더에서_나온다():
    assert core.project_name("C:/project/duet") == "duet"
    assert core.project_name("") == ""
    assert core.project_name("C:/Windows/System32") == ""
    (core.home() / core.ALIAS_FILENAME).write_text("C:/project/bookshelf = 서재\n", encoding="utf-8")
    assert core.project_name("C:\\project\\bookshelf") == "서재"


def test_옛_평평한_배치는_처음_뜰_때_프로젝트_폴더로_옮겨진다():
    base = core.home()
    # 옛 방식: 루트 바로 아래 T00n, `프로젝트:` 줄 또는 세션 cwd로 프로젝트를 알았다
    (base / "T001-옛것").mkdir()
    (base / "T001-옛것" / "task.md").write_text("# 옛것\n프로젝트: nefss\n\n설명\n", encoding="utf-8")
    (base / "T002-모름").mkdir()
    (base / "T002-모름" / "task.md").write_text("# 모름\n\n", encoding="utf-8")

    core._migrated.discard(base)
    core.home()

    refs = sorted(t.ref for t in core.list_tasks())
    assert refs == ["_미분류/T001", "nefss/T001"]
    assert "프로젝트:" not in (base / "nefss" / "T001-옛것" / "task.md").read_text(encoding="utf-8")
