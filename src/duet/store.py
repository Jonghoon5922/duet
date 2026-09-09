"""타스크·세션 도메인 규칙.

MCP 도구도 CLI도 대시보드도 전부 이 모듈을 거친다. 상태 규칙이 한 곳에만 있어야
"세션이 보고했을 때"와 "사람이 눌렀을 때"가 어긋나지 않는다.

핵심 (SPEC 3·9절):
- **타스크 상태는 저장하지 않고 계산한다.** 세션 파일들을 읽어 규칙을 적용한다.
- **자동 완료**: 세션이 1개 이상이고 전부 `완료`일 때만 `완료`. 하나라도 `중지`면
  `진행중`을 유지한다 — 실패한 세션을 완료로 뭉개지 않는다.
- **수동 우선**: 사람이 정한 값은 `task.md`의 `status_override`에 남고, 계산값을 덮는다.

**누가 무엇을 쓰는가** (충돌이 없는 이유):
- 세션 파일 → 그 세션의 프로세스만. 죽은 세션을 `중지`로 적을 때만 예외다.
- `task.md` → 사람·대시보드·`update_task`만. 세션이 참여·보고할 때는 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import paths, sessionfile, taskfile
from .sessionfile import SessionFile
from .states import (
    ACTOR_HUMAN,
    ACTOR_SESSION,
    SESSION_CLOSED,
    SESSION_DONE,
    SESSION_RUNNING,
    SESSION_STOPPED,
    TASK_DONE,
    TASK_RUNNING,
    TASK_STATUSES,
    TASK_WAITING,
    now,
)
from .taskfile import TaskFile, TaskFileError


class StoreError(Exception):
    """규칙에 어긋난 요청. 도구가 그대로 사용자에게 보여줄 수 있는 문장이어야 한다."""


@dataclass(frozen=True)
class SessionCounts:
    running: int = 0
    done: int = 0
    stopped: int = 0

    @property
    def total(self) -> int:
        return self.running + self.done + self.stopped


@dataclass(frozen=True)
class Task:
    id: str
    title: str
    description: str
    status: str
    locked: bool
    tags: list[str]
    created: str
    dirname: str
    counts: SessionCounts = field(default_factory=SessionCounts)
    last_activity: str = ""
    sessions: list[SessionFile] = field(default_factory=list)
    history: list[str] = field(default_factory=list)

    @property
    def progress(self) -> int:
        """진행률(%) = 완료 세션 비율. 세션이 없으면 0."""
        total = self.counts.total
        return round(100 * self.counts.done / total) if total else 0

    @property
    def warning(self) -> str:
        """사람이 봐야 하는 상태 한 줄. 없으면 빈 문자열."""
        if self.counts.stopped and not self.counts.running:
            return f"중지된 세션 {self.counts.stopped}개 — 이어서 할지 사람이 정한다"
        return ""

    def to_dict(self, with_sessions: bool = False) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "locked": self.locked,
            "tags": self.tags,
            "sessions": {
                "진행중": self.counts.running,
                "완료": self.counts.done,
                "중지": self.counts.stopped,
            },
            "progress": self.progress,
            "created": self.created,
            "last_activity": self.last_activity,
        }
        if self.warning:
            data["warning"] = self.warning
        if with_sessions:
            data["session_list"] = [s.to_view() for s in self.sessions]
            data["history"] = self.history
        return data


# --- 상태 계산 -------------------------------------------------------------


def effective_status(session: SessionFile) -> str:
    """파일에 적힌 상태와 하트비트를 함께 본 상태.

    `진행중`이라 적혀 있어도 하트비트가 끊겼으면 죽은 것이다. 걷어내기(reap) 전에도
    읽는 쪽은 사실대로 봐야 한다.
    """
    if session.status in SESSION_CLOSED:
        return session.status
    return SESSION_RUNNING if session.alive else SESSION_STOPPED


def count_sessions(sessions: list[SessionFile]) -> SessionCounts:
    running = done = stopped = 0
    for s in sessions:
        status = effective_status(s)
        if status == SESSION_DONE:
            done += 1
        elif status == SESSION_STOPPED:
            stopped += 1
        else:
            running += 1
    return SessionCounts(running=running, done=done, stopped=stopped)


def compute_status(counts: SessionCounts) -> str:
    """자동 규칙. 저장하지 않고 읽을 때마다 계산한다."""
    if counts.total == 0:
        return TASK_WAITING
    if counts.running:
        return TASK_RUNNING
    if counts.stopped:
        return TASK_RUNNING  # 중지가 섞였다 — 사람이 볼 때까지 열어 둔다
    return TASK_DONE


# --- 타스크 읽기 -----------------------------------------------------------


def _last_activity(task: TaskFile, sessions: list[SessionFile]) -> str:
    stamps = [task.created]
    for s in sessions:
        stamps.append(s.completed or s.heartbeat or s.started)
    if task.history:
        stamps.append(task.history[-1].split(" ", 1)[0])
    return max(x for x in stamps if x) if any(stamps) else ""


def _load(home: Path, task_dir: Path, with_sessions: bool = True) -> Task:
    tf = taskfile.read(task_dir)
    sessions = sessionfile.read_dir(paths.sessions_dir(task_dir))
    counts = count_sessions(sessions)
    status = tf.status_override or compute_status(counts)
    return Task(
        id=tf.id,
        title=tf.title,
        description=tf.description,
        status=status,
        locked=tf.locked,
        tags=tf.tags,
        created=tf.created,
        dirname=task_dir.name,
        counts=counts,
        last_activity=_last_activity(tf, sessions),
        sessions=sessions if with_sessions else [],
        history=tf.history,
    )


def get_task(home: Path, task_id: str | int) -> Task:
    try:
        task_dir = paths.find_task_dir(home, task_id)
    except paths.PathError as e:
        raise StoreError(str(e)) from e
    try:
        return _load(home, task_dir)
    except TaskFileError as e:
        raise StoreError(str(e)) from e


def list_tasks(home: Path, status: str | None = None) -> list[Task]:
    """타스크 목록. 마지막 활동이 늦은 순.

    읽을 때마다 죽은 세션을 먼저 걷어낸다 — 종료 훅이 못 돌았을 때의 그물이다.
    """
    reap_stale_sessions(home)
    if status and status not in TASK_STATUSES:
        raise StoreError(f"모르는 타스크 상태다: {status} (가능: {', '.join(TASK_STATUSES)})")

    tasks = []
    for task_dir in paths.task_dirs(home):
        try:
            task = _load(home, task_dir, with_sessions=False)
        except TaskFileError:
            continue  # 사람이 고치다 깨뜨린 파일 하나가 목록 전체를 막지 않게
        if status and task.status != status:
            continue
        tasks.append(task)

    tasks.sort(key=lambda t: (t.last_activity, t.id), reverse=True)
    return tasks


# --- 타스크 쓰기 (사람·update_task만) --------------------------------------


def create_task(
    home: Path, title: str, description: str = "", tags: list[str] | None = None
) -> Task:
    title = title.strip()
    if not title:
        raise StoreError("타스크 제목이 비어 있다.")
    task_dir = paths.next_task_dir(home, title)
    taskfile.create(task_dir, paths.task_id_of(task_dir), title, description.strip(), tags)
    return _load(home, task_dir)


def set_task_status(
    home: Path, task_id: str | int, status: str, *, actor: str = ACTOR_HUMAN, note: str = ""
) -> Task:
    """사람이 상태를 정한다. `status_override`에 적히고, 그것이 곧 수동 잠금이다."""
    if status not in TASK_STATUSES:
        raise StoreError(f"모르는 타스크 상태다: {status} (가능: {', '.join(TASK_STATUSES)})")
    task = get_task(home, task_id)
    tf = taskfile.read(paths.find_task_dir(home, task_id))
    before = task.status
    tf.status_override = status
    line = f"{before} → {status}" + (f" ({note})" if note else "")
    taskfile.add_history(tf, actor, line, write_now=False)
    taskfile.write(tf)
    return get_task(home, task_id)


def unlock_task(home: Path, task_id: str | int) -> Task:
    """수동 잠금을 푼다. 다시 세션에서 계산한 상태로 돌아간다."""
    task_dir = paths.find_task_dir(home, task_id)
    tf = taskfile.read(task_dir)
    if tf.status_override is None:
        return get_task(home, task_id)
    tf.status_override = None
    taskfile.add_history(tf, ACTOR_HUMAN, "수동 잠금 해제 (다시 자동 판정)", write_now=False)
    taskfile.write(tf)
    return get_task(home, task_id)


def update_task(
    home: Path,
    task_id: str | int,
    *,
    title: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    actor: str = ACTOR_SESSION,
) -> Task:
    """제목·설명·태그를 고친다. 폴더 이름은 그대로 둔다 (id가 판별자다)."""
    task_dir = paths.find_task_dir(home, task_id)
    tf = taskfile.read(task_dir)
    changed = []
    if title is not None and title.strip() and title.strip() != tf.title:
        changed.append(f"제목 → {title.strip()}")
        tf.title = title.strip()
    if description is not None and description.strip() != tf.description:
        changed.append("설명 고침")
        tf.description = description.strip()
    if tags is not None and [str(t) for t in tags] != tf.tags:
        changed.append(f"태그 → {', '.join(tags) or '없음'}")
        tf.tags = [str(t) for t in tags]

    if changed:
        taskfile.add_history(tf, actor, ", ".join(changed), write_now=False)
        taskfile.write(tf)
    return get_task(home, task_id)


# --- 세션 -----------------------------------------------------------------


def register_session(
    home: Path, session_id: str, *, client: str = "알 수 없음", pid: int = 0, cwd: str = ""
) -> SessionFile:
    """서버 프로세스가 뜰 때 자기 세션 파일을 `unassigned/`에 만든다."""
    return sessionfile.create(
        paths.unassigned_dir(home), session_id, client=client, pid=pid, cwd=cwd
    )


def join_task(home: Path, session: SessionFile, task_id: str | int, title: str = "") -> Task:
    """세션 파일을 타스크 폴더로 옮긴다.

    `task.md`는 건드리지 않는다. 타스크가 `진행중`이 되는 것은 세션 파일이 거기 있기
    때문이지 어딘가에 그렇게 적어서가 아니다 — 그래서 여러 세션이 동시에 참여해도
    부딪히지 않는다.
    """
    if session.status in SESSION_CLOSED:
        raise StoreError("이미 끝난 세션이다. 새 세션에서 참여하라.")
    task = get_task(home, task_id)
    task_dir = paths.find_task_dir(home, task_id)

    session.title = title.strip() or session.title or task.title
    session.status = SESSION_RUNNING
    session.heartbeat = now()
    sessionfile.move(session, paths.sessions_dir(task_dir))
    return get_task(home, task_id)


def heartbeat(session: SessionFile) -> None:
    """살아 있다고 알린다. 끝난 세션은 건드리지 않는다."""
    if session.status in SESSION_CLOSED:
        return
    session.heartbeat = now()
    sessionfile.write(session)


def report_progress(
    home: Path, session: SessionFile, message: str, percent: int | None = None
) -> dict[str, Any]:
    """진행 로그 한 줄. 이 도구가 남기는 글은 전부 Claude가 직접 쓴 것이다."""
    message = message.strip()
    if not message:
        raise StoreError("진행 내용이 비어 있다.")
    if percent is not None and not 0 <= percent <= 100:
        raise StoreError("percent는 0~100이다.")
    if session.status in SESSION_CLOSED:
        raise StoreError("이미 끝난 세션이다. 보고할 수 없다.")

    entry = {"t": now(), "msg": message, "pct": percent}
    session.progress.append(entry)
    session.heartbeat = entry["t"]
    sessionfile.write(session)
    return entry


def close_session(
    home: Path, session: SessionFile, status: str, summary: str = ""
) -> Task | None:
    """세션을 끝낸다(`완료` 또는 `중지`). 타스크 상태는 다음에 읽을 때 계산된다."""
    if status not in SESSION_CLOSED:
        raise StoreError("세션을 끝내는 상태는 완료 또는 중지뿐이다.")

    # 파일을 다시 읽어 확인한다. 이 세션이 도는 동안 대시보드가 상태를 고쳤을 수 있고,
    # 그때 프로세스가 들고 있던 옛 상태로 덮으면 사람이 한 일이 지워진다.
    if session.path is not None:
        on_disk = sessionfile.read(session.path)
        if on_disk is not None and on_disk.status in SESSION_CLOSED:
            session.status = on_disk.status
            session.summary = on_disk.summary
            session.completed = on_disk.completed
            return _task_of(home, session)
    if session.status in SESSION_CLOSED:
        # 종료 훅과 사람 조작이 겹칠 수 있다. 먼저 적힌 쪽을 남긴다.
        return _task_of(home, session)

    session.status = status
    session.summary = summary.strip() or None
    session.completed = now()
    sessionfile.write(session)
    return _task_of(home, session)


def _task_of(home: Path, session: SessionFile) -> Task | None:
    """세션 파일이 놓인 폴더로 소속 타스크를 안다. 파일 위치가 곧 소속이다."""
    if session.path is None:
        return None
    task_dir = session.path.parent.parent
    if task_dir.parent != paths.tasks_dir(home):
        return None
    try:
        return _load(home, task_dir)
    except (TaskFileError, paths.PathError):
        return None


def session_task_id(home: Path, session: SessionFile) -> str | None:
    task = _task_of(home, session)
    return task.id if task else None


def reap_stale_sessions(home: Path) -> list[str]:
    """하트비트가 90초 넘게 끊긴 세션을 `중지`로 적는다.

    세션 파일은 그 프로세스만 쓴다는 규칙의 유일한 예외다. 쓸 프로세스가 이미
    없기 때문에 허용된다 — 종료 훅이 못 돈 경우(크래시·강제 종료·정전)의 그물이다.
    """
    stale = []
    directories = [paths.unassigned_dir(home)]
    directories += [paths.sessions_dir(d) for d in paths.task_dirs(home)]

    for directory in directories:
        for session in sessionfile.read_dir(directory):
            if session.status in SESSION_CLOSED or session.alive:
                continue
            session.status = SESSION_STOPPED
            session.summary = session.summary or "하트비트 끊김 (프로세스 종료 추정)"
            session.completed = now()
            try:
                sessionfile.write(session)
            except OSError:
                continue
            stale.append(session.id)
    return stale


def list_sessions(home: Path, task_id: str | int | None = None) -> list[tuple[str | None, SessionFile]]:
    """(타스크 id, 세션) 목록. 타스크에 안 붙은 세션은 id가 None이다."""
    reap_stale_sessions(home)
    out: list[tuple[str | None, SessionFile]] = []
    if task_id is None:
        for session in sessionfile.read_dir(paths.unassigned_dir(home)):
            out.append((None, session))
        task_dirs = paths.task_dirs(home)
    else:
        task_dirs = [paths.find_task_dir(home, task_id)]

    for task_dir in task_dirs:
        tid = paths.task_id_of(task_dir)
        for session in sessionfile.read_dir(paths.sessions_dir(task_dir)):
            out.append((tid, session))
    return out
