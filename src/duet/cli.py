"""CLI.

서버(serve) · 타스크(list/add/set/unlock) · 세션 현황(sessions).
대시보드(ui/app)는 뒤 단계에서 붙는다.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, paths, store
from .states import STALE_SEC, TASK_STATUSES

# 윈도우 콘솔·파이프의 기본 인코딩은 cp949다. 한글은 넘어가지만 `—` 같은 문자에서
# UnicodeEncodeError로 죽는다. 그래서 진입 시점에 한 번 UTF-8로 돌린다.
#
# serve 중에는 stdout이 MCP 프로토콜 채널이지만, MCP SDK가 파일 서술자 1을 직접
# 가져가 자기 인코딩으로 감싸므로 여기서 손대도 영향이 없다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

app = typer.Typer(
    name="duet",
    help="Duet — 나와 Claude가 함께 하는 일을 타스크로 묶는 개인용 로컬 PMS.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()

_STATUS_STYLE = {
    "대기": "dim",
    "진행중": "cyan",
    "완료": "green",
    "중지": "yellow",
    "취소": "red",
    "미참여": "dim",
}


def _when(stamp: str) -> str:
    """화면에 쓰는 시각. 파일에는 밀리초까지 적지만 사람에게 보일 때는 초까지면 된다."""
    return stamp.replace("T", " ").split(".")[0]


def _paint(status: str) -> str:
    return f"[{_STATUS_STYLE.get(status, 'white')}]{status}[/]"


def _fail(message: str) -> None:
    console.print(f"[red]{message}[/red]")
    raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """버전 표시."""
    console.print(f"Duet {__version__}  [dim]{paths.home()}[/dim]")


@app.command()
def serve() -> None:
    """MCP(stdio) 서버. 이 프로세스가 세션 하나로 등록되고 하트비트를 친다."""
    from .server import serve as _serve

    _serve()


@app.command("list")
def list_tasks(
    status: Optional[str] = typer.Option(
        None, "--status", "-s", help=" / ".join(TASK_STATUSES)
    ),
) -> None:
    """타스크 목록 (마지막 활동순)."""
    try:
        tasks = store.list_tasks(paths.home(), status=status)
    except store.StoreError as e:
        _fail(str(e))

    if not tasks:
        console.print("[dim]타스크가 없다. `duet add <제목>` 으로 만든다.[/dim]")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("id", style="bold")
    table.add_column("상태")
    table.add_column("제목")
    table.add_column("세션", justify="center")
    table.add_column("진행", justify="right")
    table.add_column("마지막 활동", style="dim")

    for t in tasks:
        c = t.counts
        table.add_row(
            t.id,
            _paint(t.status) + (" 🔒" if t.locked else ""),
            t.title + ("  [yellow]⚠[/yellow]" if t.warning else ""),
            f"{c.running}/{c.done}/{c.stopped}",
            f"{t.progress}%",
            _when(t.last_activity),
        )
    console.print(table)
    console.print("[dim]세션 칸은 진행중/완료/중지 · 🔒 수동 잠금 · ⚠ 중지된 세션 있음[/dim]")


@app.command()
def add(
    title: str = typer.Argument(..., help="타스크 제목"),
    description: str = typer.Option("", "--desc", "-d", help="설명"),
    tag: list[str] = typer.Option([], "--tag", "-t", help="태그 (여러 번 줄 수 있다)"),
) -> None:
    """타스크를 만든다 (`대기`). `~/.duet/tasks/` 아래 폴더 하나가 생긴다."""
    try:
        task = store.create_task(paths.home(), title, description, list(tag))
    except (store.StoreError, OSError) as e:
        _fail(str(e))
    console.print(f"[{task.id}] {task.title} — {_paint(task.status)}")
    console.print(f"[dim]{paths.tasks_dir(paths.home()) / task.dirname}[/dim]")


@app.command("set")
def set_status(
    task_id: str = typer.Argument(..., help="타스크 id (T003 또는 3)"),
    status: str = typer.Argument(..., help=" / ".join(TASK_STATUSES)),
) -> None:
    """타스크 상태를 사람이 정한다. `status_override`에 적혀 자동 규칙보다 우선한다."""
    try:
        task = store.set_task_status(paths.home(), task_id, status)
    except (store.StoreError, paths.PathError, OSError) as e:
        _fail(str(e))
    console.print(
        f"[{task.id}] {task.title} → {_paint(task.status)} [dim](수동 잠금 — `duet unlock {task.id}` 로 푼다)[/dim]"
    )


@app.command()
def unlock(task_id: str = typer.Argument(..., help="타스크 id")) -> None:
    """수동 잠금을 푼다. 상태가 다시 세션에서 계산된다."""
    try:
        task = store.unlock_task(paths.home(), task_id)
    except (store.StoreError, paths.PathError, OSError) as e:
        _fail(str(e))
    console.print(f"[{task.id}] {task.title} → {_paint(task.status)} [dim](자동 판정)[/dim]")


@app.command()
def sessions(
    task_id: Optional[str] = typer.Argument(None, help="타스크 id (없으면 전부)"),
) -> None:
    """세션 현황. 하트비트가 도는 세션에 ● 표시."""
    try:
        rows = store.list_sessions(paths.home(), task_id=task_id)
    except (store.StoreError, paths.PathError) as e:
        _fail(str(e))

    if not rows:
        console.print("[dim]세션이 없다.[/dim]")
        return

    table = Table(box=None, pad_edge=False)
    table.add_column("", justify="center")
    table.add_column("세션", style="dim")
    table.add_column("타스크")
    table.add_column("상태")
    table.add_column("제목")
    table.add_column("클라이언트")
    table.add_column("마지막 하트비트", style="dim")

    for tid, s in rows:
        table.add_row(
            "[green]●[/green]" if s.alive else " ",
            s.id,
            tid or "-",
            _paint(store.effective_status(s)),
            s.title or "-",
            s.client,
            _when(s.completed or s.heartbeat),
        )
    console.print(table)
    console.print(f"[dim]● = 하트비트 {int(STALE_SEC)}초 안에 도착[/dim]")


@app.command()
def show(task_id: str = typer.Argument(..., help="타스크 id")) -> None:
    """타스크 하나의 세션과 진행 로그를 시간순으로 본다."""
    try:
        task = store.get_task(paths.home(), task_id)
    except (store.StoreError, paths.PathError) as e:
        _fail(str(e))

    console.print(f"[bold][{task.id}] {task.title}[/bold] — {_paint(task.status)}")
    if task.description:
        console.print(task.description)
    if task.warning:
        console.print(f"[yellow]⚠ {task.warning}[/yellow]")
    console.print()

    if not task.sessions:
        console.print("[dim]참여한 세션이 없다.[/dim]")
        return

    for s in task.sessions:
        mark = "[green]●[/green]" if s.alive else " "
        console.print(
            f"{mark} [bold]{s.title or s.id}[/bold] "
            f"{_paint(store.effective_status(s))} [dim]{s.client} · {s.id}[/dim]"
        )
        for entry in s.progress:
            pct = f" ({entry['pct']}%)" if entry.get("pct") is not None else ""
            console.print(f"    [dim]{_when(entry['t'])}[/dim] {entry['msg']}{pct}")
        if s.summary:
            console.print(f"    [green]→ {s.summary}[/green]")
        console.print()
