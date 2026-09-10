"""대시보드 (로컬 웹, 127.0.0.1).

폴더를 읽어 그대로 보여준다. 상태를 저장하지 않는 설계라 서버가 들고 있는 것도 없다 —
요청이 올 때마다 `~/.duet/`를 다시 읽는다. 그래서 사람이 탐색기에서 `task.md`를
고쳐도 새로고침 한 번이면 반영된다.

지금은 화면이 2초마다 다시 물어본다. 파일 감시(watchdog) + SSE는 뒤 단계에서.
바깥에 열지 않는다. 127.0.0.1만 듣는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import __version__, core

PAGE = Path(__file__).parent / "static" / "board.html"

DEFAULT_HOST = "127.0.0.1"
#: 딴 프로그램이 이 포트를 쓰면 DUET_PORT 로 바꾼다.
DEFAULT_PORT = int(os.environ.get("DUET_PORT", "8737"))


class StatusIn(BaseModel):
    #: None이면 `상태:` 줄을 지운다 = 다시 세션에서 센다.
    status: str | None = None


class TaskIn(BaseModel):
    title: str
    description: str = ""
    project: str = ""


class EditIn(BaseModel):
    #: 준 것만 바뀐다. None은 "안 건드림"이다.
    title: str | None = None
    description: str | None = None
    #: 빈 문자열이면 `프로젝트:` 줄을 지워 다시 세게 한다.
    project: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="Duet", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def board() -> str:
        # 요청마다 읽는다. 고치는 동안 서버를 다시 띄우지 않아도 되게.
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/tasks")
    def tasks(status: str | None = None) -> dict[str, Any]:
        # 카드가 세션 줄까지 그리므로 상세째로 준다. 어차피 list_tasks가 세션 파일을
        # 이미 읽었다 — 개인용 규모에서 2초마다 이걸 보내도 무겁지 않다.
        rows = [t.to_detail() for t in core.list_tasks(status)]
        return {
            "tasks": rows,
            "statuses": list(core.TASK_STATUSES),
            "home": str(core.home()),
            "archived": len(core.archived_dirs()),
            # 대시보드를 띄운 폴더. 지금 어느 프로젝트를 보고 있는지의 기준이다.
            "here": core.project_name(str(Path.cwd())),
        }

    @app.post("/api/tasks", status_code=201)
    def new_task(body: TaskIn) -> dict[str, Any]:
        try:
            return core.create_task(body.title, body.description, body.project).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.patch("/api/tasks/{task_id}")
    def edit_task(task_id: str, body: EditIn) -> dict[str, Any]:
        """제목·설명 인라인 편집. 사람이 정한 상태는 건드리지 않는다."""
        try:
            return core.update_task(task_id, body.title, body.description, body.project).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/archive")
    def archive() -> dict[str, Any]:
        rows = [t.to_detail() for t in core.list_archived()]
        return {"tasks": rows, "archived": len(rows), "home": str(core.home())}

    @app.post("/api/tasks/{task_id}/archive")
    def archive_task(task_id: str) -> dict[str, Any]:
        try:
            return core.archive_task(task_id).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/tasks/{task_id}/unarchive")
    def unarchive_task(task_id: str) -> dict[str, Any]:
        try:
            return core.unarchive_task(task_id).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/tasks/{task_id}/close")
    def close_task(task_id: str) -> dict[str, Any]:
        """이 타스크를 닫는다. 살아 있는 세션은 그대로 두고 알려만 준다."""
        try:
            task = core.close_task(task_id)
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

        alive = task.counts[core.RUNNING]
        return task.to_detail() | {
            "note": f"살아 있는 세션 {alive}개는 그대로 둔다. 그 창을 닫으면 스스로 중지로 적힌다."
            if alive else "닫았다."
        }

    @app.post("/api/tasks/{task_id}/sessions/{session_id}/status")
    def set_session_status(task_id: str, session_id: str, body: StatusIn) -> dict[str, Any]:
        """끝난 세션을 사람이 완료/중지로 바꾼다. 살아 있는 세션은 거부된다."""
        if body.status is None:
            raise HTTPException(status_code=400, detail="세션은 자동으로 되돌릴 수 없다.")
        try:
            return core.set_session_status(task_id, session_id, body.status).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict[str, Any]:
        try:
            return core.get_task(task_id).to_detail()
        except (core.DuetError, OSError) as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/tasks/{task_id}/status")
    def set_status(task_id: str, body: StatusIn) -> dict[str, Any]:
        """사람이 정한 상태를 `task.md`에 적거나(=자동 규칙보다 우선) 지운다."""
        try:
            return core.set_status(task_id, body.status).to_detail()
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app


def board_url(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> str:
    return f"http://{host}:{port}"


def is_port_free(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> bool:
    """그 포트를 지금 잡을 수 있나. 이미 다른 Duet이 보드를 띄웠으면 False."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def serve_ui(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = True,
    idle_minutes: float | None = None,
) -> None:
    """보드를 띄운다. 이 호출은 서버가 내려갈 때까지 돌아오지 않는다.

    `idle_minutes` 를 주면 그 시간 동안 아무도 안 보면 스스로 내려간다 — 창 없는
    실행 파일로 띄울 때 끄는 손이 없어서다. MCP 서버가 겸할 때는 주지 않는다
    (그 프로세스는 Claude 창과 수명을 같이한다).

    stdout에는 아무것도 쓰지 않는다. MCP 서버가 겸할 때 stdout은 프로토콜 통로다.
    """
    import threading
    import time

    import uvicorn

    app = create_app()
    last_seen = {"t": time.monotonic()}

    @app.middleware("http")
    async def touch(request, call_next):  # noqa: ANN001
        last_seen["t"] = time.monotonic()
        return await call_next(request)

    config = uvicorn.Config(
        app, host=host, port=port, log_level="warning", log_config=None, access_log=False
    )
    server = uvicorn.Server(config)

    if idle_minutes:
        def watch() -> None:
            while not server.should_exit:
                time.sleep(15)
                if time.monotonic() - last_seen["t"] > idle_minutes * 60:
                    server.should_exit = True  # 아무도 안 본다. 조용히 내려간다
        threading.Thread(target=watch, name="duet-board-idle", daemon=True).start()

    if open_browser:
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(board_url(host, port))).start()

    server.run()
