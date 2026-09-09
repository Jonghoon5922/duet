"""타스크 파일 (`task.md`) 읽고 쓰기.

프론트매터가 정의, 본문이 설명, `## 이력` 절이 상태 전이 기록이다.

```markdown
---
id: T003
title: pc101pm 전환
status_override: null
tags: [nefss]
created: 2026-09-09T14:00:00
---

NEFSS pc101pm 화면을 BXM으로 옮긴다.

## 이력
- 2026-09-09T14:00:00 세션 · 생성
- 2026-09-09T14:02:11 자동 · 대기 → 진행중 (세션 참여)
```

**`status`는 적지 않는다.** 타스크 상태는 세션 파일들에서 계산하는 값이고, 사람이 정한 것만
`status_override`에 남는다 (SPEC 9절). 그래서 자동과 수동이 같은 칸을 두고 다툴 일이 없다.
**`status_override`가 채워져 있는 것이 곧 수동 잠금이다.** 잠금을 푸는 것은 그 칸을 비우는 것.

사람이 이 파일을 직접 고치는 것도 정식 편집 방법이다. 그래서 모르는 프론트매터 키도
버리지 않고 그대로 되돌려 쓴다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .paths import task_file
from .states import TASK_STATUSES, now

HISTORY_HEADING = "## 이력"

#: 프론트매터에서 우리가 뜻을 아는 키. 이 순서로 다시 쓴다.
KNOWN_KEYS = ("id", "title", "status_override", "tags", "created")


class TaskFileError(Exception):
    """읽을 수 없거나 규칙에 어긋난 task.md."""


@dataclass
class TaskFile:
    id: str
    title: str
    description: str = ""
    status_override: str | None = None
    tags: list[str] = field(default_factory=list)
    created: str = ""
    #: `## 이력` 절의 줄들 (앞의 `- ` 를 뗀 상태). 사람이 쓴 줄도 그대로 남는다.
    history: list[str] = field(default_factory=list)
    #: 우리가 모르는 프론트매터 키. 사람이 적어 둔 것을 지우지 않으려고 들고 있는다.
    extra: dict[str, Any] = field(default_factory=dict)
    path: Path | None = field(default=None, compare=False, repr=False)

    @property
    def locked(self) -> bool:
        """수동 잠금 = 사람이 정한 상태가 적혀 있다."""
        return self.status_override is not None


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        return {}, text
    head = parts[0][3:]
    body = parts[1].lstrip("-").lstrip("\n")
    try:
        data = yaml.safe_load(head) or {}
    except yaml.YAMLError as e:
        raise TaskFileError(f"프론트매터를 읽지 못했다: {e}") from e
    if not isinstance(data, dict):
        raise TaskFileError("프론트매터가 키-값 꼴이 아니다.")
    return data, body


def _split_history(body: str) -> tuple[str, list[str]]:
    lines = body.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == HISTORY_HEADING:
            description = "\n".join(lines[:i]).strip()
            history = [
                ln.strip()[2:].strip()
                for ln in lines[i + 1:]
                if ln.strip().startswith("- ")
            ]
            return description, history
    return body.strip(), []


def read(task_dir: Path) -> TaskFile:
    path = task_file(task_dir)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise TaskFileError(f"{path.name}을 읽지 못했다: {e}") from e

    data, body = _split_frontmatter(text)
    description, history = _split_history(body)

    tags = data.get("tags") or []
    if isinstance(tags, str):
        tags = [t.strip() for t in tags.split(",") if t.strip()]

    override = data.get("status_override")
    if override is not None:
        override = str(override).strip() or None
    if override is not None and override not in TASK_STATUSES:
        # 사람이 오타를 냈다. 무시하고 계산값을 쓰되, 파일은 건드리지 않는다.
        override = None

    return TaskFile(
        id=str(data.get("id") or task_dir.name.split("-")[0]),
        title=str(data.get("title") or task_dir.name),
        description=description,
        status_override=override,
        tags=[str(t) for t in tags],
        created=str(data.get("created") or ""),
        history=history,
        extra={k: v for k, v in data.items() if k not in KNOWN_KEYS},
        path=path,
    )


def render(task: TaskFile) -> str:
    front: dict[str, Any] = {
        "id": task.id,
        "title": task.title,
        "status_override": task.status_override,
        "tags": task.tags,
        "created": task.created,
    }
    front.update(task.extra)
    head = yaml.safe_dump(front, allow_unicode=True, sort_keys=False, default_flow_style=False)

    out = ["---", head.rstrip("\n"), "---", ""]
    if task.description.strip():
        out += [task.description.strip(), ""]
    out.append(HISTORY_HEADING)
    out += [f"- {line}" for line in task.history]
    return "\n".join(out) + "\n"


def write(task: TaskFile) -> None:
    """임시 파일에 쓰고 바꿔치기한다. 대시보드가 읽는 중일 수 있다."""
    if task.path is None:
        raise TaskFileError("task.md 경로가 없다.")
    tmp = task.path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(render(task), encoding="utf-8")
    os.replace(tmp, task.path)


def create(
    task_dir: Path, task_id: str, title: str, description: str = "", tags: list[str] | None = None
) -> TaskFile:
    task = TaskFile(
        id=task_id,
        title=title,
        description=description,
        tags=list(tags or []),
        created=now(),
        path=task_file(task_dir),
    )
    add_history(task, "세션", "생성", write_now=False)
    write(task)
    return task


def add_history(task: TaskFile, actor: str, text: str, *, write_now: bool = True) -> None:
    """이력 한 줄. `- 2026-09-09T14:02:11 자동 · 대기 → 진행중`"""
    task.history.append(f"{now()} {actor} · {text}")
    if write_now:
        write(task)
