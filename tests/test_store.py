"""1단계 규칙 시험: 참여 → 보고 → 완료 → 타스크 자동 완료, 그리고 중지 처리."""

from __future__ import annotations

import pytest

from duet import paths, sessionfile, store, taskfile
from duet.states import (
    SESSION_DONE,
    SESSION_IDLE,
    SESSION_RUNNING,
    SESSION_STOPPED,
    TASK_CANCELLED,
    TASK_DONE,
    TASK_RUNNING,
    TASK_WAITING,
)


def _session(home, sid="s-aaaa"):
    return store.register_session(home, sid, client="Claude Code", pid=1234)


def _go_stale(session, seconds=200):
    """하트비트를 과거로 돌린다. 90초 넘으면 죽은 것으로 친다."""
    from datetime import datetime, timedelta

    stamp = datetime.fromisoformat(session.heartbeat) - timedelta(seconds=seconds)
    session.heartbeat = stamp.isoformat(timespec="seconds")
    sessionfile.write(session)


def test_새_타스크는_대기다(home):
    task = store.create_task(home, "  pc101pm 전환  ", "DBIO부터", ["nefss"])
    assert task.id == "T001"
    assert task.title == "pc101pm 전환"
    assert task.status == TASK_WAITING
    assert task.tags == ["nefss"]
    assert task.counts.total == 0
    assert not task.locked


def test_타스크_폴더와_파일이_생긴다(home):
    task = store.create_task(home, "pc101pm 전환")
    task_dir = paths.tasks_dir(home) / task.dirname
    assert task_dir.name == "T001-pc101pm-전환"
    assert (task_dir / "task.md").is_file()
    assert paths.sessions_dir(task_dir).is_dir()

    text = (task_dir / "task.md").read_text(encoding="utf-8")
    assert "id: T001" in text
    assert "status_override: null" in text, "타스크 상태는 저장하지 않는다"
    assert "## 이력" in text


def test_번호는_이어서_붙는다(home):
    assert store.create_task(home, "하나").id == "T001"
    assert store.create_task(home, "둘").id == "T002"
    assert store.create_task(home, "셋").id == "T003"


def test_빈_제목은_거부한다(home):
    with pytest.raises(store.StoreError):
        store.create_task(home, "   ")


def test_세션은_미참여로_등록된다(home):
    s = _session(home)
    assert s.status == SESSION_IDLE
    assert s.alive
    assert s.path.parent == paths.unassigned_dir(home)


def test_참여하면_세션_파일이_타스크로_옮겨간다(home):
    task = store.create_task(home, "전환")
    s = _session(home)
    after = store.join_task(home, s, task.id, "DBIO 계층 전환")

    assert after.status == TASK_RUNNING
    assert after.counts.running == 1
    assert s.status == SESSION_RUNNING
    assert s.title == "DBIO 계층 전환"
    assert s.path.parent == paths.sessions_dir(paths.find_task_dir(home, task.id))
    assert not sessionfile.path_for(paths.unassigned_dir(home), s.id).exists()


def test_참여는_task_md를_건드리지_않는다(home):
    """세션이 여럿 동시에 붙어도 부딪히지 않는 이유다 (SPEC 5절)."""
    task = store.create_task(home, "전환")
    path = paths.find_task_dir(home, task.id) / "task.md"
    before = path.read_text(encoding="utf-8")

    store.join_task(home, _session(home), task.id)
    assert path.read_text(encoding="utf-8") == before


def test_id_표기가_흔들려도_찾는다(home):
    task = store.create_task(home, "전환")
    for given in ("T001", "t001", "1", 1, "T1"):
        assert store.get_task(home, given).id == task.id


def test_세션이_전부_완료면_타스크가_완료로_계산된다(home):
    task = store.create_task(home, "전환")
    a, b = _session(home, "s-a"), _session(home, "s-b")
    store.join_task(home, a, task.id, "DBIO")
    store.join_task(home, b, task.id, "Bean")

    after_first = store.close_session(home, a, SESSION_DONE, "DBIO 끝")
    assert after_first.status == TASK_RUNNING, "아직 b가 남았다"

    after_second = store.close_session(home, b, SESSION_DONE, "Bean 끝")
    assert after_second.status == TASK_DONE
    assert after_second.progress == 100
    assert not after_second.locked, "자동 완료는 잠그지 않는다"


def test_중지된_세션이_있으면_완료로_계산하지_않는다(home):
    task = store.create_task(home, "전환")
    a, b = _session(home, "s-a"), _session(home, "s-b")
    store.join_task(home, a, task.id)
    store.join_task(home, b, task.id)

    store.close_session(home, a, SESSION_DONE, "끝")
    after = store.close_session(home, b, SESSION_STOPPED, "여기까지")

    assert after.status == TASK_RUNNING, "사람이 판단하도록 열어 둔다"
    assert after.counts.stopped == 1
    assert "중지된 세션 1개" in after.warning


def test_세션이_없는_타스크는_대기다(home):
    task = store.create_task(home, "빈 타스크")
    assert store.get_task(home, task.id).status == TASK_WAITING


def test_수동_잠금은_자동_규칙보다_우선한다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)

    locked = store.set_task_status(home, task.id, TASK_WAITING)
    assert locked.locked and locked.status == TASK_WAITING

    store.close_session(home, a, SESSION_DONE, "끝")
    assert store.get_task(home, task.id).status == TASK_WAITING, "잠긴 타스크를 덮지 않는다"


def test_잠금을_풀면_다시_계산된다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)
    store.set_task_status(home, task.id, TASK_WAITING)
    store.close_session(home, a, SESSION_DONE, "끝")

    after = store.unlock_task(home, task.id)
    assert not after.locked
    assert after.status == TASK_DONE


def test_취소한_타스크는_세션이_되살리지_못한다(home):
    task = store.create_task(home, "전환")
    store.set_task_status(home, task.id, TASK_CANCELLED)
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)

    assert store.get_task(home, task.id).status == TASK_CANCELLED


def test_수동_변경은_이력에_남는다(home):
    task = store.create_task(home, "전환")
    store.set_task_status(home, task.id, TASK_DONE, note="손으로 마무리")
    tf = taskfile.read(paths.find_task_dir(home, task.id))

    assert any("생성" in line for line in tf.history)
    assert any("대기 → 완료 (손으로 마무리)" in line and "사람" in line for line in tf.history)


def test_진행_로그는_세션_파일에_쌓인다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)

    store.report_progress(home, a, " 스키마 훑음 ", 20)
    store.report_progress(home, a, "DBIO 3개 전환", 60)

    reloaded = store.get_task(home, task.id).sessions[0]
    assert [e["msg"] for e in reloaded.progress] == ["스키마 훑음", "DBIO 3개 전환"]
    assert reloaded.percent == 60


def test_참여_전_보고도_참여할_때_따라간다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.report_progress(home, a, "먼저 훑는 중")
    assert store.session_task_id(home, a) is None

    store.join_task(home, a, task.id)
    assert store.session_task_id(home, a) == task.id
    assert store.get_task(home, task.id).sessions[0].progress[0]["msg"] == "먼저 훑는 중"


def test_잘못된_보고는_거부한다(home):
    a = _session(home)
    with pytest.raises(store.StoreError):
        store.report_progress(home, a, "   ")
    with pytest.raises(store.StoreError):
        store.report_progress(home, a, "됐다", 120)


def test_끝난_세션은_더_보고하지_못한다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)
    store.close_session(home, a, SESSION_DONE, "끝")

    with pytest.raises(store.StoreError):
        store.report_progress(home, a, "하나 더")
    with pytest.raises(store.StoreError):
        store.join_task(home, a, task.id)


def test_먼저_적힌_종료가_남는다(home):
    """complete_session 뒤에 종료 훅이 돌아도 `완료`를 `중지`로 덮지 않는다."""
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)
    store.close_session(home, a, SESSION_DONE, "끝")

    store.close_session(home, a, SESSION_STOPPED, "창 닫힘")
    assert store.get_task(home, task.id).sessions[0].status == SESSION_DONE
    assert store.get_task(home, task.id).status == TASK_DONE


def test_하트비트가_끊긴_세션은_읽는_순간_죽은_것으로_센다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)
    _go_stale(a)

    # 걷어내기 전에도 계산은 사실대로다
    read_back = store.get_task(home, task.id)
    assert read_back.counts.stopped == 1
    assert read_back.status == TASK_RUNNING


def test_걷어내면_세션_파일에_중지가_적힌다(home):
    task = store.create_task(home, "전환")
    a = _session(home, "s-a")
    store.join_task(home, a, task.id)
    _go_stale(a)

    assert store.reap_stale_sessions(home) == [a.id]
    after = store.get_task(home, task.id).sessions[0]
    assert after.status == SESSION_STOPPED
    assert "하트비트" in after.summary
    assert store.reap_stale_sessions(home) == [], "두 번 걷어내지 않는다"


def test_참여하지_않은_죽은_세션도_걷어낸다(home):
    a = _session(home, "s-a")
    _go_stale(a)
    assert store.reap_stale_sessions(home) == [a.id]


def test_살아있는_세션은_걷어내지_않는다(home):
    _session(home, "s-a")
    assert store.reap_stale_sessions(home) == []


def test_목록은_마지막_활동순이다(home):
    first = store.create_task(home, "먼저")
    store.create_task(home, "나중")
    a = _session(home, "s-a")
    store.join_task(home, a, first.id)
    store.report_progress(home, a, "먼저 것을 건드렸다")

    assert [t.id for t in store.list_tasks(home)][0] == first.id


def test_상태로_거른다(home):
    done = store.create_task(home, "끝난 것")
    store.create_task(home, "안 끝난 것")
    store.set_task_status(home, done.id, TASK_DONE)

    assert [t.id for t in store.list_tasks(home, status=TASK_DONE)] == [done.id]
    with pytest.raises(store.StoreError):
        store.list_tasks(home, status="아무거나")


def test_없는_타스크는_또렷하게_거부한다(home):
    with pytest.raises(store.StoreError) as e:
        store.get_task(home, 99)
    assert "T099" in str(e.value)


def test_깨진_타스크_하나가_목록을_막지_않는다(home):
    good = store.create_task(home, "멀쩡한 것")
    broken = store.create_task(home, "망가진 것")
    (paths.find_task_dir(home, broken.id) / "task.md").write_text(
        "---\n제목: [엉망\n---\n", encoding="utf-8"
    )

    assert [t.id for t in store.list_tasks(home)] == [good.id]


def test_제목을_고쳐도_폴더는_그대로다(home):
    task = store.create_task(home, "옛 제목")
    after = store.update_task(home, task.id, title="새 제목", tags=["nefss"])

    assert after.title == "새 제목"
    assert after.dirname == task.dirname, "id가 판별자다"
    assert after.tags == ["nefss"]
