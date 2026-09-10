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
   전부 `완료`면 `완료`, 하나라도 살아 있으면 `진행중`, 끊긴 세션이 남았으면
   `확인 필요` (실패한 세션을 완료로 뭉개지 않는다). `보류`·`취소`는 사람만 정한다.
2. **사람이 정한 것이 이긴다.** `task.md`에 `상태: 완료` 한 줄이 있으면 그게 상태다.
   그 줄을 지우면 다시 센다.

**한 파일에 두 주인이 없다.** 세션 파일은 그 세션의 프로세스만 쓴다.
`task.md`는 **편집만** 쓴다 — 사람이 화면·탐색기에서, Claude가 `update_task`로.
둘 다 누가 시켜서 한 번 일어나는 일이라 부딪히지 않는다. 자동으로 도는 것들
(하트비트, 참여, 진행 보고, 상태·프로젝트 계산)은 `task.md`를 절대 건드리지 않는다.
그래서 락이 없다.
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
IDLE_DIRNAME = "_미참여"    # 밑줄을 붙여 탐색기에서 타스크들 위에 고정되게 한다
ARCHIVE_DIRNAME = "_보관"  # 끝난 타스크를 치워 두는 곳. 지우는 것이 아니다
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
PROJECT_LINE = re.compile(r"^프로젝트:\s*(.+?)\s*$", re.MULTILINE)
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
    #: 이 세션이 뜬 폴더. 어느 프로젝트에서 일하는지가 여기서 나온다.
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
    id: str
    title: str
    description: str
    override: str | None
    dir: Path
    #: `프로젝트:` 줄. 사람이 적었을 때만 있고, 계산값을 덮는다.
    project_override: str = ""
    sessions: list[Session] = field(default_factory=list)

    @property
    def project(self) -> str:
        """어느 일에 속하나. **저장하지 않고 센다** — 상태와 같은 방식이다.

        붙은 세션이 뜬 폴더가 곧 프로젝트다. 사람이 분류하지 않아도 저절로 묶이고,
        `프로젝트:` 줄을 적어 두면 그것이 이긴다. 세션도 없고 적힌 줄도 없으면
        빈 문자열 — 모르면 모른다고 둔다.
        """
        if self.project_override:
            return self.project_override
        for session in self.sessions:  # 먼저 붙은 세션이 정한다
            name = project_name(session.cwd)
            if name:
                return name
        return ""

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
        """사람이 봐야 하는 것. **아직 닫히지 않은** 타스크에만 뜬다.

        닫고 나서도 경고가 남으면 "확인 필요"가 영영 줄지 않는다 — 이미 판단한 일이다.
        """
        if self.status != ATTENTION:
            return ""
        return f"끊긴 세션 {self.counts[STOPPED]}개 — 이어갈지 끝낼지 사람이 정한다"

    def to_dict(self) -> dict[str, Any]:
        """카드 한 장에 필요한 만큼."""
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "counts": self.counts,
            "override": self.override,
            "project": self.project,
            "project_override": self.project_override,
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
    """`task.md`를 읽는다.

    ```markdown
    # pc101pm 전환
    프로젝트: nefss        ← 어느 일에 속하나 (비어 있으면 세션이 붙을 때 저절로 채워진다)
    상태: 완료             ← 사람이 정했을 때만. 지우면 다시 센다

    설명 본문
    ```
    """
    text = (task_dir / "task.md").read_text(encoding="utf-8")

    override = None
    match = STATUS_LINE.search(text)
    if match:
        written = "보류" if match.group(1) == "중지" else match.group(1)  # 옛 이름
        if written in HUMAN_STATUSES:
            override = written

    found = PROJECT_LINE.search(text)
    project = found.group(1) if found else ""

    lines = [ln for ln in text.splitlines() if not META_LINE.match(ln)]
    title = lines[0].lstrip("# ").strip() if lines else task_dir.name
    return Task(
        id=TASK_DIR.match(task_dir.name).group(1),
        title=title,
        description="\n".join(lines[1:]).strip(),
        override=override,
        project_override=project,
        dir=task_dir,
        sessions=read_sessions(task_dir),
    )


def _write_task(
    task_dir: Path, title: str, description: str, override: str | None, project: str = ""
) -> None:
    out = [f"# {title}"]
    if project:
        out.append(f"프로젝트: {project}")
    if override:
        out.append(f"상태: {override}")
    out += ["", description.strip(), ""]
    (task_dir / "task.md").write_text("\n".join(out), encoding="utf-8")


def task_dirs() -> list[Path]:
    """보드에 뜨는 타스크 폴더. `_`로 시작하는 폴더(미참여·보관)는 이름에서 걸러진다."""
    return sorted(p for p in home().iterdir() if p.is_dir() and TASK_DIR.match(p.name))


def archive_dir() -> Path:
    path = home() / ARCHIVE_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def archived_dirs() -> list[Path]:
    return sorted(p for p in archive_dir().iterdir() if p.is_dir() and TASK_DIR.match(p.name))


def list_archived() -> list[Task]:
    """보관함. 여기서는 죽은 세션을 걷어내지 않는다 — 이미 끝난 일이다."""
    out = []
    for task_dir in archived_dirs():
        try:
            out.append(_read_task(task_dir))
        except (OSError, AttributeError, IndexError):
            continue
    return sorted(out, key=lambda t: t.id, reverse=True)


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


def create_task(title: str, description: str = "", project: str = "") -> Task:
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

    _write_task(task_dir, title, description, None, project.strip())
    return _read_task(task_dir)


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
        overlap = words & other
        nested = title.lower() in task.title.lower() or task.title.lower() in title.lower()
        if overlap or nested:
            found.append(task)
    return found[:limit]


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


def update_task(
    task_id: str | int,
    title: str | None = None,
    description: str | None = None,
    project: str | None = None,
) -> Task:
    """제목·설명을 고친다. **폴더 이름은 그대로 둔다** — 판별자는 앞의 id다.

    사람이 정한 `상태:` 줄은 건드리지 않는다. 제목을 고치러 왔다가 상태가 풀리면
    안 된다.
    """
    task = get_task(task_id)
    new_title = task.title if title is None else title.strip()
    if not new_title:
        raise DuetError("타스크 제목이 비어 있다.")
    new_description = task.description if description is None else description.strip()
    # 빈 문자열을 주면 그 줄을 지워 다시 세게 한다. None은 '안 건드림'이다.
    new_project = task.project_override if project is None else project.strip()

    _write_task(task.dir, new_title, new_description, task.override, new_project)
    return get_task(task_id)


def set_status(task_id: str | int, status: str | None) -> Task:
    """사람이 상태를 정한다. None을 주면 그 줄을 지워 다시 세게 한다."""
    if status is not None and status not in HUMAN_STATUSES:
        raise DuetError(
            f"고를 수 있는 상태: {', '.join(HUMAN_STATUSES)}. "
            f"'{WAITING}'·'{RUNNING}'·'{ATTENTION}'는 세션이 없느냐 살아 있느냐 끊겼느냐는 "
            "사실이라 고르는 것이 아니다. 되돌리려면 status=None(🔒 풀기)."
        )
    task = get_task(task_id)
    _write_task(task.dir, task.title, task.description, status, task.project_override)
    return get_task(task_id)


# --- 세션 -----------------------------------------------------------------


def aliases() -> dict[str, str]:
    """`~/.duet/프로젝트.md` — 폴더 경로에 붙일 이름.

    폴더 이름과 부르는 이름이 다를 때가 있다 (`C:\\project\\bookshelf` 를 "서재"라
    부르는 식). MCP는 클라이언트가 화면에 뭐라고 띄우는지 알려주지 않으므로 — 그건
    앱마다 다르고 문서화된 것도 아니다 — 한 줄 적어 두는 쪽을 택했다.

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
            # 윈도우는 대소문자를 가리지 않고 구분자도 섞여 들어온다.
            found[str(Path(folder)).lower()] = name
    return found


def project_name(cwd: str) -> str:
    """세션이 뜬 폴더에서 프로젝트 이름을 짚는다.

    Claude Code는 프로젝트 폴더에서 뜨므로 그 폴더 이름이 곧 프로젝트다. 별칭표에
    적어 둔 경로면 그 이름을 쓴다. 프로젝트로 볼 수 없는 폴더에서 떴으면 (Claude
    Desktop처럼 엉뚱한 데서 뜨는 클라이언트가 있다) 빈 문자열을 준다 —
    **모르면 모른다고 두는 편이 틀린 이름보다 낫다.**
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


def register_session(
    client: str = "알 수 없음", cwd: str = "", client_session: str = ""
) -> Session:
    """서버 프로세스가 뜰 때 자기 파일을 `_미참여/`에 만든다."""
    session = Session(
        id="s-" + uuid.uuid4().hex[:8], client=client, cwd=cwd,
        client_session=client_session, started=now(), heartbeat=now(),
    )
    session.path = home() / IDLE_DIRNAME / f"{session.id}.json"
    session.save()
    return session


def join(session: Session, task_id: str | int, title: str = "") -> Task:
    """세션 파일을 타스크 폴더로 옮긴다.

    `task.md`는 건드리지 않는다. 이 세션이 뜬 폴더가 파일에 남으므로, 타스크가 어느
    프로젝트 것인지는 읽는 쪽이 세션에서 계산한다 — 상태와 같은 방식이다.
    """
    if session.status in CLOSED:
        # 세션 하나는 타스크 하나다. 끝냈으면 이 창은 끝이고, 다른 일은 새 창에서 한다.
        done = task_of(session)
        where = f"[{done.id}] {done.title}을(를)" if done else "일을"
        raise DuetError(
            f"이 세션은 {where} 이미 끝냈다. 세션 하나는 타스크 하나다 — "
            "다른 일은 새 창에서 시작하라고 사용자에게 말하라."
        )
    current = task_of(session)
    if current is not None and session.progress:
        # 보고까지 한 뒤에 옮기면 그 로그가 엉뚱한 타스크에 남는다. 보고 전이면 잘못 붙은
        # 것일 수 있으니 옮겨 준다.
        raise DuetError(
            f"이 세션은 이미 [{current.id}] {current.title}에 붙어 보고까지 했다. "
            "세션 하나는 타스크 하나다."
        )
    task_dir = find_task(task_id)

    # 타스크 하나에는 살아 있는 세션 하나만. 두 창이 같은 일을 동시에 하면 같은 코드를
    # 동시에 건드린다. 이어받기는 앞 세션이 끝나거나 멈춘 뒤다.
    others = [s for s in read_sessions(task_dir) if s.id != session.id and s.alive]
    if others:
        busy = others[0]
        raise DuetError(
            f"[{TASK_DIR.match(task_dir.name).group(1)}]에는 이미 살아 있는 세션이 있다: "
            f"'{busy.title or busy.id}' ({busy.client}). 타스크 하나에는 세션 하나만 붙는다 — "
            "그 세션이 끝나거나 멈춘 뒤에 이어받는다. 같은 일을 다른 창에서 하고 있는 게 "
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


def set_session_status(task_id: str | int, session_id: str, status: str) -> Task:
    """**끝난** 세션의 상태를 사람이 바꾼다 (중지 → 완료, 또는 그 반대).

    살아 있는 세션은 건드리지 않는다. 그 파일의 주인은 그 프로세스이고, 사람이 끼어들면
    "한 파일에 두 주인이 없다"가 깨진다. 도는 세션을 멈추는 것은 `pause_session`이 할 일이다.

    중지된 세션을 `완료`로 바꾸면 자동 규칙이 다시 세서 타스크가 스스로 닫힌다 —
    타스크 상태를 통째로 덮지 않고 닫는 길이 이것이다.
    """
    if status not in CLOSED:
        raise DuetError("세션은 완료 또는 중지로만 바꾼다.")
    task = get_task(task_id)
    for session in task.sessions:
        if session.id == session_id:
            break
    else:
        raise DuetError(f"{task.id}에 {session_id} 세션이 없다.")

    if session.alive:
        raise DuetError("아직 살아 있는 세션이다. 그 창을 닫거나 pause_session을 부르게 하라.")

    session.status = status
    session.by_human = True
    session.save()
    return get_task(task_id)


def archive_task(task_id: str | int) -> Task:
    """타스크 폴더를 `_보관/`으로 옮긴다.

    **지우지 않는다.** 끝난 일을 보드에서 치우는 것과 없애는 것은 다르고, 없애는 쪽은
    되돌릴 수 없다. 폴더째 옮겨 두면 탐색기에서도 그대로 읽히고 언제든 되돌아온다.
    """
    task_dir = find_task(task_id)
    if any(s.alive for s in read_sessions(task_dir)):
        # 폴더를 옮기면 그 세션 프로세스가 기억하고 있는 경로가 끊긴다. 닫고 나서 치운다.
        raise DuetError("살아 있는 세션이 있다. 먼저 닫고 나서 보관한다.")
    target = archive_dir() / task_dir.name
    if target.exists():
        raise DuetError(f"보관함에 같은 이름이 이미 있다: {task_dir.name}")
    task_dir.rename(target)
    return _read_task(target)


def unarchive_task(task_id: str | int) -> Task:
    """보관함에서 도로 꺼낸다."""
    wanted = str(task_id).strip().upper().lstrip("T")
    if not wanted.isdigit():
        raise DuetError(f"타스크 id가 아니다: {task_id}")
    wanted = f"T{int(wanted):03d}"

    for p in archived_dirs():
        if TASK_DIR.match(p.name).group(1) != wanted:
            continue
        target = home() / p.name
        if target.exists():
            raise DuetError(f"보드에 같은 이름이 이미 있다: {p.name}")
        p.rename(target)
        return _read_task(target)
    raise DuetError(f"보관함에 {wanted}가 없다.")


def close_task(task_id: str | int, reason: str = "사람이 타스크를 닫았다") -> Task:
    """이 타스크를 닫는다 (SPEC 7절).

    끝나지 않은 채 죽어 있는 세션을 `중지`로 적고, 타스크를 사람이 정한 `완료`로 둔다.
    **살아 있는 세션은 건드리지 않는다** — 그 파일의 주인은 그 프로세스이고, 우리는 남의
    프로세스를 죽이지 않는다. 그 창을 닫으면 스스로 `중지`로 적힌다. 그동안에도 타스크는
    사람이 정한 `완료`로 남는다.
    """
    task = get_task(task_id)
    for session in task.sessions:
        if session.status in CLOSED or session.alive:
            continue
        session.status = STOPPED
        session.summary = session.summary or reason
        session.by_human = True
        session.save()
    return set_status(task_id, DONE)


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
