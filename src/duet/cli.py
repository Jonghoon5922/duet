"""CLI — 서버 띄우기와 눈으로 확인하기.

대시보드가 붙기 전까지 사람이 보는 창구다. 타스크 상태를 바꾸려면 `task.md`를
열어 `상태: 완료` 한 줄을 넣거나 지운다 — 그게 이 도구의 편집 방식이다.
"""

from __future__ import annotations

import sys
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from . import __version__, core

# 윈도우 콘솔의 기본 인코딩은 cp949라 `—` 같은 문자에서 죽는다. 한 번 UTF-8로 돌린다.
# serve 중 stdout은 MCP 채널이지만, SDK가 파일 서술자 1을 직접 가져가므로 영향이 없다.
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

COLOR = {"대기": "dim", "진행중": "cyan", "완료": "green", "중지": "yellow", "취소": "red"}


def _paint(status: str) -> str:
    return f"[{COLOR.get(status, 'white')}]{status}[/]"


def _when(stamp: str) -> str:
    return stamp.replace("T", " ").split(".")[0]


@app.command()
def serve() -> None:
    """MCP(stdio) 서버. 이 프로세스가 세션 하나로 등록된다."""
    from .server import serve as _serve

    _serve()


@app.command()
def add(
    title: str = typer.Argument(..., help="타스크 제목"),
    description: str = typer.Option("", "--desc", "-d"),
) -> None:
    """타스크를 만든다. `~/.duet/` 아래 폴더 하나가 생긴다."""
    try:
        task = core.create_task(title, description)
    except (core.DuetError, OSError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)
    console.print(f"[{task.id}] {task.title} — {_paint(task.status)}")
    console.print(f"[dim]{task.dir}[/dim]")


@app.command("list")
def list_tasks(status: Optional[str] = typer.Option(None, "--status", "-s")) -> None:
    """타스크 목록 (마지막 활동순)."""
    tasks = core.list_tasks(status)
    if not tasks:
        console.print("[dim]타스크가 없다. `duet add <제목>` 으로 만든다.[/dim]")
        return

    table = Table(box=None, pad_edge=False)
    for column in ("id", "상태", "제목", "세션", "마지막 활동"):
        table.add_column(column)
    for t in tasks:
        c = t.counts
        table.add_row(
            t.id,
            _paint(t.status) + (" 🔒" if t.override else ""),
            t.title + ("  [yellow]⚠[/yellow]" if t.warning else ""),
            f"{c['진행중']}/{c['완료']}/{c['중지']}",
            f"[dim]{_when(t.last_activity)}[/dim]",
        )
    console.print(table)
    console.print("[dim]세션 칸은 진행중/완료/중지 · 🔒 사람이 정한 상태 · ⚠ 중지된 세션[/dim]")


@app.command()
def show(task_id: str = typer.Argument(..., help="타스크 id (T001 또는 1)")) -> None:
    """타스크 하나의 세션과 진행 로그."""
    try:
        task = core.get_task(task_id)
    except (core.DuetError, OSError) as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"[bold][{task.id}] {task.title}[/bold] — {_paint(task.status)}")
    if task.description:
        console.print(task.description)
    if task.warning:
        console.print(f"[yellow]⚠ {task.warning}[/yellow]")
    console.print(f"[dim]{task.dir}[/dim]\n")

    if not task.sessions:
        console.print("[dim]참여한 세션이 없다.[/dim]")
        return
    for s in task.sessions:
        mark = "[green]●[/green]" if s.alive else " "
        console.print(f"{mark} [bold]{s.title or s.id}[/bold] {_paint(s.shown_status)} [dim]{s.client}[/dim]")
        for entry in s.progress:
            pct = f" ({entry['pct']}%)" if entry.get("pct") is not None else ""
            console.print(f"    [dim]{_when(entry['t'])}[/dim] {entry['msg']}{pct}")
        if s.summary:
            console.print(f"    [green]→ {s.summary}[/green]")
        console.print()


@app.command()
def version() -> None:
    """버전과 저장 위치."""
    console.print(f"Duet {__version__}  [dim]{core.home()}[/dim]")


@app.command()
def ui(
    port: int = typer.Option(8737, "--port", "-p"),
    no_open: bool = typer.Option(False, "--no-open", help="브라우저를 열지 않는다"),
) -> None:
    """대시보드. 127.0.0.1에서만 듣는다."""
    from .web import DEFAULT_HOST, serve_ui

    console.print(f"[dim]http://{DEFAULT_HOST}:{port}  ({core.home()})[/dim]")
    serve_ui(port=port, open_browser=not no_open)
