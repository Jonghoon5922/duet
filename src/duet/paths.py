"""저장 위치와 폴더 규칙.

Duet은 루트 폴더를 인자로 받지 않는다. 여러 클라이언트에서 뜬 서버 프로세스가
**같은 폴더 하나**를 봐야 세션이 한 타스크에 모이기 때문이다. 위치는 `~/.duet/`로 고정하고,
테스트와 여러 벌 운용을 위해 `DUET_HOME` 으로만 바꾼다.

```
~/.duet/
├── tasks/
│   └── T003-pc101pm-전환/
│       ├── task.md
│       └── sessions/
│           └── s-a1b2.json
└── unassigned/
    └── s-9f8e.json        ← 아직 join 안 한 세션
```

폴더 이름은 사람이 탐색기에서 읽으라고 `T003-<제목>` 꼴이다. **판별은 언제나 앞의 id로 한다** —
제목이 바뀌어도 폴더는 그대로 두므로 뒤쪽 글자는 장식이다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

HOME_ENV = "DUET_HOME"
DEFAULT_DIRNAME = ".duet"

TASKS_DIRNAME = "tasks"
UNASSIGNED_DIRNAME = "unassigned"
SESSIONS_DIRNAME = "sessions"
TASK_FILENAME = "task.md"

#: 타스크 폴더 이름에서 id를 떼는 자리. `T003-무엇` 에서 `T003`.
TASK_DIR_RE = re.compile(r"^(T\d{3,})(?:-.*)?$")

#: 윈도우에서 파일 이름에 못 쓰는 글자 + 제어문자.
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

#: 폴더 이름에 남기는 제목 길이. 경로 길이 제한(윈도우 260자)에 여유를 둔다.
SLUG_MAX = 40


class PathError(Exception):
    """폴더 규칙에 어긋난 요청."""


def home() -> Path:
    """Duet 데이터 폴더. 없으면 만든다."""
    override = os.environ.get(HOME_ENV)
    base = Path(override).expanduser() if override else Path.home() / DEFAULT_DIRNAME
    (base / TASKS_DIRNAME).mkdir(parents=True, exist_ok=True)
    (base / UNASSIGNED_DIRNAME).mkdir(parents=True, exist_ok=True)
    return base


def tasks_dir(base: Path) -> Path:
    return base / TASKS_DIRNAME


def unassigned_dir(base: Path) -> Path:
    return base / UNASSIGNED_DIRNAME


def sessions_dir(task_dir: Path) -> Path:
    return task_dir / SESSIONS_DIRNAME


def task_file(task_dir: Path) -> Path:
    return task_dir / TASK_FILENAME


def slug(title: str) -> str:
    """제목을 폴더 이름 뒤에 붙일 꼴로. 한글은 그대로 남긴다."""
    cleaned = _BAD_CHARS.sub("", title).strip()
    cleaned = re.sub(r"\s+", "-", cleaned)
    cleaned = re.sub(r"-{2,}", "-", cleaned).strip("-. ")
    return cleaned[:SLUG_MAX].strip("-. ")


def normalize_task_id(value: str | int) -> str:
    """`3`, `"3"`, `"t3"`, `"T003"` 을 전부 `T003` 으로.

    Claude가 부르는 자리라 표기가 흔들린다. 받아주는 편이 되묻는 것보다 낫다.
    """
    text = str(value).strip().upper()
    if text.startswith("T"):
        text = text[1:]
    if not text.isdigit():
        raise PathError(f"타스크 id가 아니다: {value}")
    return f"T{int(text):03d}"


def task_dirs(base: Path) -> list[Path]:
    """타스크 폴더 목록 (id 순). 규칙에 안 맞는 폴더는 조용히 건너뛴다."""
    root = tasks_dir(base)
    if not root.is_dir():
        return []
    found = []
    for p in root.iterdir():
        if p.is_dir() and TASK_DIR_RE.match(p.name):
            found.append(p)
    return sorted(found, key=lambda p: p.name)


def task_id_of(task_dir: Path) -> str:
    m = TASK_DIR_RE.match(task_dir.name)
    if not m:
        raise PathError(f"타스크 폴더 이름이 아니다: {task_dir.name}")
    return m.group(1)


def find_task_dir(base: Path, task_id: str | int) -> Path:
    """id로 타스크 폴더를 찾는다. 뒤에 붙은 제목이 무엇이든 상관없다."""
    wanted = normalize_task_id(task_id)
    for p in task_dirs(base):
        if task_id_of(p) == wanted:
            return p
    raise PathError(f"{wanted} 타스크가 없다.")


def next_task_dir(base: Path, title: str) -> Path:
    """다음 번호로 타스크 폴더를 만든다.

    번호는 폴더를 세어 정한다. 두 세션이 동시에 만들면 같은 번호를 노리게 되는데,
    `mkdir`이 원자적이라 진 쪽만 FileExistsError를 받는다. 그때 다음 번호로 밀면 된다.
    """
    root = tasks_dir(base)
    root.mkdir(parents=True, exist_ok=True)
    used = {task_id_of(p) for p in task_dirs(base)}
    number = 1
    while True:
        task_id = f"T{number:03d}"
        if task_id in used:
            number += 1
            continue
        name = f"{task_id}-{slug(title)}" if slug(title) else task_id
        candidate = root / name
        try:
            candidate.mkdir()
        except FileExistsError:
            number += 1
            continue
        sessions_dir(candidate).mkdir(exist_ok=True)
        return candidate
