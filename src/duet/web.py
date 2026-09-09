"""대시보드 (로컬 웹, 127.0.0.1).

폴더를 읽어 그대로 보여준다. 상태를 저장하지 않는 설계라 서버가 들고 있는 것도 없다 —
요청이 올 때마다 `~/.duet/`를 다시 읽는다. 그래서 사람이 탐색기에서 `task.md`를
고쳐도 새로고침 한 번이면 반영된다.

지금은 화면이 2초마다 다시 물어본다. 파일 감시(watchdog) + SSE는 뒤 단계에서.
바깥에 열지 않는다. 127.0.0.1만 듣는다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import __version__, core

PAGE = Path(__file__).parent / "static" / "board.html"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8737


class StatusIn(BaseModel):
    #: None이면 `상태:` 줄을 지운다 = 다시 세션에서 센다.
    status: str | None = None


def _task_row(task: core.Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "title": task.title,
        "status": task.status,
        "counts": task.counts,
        "override": task.override,
        "warning": task.warning,
        "last_activity": task.last_activity,
    }


def _task_detail(task: core.Task) -> dict[str, Any]:
    return _task_row(task) | {
        "description": task.description,
        "folder": str(task.dir),
        "sessions": [
            {
                "id": s.id,
                "title": s.title,
                "client": s.client,
                "status": s.shown_status,
                "alive": s.alive,
                "started": s.started,
                "heartbeat": s.heartbeat,
                "summary": s.summary,
                "progress": s.progress,
            }
            for s in task.sessions
        ],
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Duet", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/", response_class=HTMLResponse)
    def board() -> str:
        # 요청마다 읽는다. 고치는 동안 서버를 다시 띄우지 않아도 되게.
        return PAGE.read_text(encoding="utf-8")

    @app.get("/api/tasks")
    def tasks(status: str | None = None) -> dict[str, Any]:
        rows = [_task_row(t) for t in core.list_tasks(status)]
        return {"tasks": rows, "statuses": list(core.TASK_STATUSES), "home": str(core.home())}

    @app.get("/api/tasks/{task_id}")
    def task(task_id: str) -> dict[str, Any]:
        try:
            return _task_detail(core.get_task(task_id))
        except (core.DuetError, OSError) as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.post("/api/tasks/{task_id}/status")
    def set_status(task_id: str, body: StatusIn) -> dict[str, Any]:
        """사람이 정한 상태를 `task.md`에 적거나(=자동 규칙보다 우선) 지운다."""
        try:
            return _task_detail(core.set_status(task_id, body.status))
        except core.DuetError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e))

    return app


def serve_ui(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = True) -> None:
    import uvicorn

    if open_browser:
        import threading
        import webbrowser

        threading.Timer(0.7, lambda: webbrowser.open(f"http://{host}:{port}")).start()
    uvicorn.run(create_app(), host=host, port=port, log_level="warning")
