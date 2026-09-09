"""MCP 서버 (stdio). 이 프로세스가 세션 하나다.

뜰 때 `_미참여/`에 자기 파일을 만들고, 30초마다 하트비트를 찍고, 끝날 때 `중지`로
적는다. 훅조차 못 돌면 하트비트 90초 초과를 읽는 쪽이 잡는다 (core.reap).

stdout은 MCP 프로토콜 채널이다. 이 모듈은 stdout에 아무것도 출력하지 않는다.
"""

from __future__ import annotations

import atexit
import os
import threading
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from . import __version__, core

#: `initialize`에서 클라이언트가 밝히는 이름 → 사람이 읽는 이름.
#: Claude Desktop은 `local-agent-mode-<서버이름>` 으로 온다 (서재에서 실측).
CLIENT_LABELS = {"claude-ai": "Claude Desktop", "claude-code": "Claude Code", "cursor": "Cursor"}


def label_client(name: str | None) -> str:
    if not name:
        return "알 수 없음"
    if name.lower().startswith("local-agent-mode-"):
        return "Claude Desktop"
    return CLIENT_LABELS.get(name.lower(), name)


def build_instructions() -> str:
    """열린 타스크 목록을 instructions에 넣는다. 연결할 때 한 번만 전달된다."""
    open_tasks = [t for t in core.list_tasks() if t.status in (core.WAITING, core.RUNNING)]
    lines = [
        "이 세션이 어떤 일감(타스크)에 속하는지 기록하는 도구다. "
        "진행 로그는 네가 직접 쓴다 — 이 도구는 아무것도 요약해주지 않는다.",
        "",
    ]
    if open_tasks:
        lines.append("열린 타스크:")
        for t in open_tasks[:12]:
            lines.append(f"- [{t.id}] {t.title} ({t.status}, 세션 {t.counts[core.RUNNING]}개 진행중)")
    else:
        lines.append("지금 열린 타스크가 없다. 새 일이면 create_task로 만든다.")

    lines += [
        "",
        "이 세션은 아직 어떤 타스크에도 참여하지 않았다. 사용자가 작업을 지시하면 어느 타스크인지 "
        "확인하고 join_task를 호출하라. 새 일이면 create_task 후 join_task.",
        "작업 중간에 report_progress로 한 줄씩 남기고, 끝나면 complete_session을 호출하라.",
        "사용자가 타스크를 언급하지 않으면 묻지 말고 작업을 먼저 하되, 첫 보고 시점에 한 번만 확인한다.",
    ]
    return "\n".join(lines)


def create_server(session: core.Session, lock: threading.Lock) -> MCPServer:
    server = MCPServer(
        name="duet", title="Duet", version=__version__, instructions=build_instructions()
    )

    def touch(ctx: Context) -> None:
        """도구 호출은 그 자체로 살아있다는 신호다. 김에 클라이언트 이름도 확정한다."""
        try:
            params = ctx.request_context.session.client_params
            info = params.client_info if params else None
            label = label_client(info.name if info else None)
            with lock:
                if label != "알 수 없음":
                    session.client = label
                core.heartbeat(session)
        except Exception:
            pass

    @server.tool(
        name="list_tasks",
        description="타스크 목록. 어느 타스크에 참여할지 고를 때 먼저 호출한다.",
    )
    def list_tasks_tool(ctx: Context, status: str | None = None) -> dict[str, Any]:
        touch(ctx)
        joined = core.task_of(session)
        return {
            "joined_task": joined.id if joined else None,
            "tasks": [t.to_dict() for t in core.list_tasks(status)],
        }

    @server.tool(
        name="create_task",
        description=(
            "새 타스크를 만든다(`대기`). 참여까지 하지는 않으므로, 이 세션이 그 일을 "
            "할 것이면 이어서 join_task를 호출하라."
        ),
    )
    def create_task_tool(ctx: Context, title: str, description: str = "") -> dict[str, Any]:
        touch(ctx)
        try:
            task = core.create_task(title, description)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        return {"task": task.to_dict(), "next": "이 세션이 이 일을 한다면 join_task를 호출하라."}

    @server.tool(
        name="join_task",
        description=(
            "이 세션을 타스크에 묶는다. 타스크가 `진행중`이 되고 이 세션의 보고가 "
            "그 타스크에 쌓인다. 세션 하나는 타스크 하나에만 붙는다."
        ),
    )
    def join_task_tool(ctx: Context, task_id: str, session_title: str = "") -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                task = core.join(session, task_id, session_title)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        return {
            "task": task.to_dict(),
            "note": "작업 중간에 report_progress, 끝나면 complete_session을 호출하라.",
        }

    @server.tool(
        name="report_progress",
        description=(
            "이 세션의 진행 로그를 한 줄 남긴다. 다음 세션이 읽고 이어받을 수 있게 "
            "무엇을 했고 어디까지 됐는지 사실만 쓴다."
        ),
    )
    def report_progress_tool(
        ctx: Context, message: str, percent: int | None = None
    ) -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                entry = core.report(session, message, percent)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        task = core.task_of(session)
        result = {"t": entry["t"], "task_id": task.id if task else None}
        if task is None:
            result["note"] = (
                "아직 타스크에 참여하지 않았다. join_task를 부르면 이 보고도 함께 따라간다."
            )
        return result

    @server.tool(
        name="complete_session",
        description=(
            "이 세션의 일이 끝났다고 보고한다. 타스크의 세션이 전부 완료면 타스크도 "
            "`완료`가 된다. summary는 사람이 읽는 마무리 한 줄이다."
        ),
    )
    def complete_session_tool(ctx: Context, summary: str) -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                task = core.finish(session, core.DONE, summary)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        if task is None:
            return {"status": core.DONE, "note": "타스크에 참여하지 않아 닫히기만 했다."}

        if task.status == core.DONE:
            note = f"[{task.id}] {task.title} 타스크가 자동 완료됐다."
        elif task.warning:
            note = task.warning
        else:
            note = f"아직 진행중인 세션이 {task.counts[core.RUNNING]}개 있다."
        return {"status": core.DONE, "task": task.to_dict(), "note": note}

    return server


def serve() -> None:
    """이 함수가 도는 동안이 곧 세션 하나의 수명이다."""
    session = core.register_session(client=_guess_client())
    lock = threading.Lock()
    stop = threading.Event()
    closed = threading.Event()

    def beat() -> None:
        while not stop.wait(core.HEARTBEAT_SEC):
            try:
                with lock:
                    core.heartbeat(session)
            except OSError:
                continue  # 잠깐 못 썼을 뿐이다. 다음 박자에 다시 친다

    def close() -> None:
        """창이 닫혔다. 보고 없이 끝났으면 `중지`로 남긴다."""
        if closed.is_set():
            return
        closed.set()
        stop.set()
        try:
            with lock:
                core.finish(session, core.STOPPED, "세션 창이 닫힘 (보고 없이 종료)")
        except Exception:
            pass  # 종료 경로다. 여기서 예외를 올리면 보이는 건 스택뿐이다

    threading.Thread(target=beat, name="duet-heartbeat", daemon=True).start()
    atexit.register(close)
    try:
        create_server(session, lock).run("stdio")
    finally:
        close()


def _guess_client() -> str:
    """등록 시점 추정치. 첫 도구 호출 때 진짜 이름으로 덮어쓴다."""
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "Claude Code"
    if os.environ.get("CURSOR_TRACE_ID"):
        return "Cursor"
    return "알 수 없음"
