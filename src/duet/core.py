"""Duet 핵심 — 폴더와 파일, 그리고 상태 규칙.

```
~/.duet/
├── duet/                    프로젝트 = 폴더
│   ├── T001-대시보드-만들기/
│   │   ├── task.md          제목·설명 (+ 사람이 정한 상태 한 줄)
│   │   └── s-a1b2c3d4.json  세션 (그 프로세스만 쓴다)
│   └── T002-…/
├── nefss/
│   └── T001-…/              번호는 프로젝트 안에서 1부터
├── _미분류/                 어느 프로젝트인지 못 알아낸 타스크
├── _미참여/                 아직 join 안 한 세션 파일
├── _보관/                   끝난 프로젝트를 폴더째 옮겨 두는 곳
│   └── nefss/…
└── 프로젝트.md              폴더 경로 = 부르는 이름 (별칭표)
```

**프로젝트가 단위다.** 타스크는 프로젝트 안에서 번호가 매겨지고, 보관도 프로젝트 폴더째
한다. 타스크는 프로젝트의 이력이라 끝났다고 하나씩 치우지 않는다.

규칙 두 개가 전부다:

1. **타스크 상태는 저장하지 않고 센다.** 폴더 안 세션 파일을 세서 정한다.
   전부 `완료`면 `완료`, 하나라도 살아 있으면 `진행중`, 끊긴 세션이 남았으면
   `확인 필요` (실패한 세션을 완료로 뭉개지 않는다). `보류`·`취소`는 사람만 정한다.
2. **사람이 정한 것이 이긴다.** `task.md`에 `상태: 완료` 한 줄이 있으면 그게 상태다.
   그 줄을 지우면 다시 센다.

**한 파일에 두 주인이 없다.** 세션 파일은 그 세션의 프로세스만 쓴다. `task.md`는
**편집만** 쓴다 — 사람이 화면·탐색기에서, Claude가 `update_task`로. 자동으로 도는 것들
(하트비트, 참여, 진행 보고, 상태 계산)은 `task.md`를 절대 건드리지 않는다. 그래서 락이 없다.

타스크를 부르는 이름은 `duet/T001` 이다. 같은 프로젝트 안에서는 `T001` 만으로도 된다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

HOME_ENV = "DUET_HOME"
IDLE_DIRNAME = "_미참여"      # 밑줄을 붙여 탐색기에서 프로젝트들 위에 고정되게 한다
ARCHIVE_DIRNAME = "_보관"    # 끝난 프로젝트를 치워 두는 곳. 지우는 것이 아니다
UNSORTED_DIRNAME = "_미분류"  # 프로젝트를 못 알아낸 타스크
ALIAS_FILENAME = "프로젝트.md"  # 폴더 경로 = 부르는 이름

HEARTBEAT_SEC = 30.0
STALE_SEC = 90.0  # 이만큼 하트비트가 없으면 죽은 세션이다

WAITING, RUNNING, DONE, STOPPED = "대기", "진행중", "완료", "중지"
#: 타스크에만 있는 상태. 확인 필요는 세션에서 세고, 보류·취소는 사람만 정한다.
ATTENTION, HOLD, CANCELLED = "확인 필요", "보류", "취소"
TASK_STATUSES = (WAITING, RUNNING, ATTENTION, DONE, HOLD, CANCELLED)
#: 사람이 고를 수 있는 것은 셋 — 끝났다, 잠시 둔다, 안 한다. 대기·진행중·확인 필요는
#: 세션이 없느냐 살아 있느냐 끊겼느냐는 사실이라 고르는 것이 아니다. 되돌리려면 🔒를 푼다.
HUMAN_STATUSES = (DONE, HOLD, CANCELLED)
#: 세션이 끝난 상태. 세션의 `중지`는 "안 끝난 채 끊겼다"이고 타스크의 `보류`와 다르다.
CLOSED = (DONE, STOPPED)

TASK_DIR = re.compile(r"^(T\d{3,})(?:-.*)?$")
STATUS_LINE = re.compile(r"^상태:\s*(\S+)\s*$", re.MULTILINE)
PROJECT_LINE = re.compile(r"^프로젝트:\s*(.+?)\s*$", re.MULTILINE)  # 옛 파일. 이관 때만 읽는다
META_LINE = re.compile(r"^(?:상태|프로젝트):\s*.*$")

#: 프로젝트 이름으로 삼지 않을 폴더. 여기서 세션이 떴다면 프로젝트를 안 것이 아니다.
NOT_PROJECT = frozenset({"", "/", "\\", "system32", "windows", "desktop", "바탕 화면", "temp", "tmp"})
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


_migrated: set[Path] = set()


def home() -> Path:
    """`~/.duet/`. 테스트와 여러 벌 운용을 위해 DUET_HOME으로만 바꾼다."""
    override = os.environ.get(HOME_ENV)
    base = Path(override).expanduser() if override else Path.home() / ".duet"
    (base / IDLE_DIRNAME).mkdir(parents=True, exist_ok=True)
    if base not in _migrated:
        _migrated.add(base)
        _migrate_flat_layout(base)
    return base


# --- 세션 파일 -------------------------------------------------------------


@dataclass
class Session:
    id: str
    title: str = ""
    client: str = "알 수 없음"
    status: str = RUNNING  # 참여 여부는 파일이 어느 폴더에 있느냐로 안다
    #: 이 세션이 뜬 폴더. 새 타스크가 어느 프로젝트에 놓일지가 여기서 나온다.
    cwd: str = ""
    #: 클라이언트가 붙인 대화 id (Claude Code라면 대화 기록 파일 이름과 같다).
    #: 우리 id와 따로 둔다 — 우리 것은 프로세스 하나를 가리키고, 이건 대화를 가리킨다.
    client_session: str = ""
    started: str = ""
    heartbeat: str = ""
    summary: str = ""
    #: 이 상태를 사람이 정했나. 세션이 스스로 보고한 것과 구분해 화면에 표시한다.
    by_human: bool = False
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

    def to_dict(self) -> dict[str, Any]:
        """화면과 도구에 나가는 꼴. 파일에 적는 것과 달리 살아있는지도 알려준다."""
        return {
            "id": self.id,
            "title": self.title,
            "client": self.client,
            "cwd": self.cwd,
            "client_session": self.client_session,
            "status": self.shown_status,
            "by_human": self.by_human,
            "alive": self.alive,
            "started": self.started,
            "heartbeat": self.heartbeat,
            "summary": self.summary,
            "progress": self.progress,
        }

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
    id: str  # 프로젝트 안에서의 번호. `T001`
    title: str
    description: str
    override: str | None
    dir: Path
    sessions: list[Session] = field(default_factory=list)

    @property
    def project(self) -> str:
        """부모 폴더가 곧 프로젝트다. `_미분류`는 빈 문자열."""
        name = self.dir.parent.name
        return "" if name == UNSORTED_DIRNAME else name

    @property
    def ref(self) -> str:
        """부르는 이름. `duet/T001`. 프로젝트를 모르면 `_미분류/T001`."""
        return f"{self.project or UNSORTED_DIRNAME}/{self.id}"

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
        if c[RUNNING]:
            return RUNNING
        if c[STOPPED]:
            return ATTENTION  # 끊긴 세션이 남았다. 이어갈지 끝낼지 사람이 정한다
        return DONE

    @property
    def last_activity(self) -> str:
        stamps = [s.heartbeat for s in self.sessions if s.heartbeat]
        return max(stamps) if stamps else ""

    @property
    def warning(self) -> str:
        """사람이 봐야 하는 것. `확인 필요`일 때만."""
        if self.status != ATTENTION:
            return ""
        return f"끊긴 세션 {self.counts[STOPPED]}개 — 이어갈지 끝낼지 사람이 정한다"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "ref": self.ref,
            "project": self.project,
            "title": self.title,
            "status": self.status,
            "counts": self.counts,
            "override": self.override,
            "warning": self.warning,
            "last_activity": self.last_activity,
        }

    def to_detail(self) -> dict[str, Any]:
        """세션이 무엇을 했는지까지. 다른 세션이 읽고 이어받는 재료다."""
        return self.to_dict() | {
            "description": self.description,
            "folder": str(self.dir),
            "sessions": [s.to_dict() for s in self.sessions],
        }


def _read_task(task_dir: Path) -> Task:
    """`task.md`: 첫 줄이 `# 제목`, `상태:` 줄이 있으면 사람이 정한 것, 나머지는 설명."""
    text = (task_dir / "task.md").read_text(encoding="utf-8")

    override = None
    match = STATUS_LINE.search(text)
    if match:
        written = HOLD if match.group(1) == STOPPED else match.group(1)  # 옛 이름
        if written in HUMAN_STATUSES:
            override = written

    lines = [ln for ln in text.splitlines() if not META_LINE.match(ln)]
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


def _safe_read(task_dir: Path) -> Task | None:
    """사람이 고치다 깨뜨린 파일 하나가 목록 전체를 막지 않게."""
    try:
        return _read_task(task_dir)
    except (OSError, AttributeError, IndexError):
        return None


# --- 프로젝트 폴더 ----------------------------------------------------------


def project_dir(name: str, base: Path | None = None) -> Path:
    """프로젝트 폴더. 빈 이름은 `_미분류`."""
    root = base or home()
    folder = name.strip() or UNSORTED_DIRNAME
    if folder != UNSORTED_DIRNAME and (folder.startswith("_") or BAD_CHARS.search(folder)):
        raise DuetError(f"프로젝트 이름으로 쓸 수 없다: {name}")
    return root / folder


def project_dirs(base: Path | None = None) -> list[Path]:
    """보드에 뜨는 프로젝트 폴더. `_미분류`는 포함, 다른 `_` 폴더(미참여·보관)는 제외."""
    root = base or home()
    out = []
    for p in root.iterdir():
        if not p.is_dir():
            continue
        if p.name.startswith("_") and p.name != UNSORTED_DIRNAME:
            continue
        out.append(p)
    return sorted(out, key=lambda p: p.name)


def task_dirs_in(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_dir() and TASK_DIR.match(p.name))


def task_dirs() -> list[Path]:
    """보드에 뜨는 타스크 폴더 전부."""
    return [t for p in project_dirs() for t in task_dirs_in(p)]


def fingerprint() -> str:
    """`~/.duet` 아래 파일들이 지금 어떤 모양인지 한 줄. 바뀌면 값이 바뀐다.

    보드가 "뭔가 바뀌었나"를 물을 때 쓴다. watchdog 같은 감시기 대신 파일 몇백 개의 수정
    시각을 훑는다 — 개인용 규모에서 수 ms이고, 프로세스가 하나 더 안 뜬다.
    """
    root = home()
    parts: list[str] = []
    folders = [root / IDLE_DIRNAME, *project_dirs(root), *task_dirs()]
    for folder in folders:
        try:
            with os.scandir(folder) as it:
                for entry in it:
                    if entry.is_file() and (entry.name.endswith(".json") or entry.name == "task.md"):
                        # 같은 틱 안에 두 번 쓰면 수정 시각이 같을 수 있다. 크기를 같이 본다.
                        st = entry.stat()
                        parts.append(f"{entry.path}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            continue
    parts.append(str(sorted(p.name for p in folders)))
    digest = hashlib.blake2b("
".join(sorted(parts)).encode("utf-8"), digest_size=8).hexdigest()
    return f"{len(parts)}:{digest}"


def archive_dir() -> Path:
    path = home() / ARCHIVE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def _next_task_dir(folder: Path, title: str) -> Path:
    """그 프로젝트의 다음 번호로 타스크 폴더를 만든다.

    번호는 폴더를 세어 정한다. 두 세션이 동시에 만들면 같은 번호를 노리는데, mkdir이
    원자적이라 진 쪽만 다음으로 밀린다.
    """
    folder.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"\s+", "-", BAD_CHARS.sub("", title).strip())[:40].strip("-. ")
    used = {TASK_DIR.match(p.name).group(1) for p in task_dirs_in(folder)}
    number = 1
    while True:
        tid = f"T{number:03d}"
        if tid in used:
            number += 1
            continue
        candidate = folder / (f"{tid}-{slug}" if slug else tid)
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            number += 1


# --- 타스크 찾기·만들기·고치기 ------------------------------------------------


def _parse_ref(ref: str | int) -> tuple[str | None, str]:
    """`duet/T3`, `T003`, `3`, `_미분류/T1` 을 (프로젝트, T00n) 으로."""
    text = str(ref).strip()
    project: str | None = None
    if "/" in text:
        project, _, text = text.rpartition("/")
        project = project.strip()
        if project == UNSORTED_DIRNAME:
            project = ""
    number = text.strip().upper().lstrip("T")
    if not number.isdigit():
        raise DuetError(f"타스크 id가 아니다: {ref}")
    return project, f"T{int(number):03d}"


def find_task(ref: str | int, hint: str | None = None) -> Path:
    """타스크 폴더를 찾는다.

    `duet/T001` 처럼 프로젝트가 붙어 있으면 거기서만 찾는다. `T001` 만 왔으면 `hint`
    (부른 세션이 뜬 폴더의 프로젝트)에서 먼저 찾고, 없으면 전체를 훑는다 — 하나뿐이면
    그것이고, 여럿이면 어느 것인지 물어야 한다.
    """
    project, tid = _parse_ref(ref)
    if project is not None:
        for p in task_dirs_in(project_dir(project)):
            if TASK_DIR.match(p.name).group(1) == tid:
                return p
        raise DuetError(f"{project or UNSORTED_DIRNAME}/{tid} 타스크가 없다.")

    if hint is not None:
        for p in task_dirs_in(project_dir(hint)):
            if TASK_DIR.match(p.name).group(1) == tid:
                return p

    found = [p for p in task_dirs() if TASK_DIR.match(p.name).group(1) == tid]
    if len(found) == 1:
        return found[0]
    if not found:
        raise DuetError(f"{tid} 타스크가 없다.")
    refs = ", ".join(f"{p.parent.name}/{tid}" for p in found)
    raise DuetError(f"{tid}가 여러 프로젝트에 있다: {refs}. 프로젝트를 붙여서 불러라.")


def get_task(ref: str | int, hint: str | None = None) -> Task:
    return _read_task(find_task(ref, hint))


def create_task(title: str, description: str = "", project: str = "") -> Task:
    """타스크를 만든다. 프로젝트 폴더가 없으면 그때 생긴다 — 프로젝트는 첫 타스크와 함께 태어난다."""
    title = title.strip()
    if not title:
        raise DuetError("타스크 제목이 비어 있다.")
    task_dir = _next_task_dir(project_dir(project), title)
    _write_task(task_dir, title, description, None)
    return _read_task(task_dir)


def list_tasks(status: str | None = None) -> list[Task]:
    """마지막 활동이 늦은 순. 읽을 때마다 죽은 세션을 먼저 걷어낸다."""
    reap()
    tasks = [t for d in task_dirs() if (t := _safe_read(d))]
    if status:
        tasks = [t for t in tasks if t.status == status]
    return sorted(tasks, key=lambda t: (t.last_activity, t.project, t.id), reverse=True)


def projects() -> list[str]:
    """보드에 있는 프로젝트 이름들. 모르는 것은 빈 문자열 — 단, 비어 있으면 상자로 띄우지 않는다."""
    out = []
    for p in project_dirs():
        if p.name == UNSORTED_DIRNAME:
            if task_dirs_in(p):
                out.append("")
        else:
            out.append(p.name)
    return out


def similar_tasks(title: str, limit: int = 3) -> list[Task]:
    """제목이 겹치는 **열린** 타스크. 같은 일을 두 번 만들지 않게 미리 보여준다.

    똑똑하게 재지 않는다 — 낱말이 하나라도 겹치면 후보다. 판단은 부른 쪽이 한다.
    """
    words = {w for w in re.split(r"[\s·,]+", title.lower()) if len(w) > 1}
    found = []
    for task in list_tasks():
        if task.status in (DONE, CANCELLED):
            continue
        other = {w for w in re.split(r"[\s·,]+", task.title.lower()) if len(w) > 1}
        nested = title.lower() in task.title.lower() or task.title.lower() in title.lower()
        if (words & other) or nested:
            found.append(task)
    return found[:limit]


def update_task(
    ref: str | int,
    title: str | None = None,
    description: str | None = None,
    project: str | None = None,
    hint: str | None = None,
) -> Task:
    """제목·설명을 고친다. 사람이 정한 `상태:` 줄은 건드리지 않는다.

    프로젝트를 주면 그 폴더로 **옮긴다.** 번호는 옮겨 간 프로젝트의 다음 번호가 된다 —
    같은 프로젝트 안에서 번호가 겹칠 수는 없어서다. 살아 있는 세션이 붙어 있으면 못 옮긴다.
    """
    task = get_task(ref, hint)
    new_title = task.title if title is None else title.strip()
    if not new_title:
        raise DuetError("타스크 제목이 비어 있다.")
    new_description = task.description if description is None else description.strip()
    _write_task(task.dir, new_title, new_description, task.override)

    if project is not None and project.strip() != task.project:
        if task.counts[RUNNING]:
            raise DuetError("살아 있는 세션이 붙어 있어 다른 프로젝트로 못 옮긴다.")
        target = _next_task_dir(project_dir(project), new_title)
        target.rmdir()  # 번호만 받고, 폴더는 통째로 옮긴다
        task.dir.rename(target)
        return _read_task(target)
    return _read_task(task.dir)


def set_status(ref: str | int, status: str | None, hint: str | None = None) -> Task:
    """사람이 상태를 정한다. None을 주면 그 줄을 지워 다시 세게 한다."""
    if status is not None and status not in HUMAN_STATUSES:
        raise DuetError(
            f"고를 수 있는 상태: {', '.join(HUMAN_STATUSES)}. "
            f"'{WAITING}'·'{RUNNING}'·'{ATTENTION}'는 세션이 없느냐 살아 있느냐 끊겼느냐는 "
            "사실이라 고르는 것이 아니다. 되돌리려면 status=None(🔒 풀기)."
        )
    task = get_task(ref, hint)
    _write_task(task.dir, task.title, task.description, status)
    return _read_task(task.dir)


# --- 세션 -----------------------------------------------------------------


def aliases() -> dict[str, str]:
    """`~/.duet/프로젝트.md` — 폴더 경로에 붙일 이름.

    폴더 이름과 부르는 이름이 다를 때가 있다 (`C:\\project\\bookshelf` 를 "서재"라
    부르는 식). MCP는 클라이언트가 화면에 뭐라고 띄우는지 알려주지 않으므로 한 줄 적어 둔다.

    ```
    C:\\project\\bookshelf = 서재
    ```
    """
    path = home() / ALIAS_FILENAME
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    found = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        folder, _, name = line.partition("=")
        folder, name = folder.strip(), name.strip()
        if folder and name:
            found[str(Path(folder)).lower()] = name  # 윈도우는 대소문자를 안 가린다
    return found


def project_name(cwd: str) -> str:
    """세션이 뜬 폴더에서 프로젝트 이름을 짚는다.

    Claude Code는 프로젝트 폴더에서 뜨므로 그 폴더 이름이 곧 프로젝트다. 별칭표에 적어 둔
    경로면 그 이름을 쓴다. 프로젝트로 볼 수 없는 폴더에서 떴으면 빈 문자열 — **모르면
    모른다고 두는 편이 틀린 이름보다 낫다.**
    """
    if not cwd:
        return ""
    named = aliases().get(str(Path(cwd)).lower())
    if named:
        return named
    name = Path(cwd).name.strip()
    if name.lower() in NOT_PROJECT or name.startswith("."):
        return ""
    return name


def register_session(client: str = "알 수 없음", cwd: str = "", client_session: str = "") -> Session:
    """서버 프로세스가 뜰 때 자기 파일을 `_미참여/`에 만든다."""
    session = Session(
        id="s-" + uuid.uuid4().hex[:8], client=client, cwd=cwd,
        client_session=client_session, started=now(), heartbeat=now(),
    )
    session.path = home() / IDLE_DIRNAME / f"{session.id}.json"
    session.save()
    return session


def join(session: Session, ref: str | int, title: str = "") -> Task:
    """세션 파일을 타스크 폴더로 옮긴다. `task.md`는 건드리지 않는다."""
    if session.status in CLOSED:
        done = task_of(session)
        where = f"[{done.ref}] {done.title}을(를)" if done else "일을"
        raise DuetError(
            f"이 세션은 {where} 이미 끝냈다. 세션 하나는 타스크 하나다 — "
            "다른 일은 새 세션에서."
        )
    current = task_of(session)
    if current is not None and session.progress:
        raise DuetError(
            f"이 세션은 이미 [{current.ref}] {current.title}에 붙어 보고까지 했다. "
            "세션 하나는 타스크 하나다."
        )
    task_dir = find_task(ref, hint=project_name(session.cwd))

    # 타스크 하나에는 살아 있는 세션 하나만. 두 창이 같은 일을 동시에 하면 같은 코드를
    # 동시에 건드린다. 이어받기는 앞 세션이 끝나거나 멈춘 뒤다.
    others = [s for s in read_sessions(task_dir) if s.id != session.id and s.alive]
    if others:
        busy = others[0]
        raise DuetError(
            f"[{task_dir.parent.name}/{TASK_DIR.match(task_dir.name).group(1)}]에는 이미 살아 있는 "
            f"세션이 있다: '{busy.title or busy.id}' ({busy.client}). 타스크 하나에는 세션 하나만 "
            "붙는다 — 그 세션이 끝나거나 멈춘 뒤에 이어받는다. 같은 일을 다른 창에서 하고 있는 게 "
            "아닌지 사용자에게 확인하라."
        )

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
    return _safe_read(session.path.parent)


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
        session.status, session.summary = on_disk.status, on_disk.summary
        return task_of(session)
    session.status = status
    session.summary = summary.strip()
    session.save()
    return task_of(session)


def set_session_status(ref: str | int, session_id: str, status: str, hint: str | None = None) -> Task:
    """**끝난** 세션의 상태를 사람이 바꾼다 (중지 → 완료, 또는 그 반대).

    살아 있는 세션은 건드리지 않는다. 그 파일의 주인은 그 프로세스다. 중지된 세션을
    `완료`로 바꾸면 자동 규칙이 다시 세서 타스크가 스스로 닫힌다.
    """
    if status not in CLOSED:
        raise DuetError("세션은 완료 또는 중지로만 바꾼다.")
    task = get_task(ref, hint)
    for session in task.sessions:
        if session.id == session_id:
            break
    else:
        raise DuetError(f"{task.ref}에 {session_id} 세션이 없다.")
    if session.alive:
        raise DuetError("아직 살아 있는 세션이다. 그 창을 닫거나 pause_session을 부르게 하라.")
    session.status = status
    session.by_human = True
    session.save()
    return _read_task(task.dir)


# --- 보관 (프로젝트 폴더째) --------------------------------------------------


def project_tasks(name: str, archived: bool = False) -> list[Task]:
    folder = project_dir(name, archive_dir() if archived else None)
    return [t for d in task_dirs_in(folder) if (t := _safe_read(d))]


def list_archived() -> list[Task]:
    """보관함 전부. 여기서는 죽은 세션을 걷어내지 않는다 — 이미 끝난 일이다."""
    out = []
    for p in project_dirs(archive_dir()):
        out += [t for d in task_dirs_in(p) if (t := _safe_read(d))]
    return sorted(out, key=lambda t: (t.project, t.id), reverse=True)


def archive_project(name: str) -> list[Task]:
    """프로젝트 폴더째 `_보관/`으로 옮긴다.

    **지우지 않는다.** 타스크는 프로젝트의 이력이라 하나씩 치우지 않고, 프로젝트가 끝났을
    때 통째로 치운다. 살아 있는 세션이 하나라도 있으면 거부한다 — 폴더를 옮기면 그 프로세스가
    기억하는 경로가 끊긴다.
    """
    src = project_dir(name)
    tasks = project_tasks(name)
    if not tasks:
        raise DuetError(f"'{name or UNSORTED_DIRNAME}'에 타스크가 없다.")
    busy = [t.ref for t in tasks if t.counts[RUNNING]]
    if busy:
        raise DuetError(f"살아 있는 세션이 있다: {', '.join(busy)}. 창을 닫고 나서 보관한다.")
    target = archive_dir() / src.name
    if target.exists():
        raise DuetError(f"보관함에 '{src.name}'이 이미 있다. 먼저 되돌리거나 이름을 바꿔라.")
    src.rename(target)
    return project_tasks(name, archived=True)


def unarchive_project(name: str) -> list[Task]:
    """보관함에서 프로젝트 폴더째 도로 꺼낸다."""
    src = project_dir(name, archive_dir())
    if not src.is_dir():
        raise DuetError(f"보관함에 '{name or UNSORTED_DIRNAME}'이 없다.")
    target = home() / src.name
    if target.exists():
        raise DuetError(f"보드에 '{src.name}'이 이미 있다.")
    src.rename(target)
    return project_tasks(name)


# --- 정리 -----------------------------------------------------------------


def delete_task(ref: str | int, hint: str | None = None) -> str:
    """타스크 폴더를 지운다. 세션 기록까지 함께 사라진다.

    잘못 만든 것을 치우는 용도다. 끝난 일은 지우지 말고 프로젝트째 보관한다.
    살아 있는 세션이 붙어 있으면 거부한다 — 그 프로세스가 아직 그 폴더에 쓴다.
    """
    task = get_task(ref, hint)
    if task.counts[RUNNING]:
        raise DuetError("살아 있는 세션이 붙어 있어 지울 수 없다. 그 창을 닫거나 끝낸 뒤에.")
    shutil.rmtree(task.dir)
    return task.ref


def delete_project(name: str) -> str:
    """프로젝트 폴더를 타스크째 지운다. 살아 있는 세션이 하나라도 있으면 거부한다."""
    folder = project_dir(name)
    if not folder.is_dir():
        raise DuetError(f"'{name or UNSORTED_DIRNAME}' 프로젝트가 없다.")
    busy = [t.ref for t in project_tasks(name) if t.counts[RUNNING]]
    if busy:
        raise DuetError(f"살아 있는 세션이 있다: {', '.join(busy)}. 창을 닫은 뒤에 지운다.")
    shutil.rmtree(folder)
    return folder.name


def close_task(ref: str | int, reason: str = "사람이 타스크를 닫았다", hint: str | None = None) -> Task:
    """죽어 있는 세션을 `중지`로 적고 타스크를 사람이 정한 `완료`로 둔다.

    **살아 있는 세션은 건드리지 않는다** — 남의 프로세스를 죽이지 않는다.
    """
    task = get_task(ref, hint)
    for session in task.sessions:
        if session.status in CLOSED or session.alive:
            continue
        session.status = STOPPED
        session.summary = session.summary or reason
        session.by_human = True
        session.save()
    return set_status(task.ref, DONE)


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


# --- 이관 -----------------------------------------------------------------


def _migrate_flat_layout(base: Path) -> None:
    """옛 배치(`~/.duet/T001-…` 평평하게)를 프로젝트 폴더 아래로 옮긴다. 한 번만 일어난다.

    프로젝트는 옛 방식대로 정한다 — `task.md`의 `프로젝트:` 줄이 있으면 그것, 없으면
    붙은 세션이 뜬 폴더. 둘 다 없으면 `_미분류`. 번호는 옮겨 간 프로젝트의 다음 번호다.
    """
    for root in (base, base / ARCHIVE_DIRNAME):
        if not root.is_dir():
            continue
        for old in sorted(p for p in root.iterdir() if p.is_dir() and TASK_DIR.match(p.name)):
            try:
                text = (old / "task.md").read_text(encoding="utf-8")
            except OSError:
                continue
            found = PROJECT_LINE.search(text)
            project = found.group(1).strip() if found else ""
            if not project:
                for s in read_sessions(old):
                    project = project_name(s.cwd)
                    if project:
                        break
            try:
                folder = project_dir(project, root)
            except DuetError:
                folder = root / UNSORTED_DIRNAME
            folder.mkdir(parents=True, exist_ok=True)
            title = old.name.split("-", 1)[1] if "-" in old.name else ""
            target = _next_task_dir(folder, title.replace("-", " "))
            target.rmdir()
            old.rename(target)
            # `프로젝트:` 줄은 이제 폴더가 대신한다. 남겨 두면 헷갈린다.
            cleaned = "\n".join(ln for ln in text.splitlines() if not PROJECT_LINE.match(ln))
            (target / "task.md").write_text(cleaned, encoding="utf-8")
