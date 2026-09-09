"""MCP 서버 (stdio).

이 프로세스가 세션 하나다. 뜰 때 자기 세션 파일을 만들고, Claude가 도구로
참여·보고·완료를 적는다.

stdout은 MCP 프로토콜 채널이다. 이 모듈은 stdout에 아무것도 출력하지 않는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from . import __version__, paths, store
from .session import SessionHandle
from .states import SESSION_DONE, TASK_DONE, TASK_RUNNING, TASK_WAITING

#: instructions에 열거하는 열린 타스크 최대 개수. 넘으면 마지막 활동순으로 자른다.
MAX_LISTED_TASKS = 12


def build_instructions(home: Path) -> str:
    """열린 타스크 목록을 서버 instructions로 만든다 (SPEC 4절).

    instructions는 연결할 때 한 번만 전달된다. 그래서 "지금 무엇이 열려 있나"만 넣고,
    최신 상태가 필요하면 `list_tasks`를 부르라고 안내한다.
    """
    open_tasks = [t for t in store.list_tasks(home) if t.status in (TASK_WAITING, TASK_RUNNING)]

    lines = [
        "이 세션이 어떤 일감(타스크)에 속하는지 기록하는 도구다. "
        "진행 로그는 네가 직접 쓴다 — 이 도구는 아무것도 요약해주지 않는다.",
        "",
    ]

    if open_tasks:
        lines.append("열린 타스크:")
        for t in open_tasks[:MAX_LISTED_TASKS]:
            c = t.counts
            live = f"세션 {c.running}개 진행중" if c.running else f"세션 {c.total}개"
            lines.append(f"- [{t.id}] {t.title} ({t.status}, {live})")
        if len(open_tasks) > MAX_LISTED_TASKS:
            lines.append(f"- … 외 {len(open_tasks) - MAX_LISTED_TASKS}개 (list_tasks로 전부 본다)")
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


def create_server(home: Path, handle: SessionHandle) -> MCPServer:
    """이 프로세스의 세션에 묶인 MCP 서버를 만든다."""
    server = MCPServer(
        name="duet",
        title="Duet",
        version=__version__,
        instructions=build_instructions(home),
    )

    def touch(ctx: Context) -> None:
        """도구 호출은 그 자체로 살아있다는 신호다. 김에 클라이언트 이름도 확정한다."""
        try:
            params = ctx.request_context.session.client_params
            info = params.client_info if params else None
            handle.confirm_client(info.name if info else None, info.version if info else None)
        except Exception:
            pass
        try:
            with handle.lock:
                if handle.file is not None:
                    store.heartbeat(handle.file)
        except OSError:
            pass

    @server.tool(
        name="list_tasks",
        description=(
            "타스크 목록. 세션 수·상태·마지막 활동이 함께 온다. "
            "어느 타스크에 참여할지 고를 때 먼저 호출한다."
        ),
    )
    def list_tasks_tool(ctx: Context, status: str | None = None) -> dict[str, Any]:
        touch(ctx)
        try:
            tasks = store.list_tasks(home, status=status)
        except store.StoreError as e:
            return {"error": str(e)}
        return {
            "session_id": handle.id,
            "joined_task": _joined_task_id(home, handle),
            "tasks": [t.to_dict() for t in tasks],
        }

    @server.tool(
        name="create_task",
        description=(
            "새 타스크를 만든다(`대기` 상태). 만들기만 하고 참여하지는 않으므로, "
            "이 세션이 그 일을 할 것이면 이어서 join_task를 호출하라."
        ),
    )
    def create_task_tool(
        ctx: Context, title: str, description: str = "", tags: list[str] | None = None
    ) -> dict[str, Any]:
        touch(ctx)
        try:
            task = store.create_task(home, title, description, tags)
        except (store.StoreError, OSError) as e:
            return {"error": str(e)}
        return {
            "task": task.to_dict(),
            "folder": task.dirname,
            "next": "이 세션이 이 일을 한다면 join_task를 호출하라.",
        }

    @server.tool(
        name="join_task",
        description=(
            "이 세션을 타스크에 묶는다. 타스크는 `진행중`이 되고, 이 세션의 보고가 "
            "그 타스크의 타임라인에 쌓인다. 세션 하나는 타스크 하나에만 붙는다."
        ),
    )
    def join_task_tool(ctx: Context, task_id: str, session_title: str = "") -> dict[str, Any]:
        touch(ctx)
        if handle.file is None:
            return {"error": "이 세션의 파일이 없다. 서버를 다시 띄워야 한다."}
        try:
            with handle.lock:
                task = store.join_task(home, handle.file, task_id, session_title)
        except (store.StoreError, OSError) as e:
            return {"error": str(e)}
        return {
            "session_id": handle.id,
            "task": task.to_dict(),
            "note": "작업 중간에 report_progress, 끝나면 complete_session을 호출하라.",
        }

    @server.tool(
        name="report_progress",
        description=(
            "이 세션의 진행 로그를 한 줄 남긴다. 대시보드 타임라인에 그대로 뜬다. "
            "다음 세션이 읽고 이어받을 수 있게, 무엇을 했고 어디까지 됐는지 사실만 쓴다."
        ),
    )
    def report_progress_tool(
        ctx: Context, message: str, percent: int | None = None
    ) -> dict[str, Any]:
        touch(ctx)
        if handle.file is None:
            return {"error": "이 세션의 파일이 없다. 서버를 다시 띄워야 한다."}
        try:
            with handle.lock:
                entry = store.report_progress(home, handle.file, message, percent)
                task_id = store.session_task_id(home, handle.file)
        except (store.StoreError, OSError) as e:
            return {"error": str(e)}

        result = {"t": entry["t"], "task_id": task_id, "message": entry["msg"], "percent": entry["pct"]}
        if task_id is None:
            result["note"] = (
                "이 세션은 아직 타스크에 참여하지 않았다. join_task를 부르면 "
                "이 보고까지 함께 그 타스크로 따라간다."
            )
        return result

    @server.tool(
        name="complete_session",
        description=(
            "이 세션의 일이 끝났다고 보고한다. 세션이 `완료`가 되고, 타스크의 세션이 "
            "전부 완료면 타스크도 `완료`로 계산된다. summary는 사람이 읽는 마무리 한 줄이다."
        ),
    )
    def complete_session_tool(ctx: Context, summary: str) -> dict[str, Any]:
        touch(ctx)
        if handle.file is None:
            return {"error": "이 세션의 파일이 없다. 서버를 다시 띄워야 한다."}
        try:
            with handle.lock:
                task = store.close_session(home, handle.file, SESSION_DONE, summary)
        except (store.StoreError, OSError) as e:
            return {"error": str(e)}

        result: dict[str, Any] = {"session_id": handle.id, "status": SESSION_DONE}
        if task is None:
            result["note"] = "이 세션은 타스크에 참여하지 않아 닫히기만 했다."
            return result

        result["task"] = task.to_dict()
        if task.status == TASK_DONE:
            result["note"] = f"[{task.id}] {task.title} 타스크가 자동 완료됐다."
        elif task.counts.stopped:
            result["note"] = (
                f"중지된 세션이 {task.counts.stopped}개 있어 타스크는 열어 둔다. "
                "사람이 대시보드에서 판단한다."
            )
        elif task.locked:
            result["note"] = f"이 타스크는 사람이 `{task.status}`로 정해 둬서 자동 규칙이 덮지 않는다."
        else:
            result["note"] = f"아직 진행중인 세션이 {task.counts.running}개 있다."
        return result

    return server


def _joined_task_id(home: Path, handle: SessionHandle) -> str | None:
    if handle.file is None:
        return None
    return store.session_task_id(home, handle.file)


def serve() -> None:
    """stdio MCP 서버를 띄운다. 이 함수가 도는 동안이 곧 세션 하나의 수명이다."""
    home = paths.home()
    handle = SessionHandle(home)
    handle.start()

    server = create_server(home, handle)
    try:
        server.run("stdio")
    finally:
        # 정상 종료(stdin EOF)든 예외든 여기서 `중지`를 적는다.
        # 이미 complete_session으로 닫혔으면 store가 덮지 않는다.
        handle.stop()
