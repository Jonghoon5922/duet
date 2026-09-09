"""세션 파일 (`s-XXXX.json`) 읽고 쓰기.

**이 파일은 그 세션의 프로세스만 쓴다.** 그래서 락이 필요 없다 (SPEC 5절).
읽는 쪽(대시보드·CLI·다른 세션의 `get_task`)은 여럿이지만 읽기뿐이다.

쓰기는 임시 파일에 다 쓴 뒤 바꿔치기한다. 하트비트를 쓰는 도중에 대시보드가 읽어도
반쪽짜리 JSON을 보지 않게.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .states import (
    SESSION_CLOSED,
    SESSION_IDLE,
    STALE_SEC,
    age_seconds,
    now,
)

SUFFIX = ".json"
PREFIX = "s-"


def new_session_id() -> str:
    """세션 id. 프로세스마다 새로 만들고 어디에도 저장해 두지 않는다."""
    return PREFIX + uuid.uuid4().hex[:8]


@dataclass
class SessionFile:
    """세션 파일 하나. 그대로 JSON이 된다."""

    id: str
    title: str = ""
    client: str = "알 수 없음"
    status: str = SESSION_IDLE
    started: str = ""
    heartbeat: str = ""
    completed: str | None = None
    summary: str | None = None
    pid: int = 0
    cwd: str = ""
    #: [{"t": ISO, "msg": str, "pct": int|None}] — Claude가 직접 쓴 진행 로그
    progress: list[dict[str, Any]] = field(default_factory=list)

    #: 읽은 파일의 위치. 저장 대상이 아니라서 asdict에서 빼낸다.
    path: Path | None = field(default=None, compare=False, repr=False)

    @property
    def alive(self) -> bool:
        """하트비트가 아직 도는 세션인지. 끝난 세션은 언제나 False."""
        if self.status in SESSION_CLOSED:
            return False
        return age_seconds(self.heartbeat) <= STALE_SEC

    @property
    def percent(self) -> int | None:
        """마지막으로 보고된 퍼센트."""
        for entry in reversed(self.progress):
            if entry.get("pct") is not None:
                return int(entry["pct"])
        return None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("path", None)
        return data

    def to_view(self) -> dict[str, Any]:
        """도구·화면에 나가는 꼴. 파일에 적는 것과 달리 살아있는지도 알려준다."""
        view = self.to_dict()
        view["alive"] = self.alive
        view["percent"] = self.percent
        return view


def path_for(directory: Path, session_id: str) -> Path:
    return directory / f"{session_id}{SUFFIX}"


def create(directory: Path, session_id: str, *, client: str, pid: int, cwd: str) -> SessionFile:
    """`미참여` 세션 파일을 만든다."""
    directory.mkdir(parents=True, exist_ok=True)
    ts = now()
    session = SessionFile(
        id=session_id, client=client, status=SESSION_IDLE,
        started=ts, heartbeat=ts, pid=pid, cwd=cwd,
    )
    session.path = path_for(directory, session_id)
    write(session)
    return session


def write(session: SessionFile) -> None:
    """임시 파일에 쓰고 바꿔치기한다. 읽는 쪽이 반쪽 JSON을 보지 않게."""
    if session.path is None:
        raise ValueError("세션 파일 경로가 없다.")
    session.path.parent.mkdir(parents=True, exist_ok=True)
    tmp = session.path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(
        json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(tmp, session.path)


def read(path: Path) -> SessionFile | None:
    """세션 파일 하나. 읽을 수 없으면 None.

    바꿔치기 도중이거나 사람이 잘못 고쳤을 수 있다. 그 파일 하나 때문에
    목록 전체가 죽으면 안 되므로 조용히 건너뛴다.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None

    known = {f for f in SessionFile.__dataclass_fields__ if f != "path"}
    session = SessionFile(**{k: v for k, v in data.items() if k in known})
    session.path = path
    return session


def read_dir(directory: Path) -> list[SessionFile]:
    """폴더 안의 세션 파일 전부 (시작순)."""
    if not directory.is_dir():
        return []
    found = []
    for p in sorted(directory.glob(f"{PREFIX}*{SUFFIX}")):
        session = read(p)
        if session is not None:
            found.append(session)
    found.sort(key=lambda s: (s.started, s.id))
    return found


def move(session: SessionFile, directory: Path) -> SessionFile:
    """세션 파일을 다른 폴더로 옮긴다 (`unassigned` → 타스크 폴더).

    옮기는 것도 이 세션의 프로세스만 한다.
    """
    if session.path is None:
        raise ValueError("세션 파일 경로가 없다.")
    directory.mkdir(parents=True, exist_ok=True)
    target = path_for(directory, session.id)
    old = session.path
    session.path = target
    write(session)
    if old != target:
        old.unlink(missing_ok=True)
    return session
