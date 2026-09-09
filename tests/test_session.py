"""프로세스 수명 = 세션 수명, 그리고 파일 자체의 규칙 (SPEC 4·5절)."""

from __future__ import annotations

import json

from duet import paths, sessionfile, store, taskfile
from duet.server import build_instructions
from duet.session import SessionHandle, label_client
from duet.states import SESSION_DONE, SESSION_IDLE, SESSION_STOPPED, TASK_DONE, TASK_RUNNING


def _handle(home):
    return SessionHandle(home, client="Claude Code", cwd="C:/project/duet")


def test_등록하면_미참여_세션_파일이_생긴다(home):
    h = _handle(home)
    h.register()

    path = sessionfile.path_for(paths.unassigned_dir(home), h.id)
    assert path.is_file()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == SESSION_IDLE
    assert data["client"] == "Claude Code"
    assert data["pid"] > 0
    assert data["progress"] == []


def test_보고_없이_끝나면_중지로_적힌다(home):
    h = _handle(home)
    h.register()
    task = store.create_task(home, "전환")
    store.join_task(home, h.file, task.id)

    h.stop()
    after = store.get_task(home, task.id)
    assert after.sessions[0].status == SESSION_STOPPED
    assert "닫힘" in after.sessions[0].summary
    assert after.status == TASK_RUNNING, "중지는 자동 완료를 막는다"


def test_완료_보고한_세션은_종료_훅이_덮지_않는다(home):
    h = _handle(home)
    h.register()
    task = store.create_task(home, "전환")
    store.join_task(home, h.file, task.id)
    store.close_session(home, h.file, SESSION_DONE, "끝")

    h.stop()
    after = store.get_task(home, task.id)
    assert after.sessions[0].status == SESSION_DONE
    assert after.status == TASK_DONE


def test_하트비트는_파일에_적힌다(home):
    h = _handle(home)
    h.register()
    before = h.file.heartbeat
    h.file.heartbeat = "2000-01-01T00:00:00"
    store.heartbeat(h.file)

    reread = sessionfile.read(h.file.path)
    assert reread.heartbeat >= before


def test_끝난_세션은_하트비트를_받지_않는다(home):
    h = _handle(home)
    h.register()
    store.close_session(home, h.file, SESSION_STOPPED, "끝")
    ended = h.file.heartbeat

    store.heartbeat(h.file)
    assert sessionfile.read(h.file.path).heartbeat == ended


def test_클라이언트_이름은_한_번만_확정한다(home):
    h = _handle(home)
    h.register()
    h.confirm_client("claude-code", "2.0.1")
    assert sessionfile.read(h.file.path).client == "Claude Code 2.0.1"

    h.confirm_client("cursor")
    assert sessionfile.read(h.file.path).client == "Claude Code 2.0.1", "첫 이름을 지킨다"


def test_클라이언트_이름_읽기(home):
    assert label_client("claude-ai") == "Claude Desktop"
    assert label_client("local-agent-mode-duet", "1.0.0") == "Claude Desktop"
    assert label_client(None) == "알 수 없음"
    assert label_client("모르는앱", "3.1") == "모르는앱 3.1"


def test_깨진_세션_파일은_건너뛴다(home):
    h = _handle(home)
    h.register()
    (paths.unassigned_dir(home) / "s-broken.json").write_text("{반쪽", encoding="utf-8")

    found = sessionfile.read_dir(paths.unassigned_dir(home))
    assert [s.id for s in found] == [h.id]


def test_사람이_고친_프론트매터_키를_지우지_않는다(home):
    task = store.create_task(home, "전환")
    path = paths.find_task_dir(home, task.id) / "task.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("created:", "메모: 손으로 적은 것\ncreated:"),
        encoding="utf-8",
    )

    store.set_task_status(home, task.id, "완료")
    assert "메모: 손으로 적은 것" in path.read_text(encoding="utf-8")


def test_사람이_적은_이상한_상태는_무시한다(home):
    task = store.create_task(home, "전환")
    tf = taskfile.read(paths.find_task_dir(home, task.id))
    tf.status_override = "아무거나"
    taskfile.write(tf)

    after = store.get_task(home, task.id)
    assert after.status == "대기", "계산값으로 돌아간다"
    assert not after.locked


def test_instructions에_열린_타스크가_들어간다(home):
    open_task = store.create_task(home, "pc101pm 전환")
    done = store.create_task(home, "끝난 일")
    store.set_task_status(home, done.id, TASK_DONE)
    h = _handle(home)
    h.register()
    store.join_task(home, h.file, open_task.id)

    text = build_instructions(home)
    assert f"[{open_task.id}] pc101pm 전환" in text
    assert "세션 1개 진행중" in text
    assert "끝난 일" not in text, "닫힌 타스크는 넣지 않는다"
    assert "join_task" in text


def test_열린_타스크가_없을_때의_instructions(home):
    assert "create_task" in build_instructions(home)


def test_대시보드가_고친_세션_상태를_종료_훅이_덮지_않는다(home):
    """세션이 도는 동안 사람이 `완료`로 바꿔 두면, 창을 닫아도 그대로 남아야 한다."""
    h = _handle(home)
    h.register()
    task = store.create_task(home, "전환")
    store.join_task(home, h.file, task.id)

    # 대시보드 쪽에서 파일을 직접 고친 상황
    outside = sessionfile.read(h.file.path)
    outside.status = SESSION_DONE
    outside.summary = "사람이 완료로 표시"
    sessionfile.write(outside)

    h.stop()
    after = store.get_task(home, task.id)
    assert after.sessions[0].status == SESSION_DONE
    assert after.sessions[0].summary == "사람이 완료로 표시"
    assert after.status == TASK_DONE
