"""Duet 핵심 — 폴더와 파일, 그리고 상태 규칙.

```
~/.duet/
├── T001-pc101pm-전환/
│   ├── task.md            제목·설명. 사람이 열어 고치는 파일
│   ├── s-a1b2c3d4.json    세션 A (그 프로세스만 쓴다)
│   └── s-e5f6a7b8.json    세션 B
└── _미참여/
    └── s-9f8e7d6c.json    아직 join 안 한 세션
```

규칙 두 개가 전부다:

1. **타스크 상태는 저장하지 않고 센다.** 폴더 안 세션 파일을 세서 정한다.
   전부 `완료`면 `완료`, 하나라도 살아 있으면 `진행중`, 중지가 섞이면 `진행중`으로
   열어 둔다 (실패한 세션을 완료로 뭉개지 않는다).
2. **사람이 정한 것이 이긴다.** `task.md`에 `상태: 완료` 한 줄이 있으면 그게 상태다.
   그 줄을 지우면 다시 센다.

한 파일에 두 주인이 없다. 세션 파일은 그 세션의 프로세스만 쓰고, `task.md`는
사람만 쓴다. 그래서 락이 없다.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

HOME_ENV = "DUET_HOME"
IDLE_DIRNAME = "_미참여"  # 밑줄을 붙여 탐색기에서 타스크들 위에 고정되게 한다

HEARTBEAT_SEC = 30.0
STALE_SEC = 90.0  # 이만큼 하트비트가 없으면 죽은 세션이다

WAITING, RUNNING, DONE, STOPPED = "대기", "진행중", "완료", "중지"
TASK_STATUSES = (WAITING, RUNNING, DONE, STOPPED, "취소")
CLOSED = (DONE, STOPPED)

TASK_DIR = re.compile(r"^(T\d{3,})(?:-.*)?$")
STATUS_LINE = re.compile(r"^상태:\s*(\S+)\s*$", re.MULTILINE)
BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class DuetError(Exception):
    """사용자에게 그대로 보여줄 수 있는 문장이어야 한다."""


def now() -> str:
    return datetime.now().isoformat(timespec="milliseconds")


def age(stamp: str) -> float:
    """그 시각에서 지금까지 몇 초. 읽을 수 없으면 아주 오래된 것으로 친다."""
    try:
        return (datetime.now() - datetime.fromisoformat(stamp)).total_seconds()
    except (TypeError, ValueError):
        return float("inf")


def home() -> Path:
    """`~/.duet/`. 테스트와 여러 벌 운용을 위해 DUET_HOME으로만 바꾼다."""
    override = os.environ.get(HOME_ENV)
    base = Path(override).expanduser() if override else Path.home() / ".duet"
    (base / IDLE_DIRNAME).mkdir(parents=True, exist_ok=True)
    return base


# --- 세션 파일 -------------------------------------------------------------


@dataclass
class Session:
    id: str
    title: str = ""
    client: str = "알 수 없음"
    status: str = RUNNING  # 참여 여부는 파일이 어느 폴더에 있느냐로 안다
    started: str = ""
    heartbeat: str = ""
    summary: str = ""
    progress: list[dict[str, Any]] = field(default_factory=list)
    path: Path | None = field(default=None, compare=False, repr=False)

    @property
    def alive(self) -> bool:
        return self.status not in CLOSED and age(self.heartbeat) <= STALE_SEC

    @property
    def shown_status(self) -> str:
        """파일에 적힌 상태와 하트비트를 함께 본 것. 끊긴 세션은 이미 `중지`다."""
        if self.status in CLOSED:
            return self.status
        return RUNNING if self.alive else STOPPED

    def save(self) -> None:
        """임시 파일에 쓰고 바꿔치기한다. 읽는 쪽이 반쪽 JSON을 보지 않게."""
        data = {k: v for k, v in asdict(self).items() if k != "path"}
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, self.path)


def read_session(path: Path) -> Session | None:
    """세션 파일 하나. 읽을 수 없으면 None — 그 파일 때문에 목록이 죽지 않게."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    known = {f for f in Session.__dataclass_fields__ if f != "path"}
    session = Session(**{k: v for k, v in data.items() if k in known})
    session.path = path
    return session


def read_sessions(directory: Path) -> list[Session]:
    found = [read_session(p) for p in sorted(directory.glob("s-*.json"))]
    return sorted((s for s in found if s), key=lambda s: s.started)


# --- 타스크 ---------------------------------------------------------------


@dataclass
class Task:
    id: str
    title: str
    description: str
    override: str | None
    dir: Path
    sessions: list[Session] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        c = {RUNNING: 0, DONE: 0, STOPPED: 0}
        for s in self.sessions:
            c[s.shown_status] += 1
        return c

    @property
    def status(self) -> str:
        if self.override:
            return self.override
        c = self.counts
        if not self.sessions:
            return WAITING
        if c[RUNNING] or c[STOPPED]:
            return RUNNING  # 중지가 섞였으면 사람이 볼 때까지 열어 둔다
        return DONE

    @property
    def last_activity(self) -> str:
        stamps = [s.heartbeat for s in self.sessions if s.heartbeat]
        return max(stamps) if stamps else ""

    @property
    def warning(self) -> str:
        c = self.counts
        if c[STOPPED] and not c[RUNNING]:
            return f"중지된 세션 {c[STOPPED]}개 — 이어서 할지 사람이 정한다"
        return ""

    def to_dict(self) -> dict[str, Any]:
        data = {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "sessions": self.counts,
            "last_activity": self.last_activity,
        }
        if self.override:
            data["사람이 정한 상태"] = self.override
        if self.warning:
            data["warning"] = self.warning
        return data


def _read_task(task_dir: Path) -> Task:
    """`task.md`: 첫 줄이 `# 제목`, `상태:` 줄이 있으면 사람이 정한 것, 나머지는 설명."""
    text = (task_dir / "task.md").read_text(encoding="utf-8")
    override = None
    match = STATUS_LINE.search(text)
    if match and match.group(1) in TASK_STATUSES:
        override = match.group(1)

    lines = [ln for ln in text.splitlines() if not STATUS_LINE.match(ln)]
    title = lines[0].lstrip("# ").strip() if lines else task_dir.name
    return Task(
        id=TASK_DIR.match(task_dir.name).group(1),
        title=title,
        description="\n".join(lines[1:]).strip(),
        override=override,
        dir=task_dir,
        sessions=read_sessions(task_dir),
    )


def _write_task(task_dir: Path, title: str, description: str, override: str | None) -> None:
    out = [f"# {title}"]
    if override:
        out.append(f"상태: {override}")
    out += ["", description.strip(), ""]
    (task_dir / "task.md").write_text("\n".join(out), encoding="utf-8")


def task_dirs() -> list[Path]:
    return sorted(p for p in home().iterdir() if p.is_dir() and TASK_DIR.match(p.name))


def find_task(task_id: str | int) -> Path:
    """`3`, `"t3"`, `"T003"` 을 전부 받아준다. Claude가 부르는 자리라 표기가 흔들린다."""
    text = str(task_id).strip().upper().lstrip("T")
    if not text.isdigit():
        raise DuetError(f"타스크 id가 아니다: {task_id}")
    wanted = f"T{int(text):03d}"
    for p in task_dirs():
        if TASK_DIR.match(p.name).group(1) == wanted:
            return p
    raise DuetError(f"{wanted} 타스크가 없다.")


def create_task(title: str, description: str = "") -> Task:
    title = title.strip()
    if not title:
        raise DuetError("타스크 제목이 비어 있다.")
    slug = re.sub(r"\s+", "-", BAD_CHARS.sub("", title).strip())[:40].strip("-. ")

    number = len(task_dirs()) + 1
    while True:
        # mkdir이 원자적이라, 두 세션이 같은 번호를 노려도 진 쪽만 다음으로 밀린다.
        task_dir = home() / f"T{number:03d}-{slug}"
        try:
            task_dir.mkdir()
            break
        except FileExistsError:
            number += 1

    _write_task(task_dir, title, description, None)
    return _read_task(task_dir)


def get_task(task_id: str | int) -> Task:
    return _read_task(find_task(task_id))


def list_tasks(status: str | None = None) -> list[Task]:
    """마지막 활동이 늦은 순. 읽을 때마다 죽은 세션을 먼저 걷어낸다."""
    reap()
    tasks = []
    for task_dir in task_dirs():
        try:
            task = _read_task(task_dir)
        except (OSError, AttributeError, IndexError):
            continue  # 사람이 고치다 깨뜨린 파일 하나가 목록을 막지 않게
        if status is None or task.status == status:
            tasks.append(task)
    return sorted(tasks, key=lambda t: (t.last_activity, t.id), reverse=True)


def set_status(task_id: str | int, status: str | None) -> Task:
    """사람이 상태를 정한다. None을 주면 그 줄을 지워 다시 세게 한다."""
    if status is not None and status not in TASK_STATUSES:
        raise DuetError(f"모르는 상태다: {status} (가능: {', '.join(TASK_STATUSES)})")
    task = get_task(task_id)
    _write_task(task.dir, task.title, task.description, status)
    return get_task(task_id)


# --- 세션 -----------------------------------------------------------------


def register_session(client: str = "알 수 없음") -> Session:
    """서버 프로세스가 뜰 때 자기 파일을 `_미참여/`에 만든다."""
    session = Session(id="s-" + uuid.uuid4().hex[:8], client=client, started=now(), heartbeat=now())
    session.path = home() / IDLE_DIRNAME / f"{session.id}.json"
    session.save()
    return session


def join(session: Session, task_id: str | int, title: str = "") -> Task:
    """세션 파일을 타스크 폴더로 옮긴다. `task.md`는 건드리지 않는다."""
    if session.status in CLOSED:
        raise DuetError("이미 끝난 세션이다. 새 세션에서 참여하라.")
    task_dir = find_task(task_id)

    old = session.path
    session.title = title.strip() or session.title
    session.status = RUNNING
    session.heartbeat = now()
    session.path = task_dir / f"{session.id}.json"
    session.save()
    if old != session.path:
        old.unlink(missing_ok=True)
    return _read_task(task_dir)


def task_of(session: Session) -> Task | None:
    """세션 파일이 놓인 폴더가 곧 소속이다."""
    if session.path is None or not TASK_DIR.match(session.path.parent.name):
        return None
    return _read_task(session.path.parent)


def heartbeat(session: Session) -> None:
    if session.status not in CLOSED:
        session.heartbeat = now()
        session.save()


def report(session: Session, message: str, percent: int | None = None) -> dict[str, Any]:
    """진행 로그 한 줄. 이 글은 전부 Claude가 직접 쓴 것이다."""
    message = message.strip()
    if not message:
        raise DuetError("진행 내용이 비어 있다.")
    if session.status in CLOSED:
        raise DuetError("이미 끝난 세션이다. 보고할 수 없다.")

    entry = {"t": now(), "msg": message, "pct": percent}
    session.progress.append(entry)
    session.heartbeat = entry["t"]
    session.save()
    return entry


def finish(session: Session, status: str, summary: str = "") -> Task | None:
    """세션을 끝낸다. 먼저 적힌 종료가 남는다 — 나중 것이 덮지 않는다."""
    on_disk = read_session(session.path) if session.path else None
    if on_disk and on_disk.status in CLOSED:
        # 사람이 대시보드에서 먼저 바꿨거나, 종료 훅이 이미 돌았다.
        session.status, session.summary = on_disk.status, on_disk.summary
        return task_of(session)

    session.status = status
    session.summary = summary.strip()
    session.save()
    return task_of(session)


def reap() -> list[str]:
    """하트비트가 끊긴 세션을 `중지`로 적는다.

    "세션 파일은 그 프로세스만 쓴다"의 유일한 예외다. 쓸 프로세스가 이미 없어서
    허용된다 — 종료 훅조차 못 돈 경우(크래시·강제 종료)의 그물이다.
    """
    dead = []
    for directory in [home() / IDLE_DIRNAME, *task_dirs()]:
        for session in read_sessions(directory):
            if session.status in CLOSED or session.alive:
                continue
            session.status = STOPPED
            session.summary = session.summary or "하트비트 끊김 (프로세스 종료 추정)"
            session.save()
            dead.append(session.id)
    return dead
