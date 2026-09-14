"""대시보드 (로컬 웹, 127.0.0.1).

폴더를 읽어 그대로 보여준다. 상태를 저장하지 않는 설계라 서버가 들고 있는 것도 없다 —
요청이 올 때마다 `~/.duet/`를 다시 읽는다. 그래서 사람이 탐색기에서 `task.md`를
고쳐도 새로고침 한 번이면 반영된다.

타스크 주소는 `/api/tasks/<프로젝트>/<T00n>` 이다. 프로젝트를 모르는 것은 `_미분류`.

화면은 `/api/events`(SSE)를 열어 두고, 폴더가 바뀌면 그때만 다시 읽는다. 감시기는
따로 없다 — 서버가 반 초마다 파일 수정 시각을 훑어 지문이 달라졌을 때만 신호를 보낸다.
바깥에 열지 않는다. 127.0.0.1만 듣는다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

from . import __version__, core

PAGE = Path(__file__).parent / "static" / "board.html"

DEFAULT_HOST = "127.0.0.1"
#: 딴 프로그램이 이 포트를 쓰면 DUET_PORT 로 바꾼다.
DEFAULT_PORT = int(os.environ.get("DUET_PORT", "8737"))
#: 피드백이 가는 곳. 보드가 GitHub 새 이슈 화면을 채워서 연다 — Duet 자신은 네트워크를 쓰지 않는다.
REPO_URL = "https://github.com/Jonghoon5922/duet"


class StatusIn(BaseModel):
    #: None이면 `상태:` 줄을 지운다 = 다시 세션에서 센다.
    status: str | None = None


class TaskIn(BaseModel):
    title: str
    description: str = ""
    #: 빈 문자열은 `_미분류`. 프로젝트 폴더가 없으면 첫 타스크와 함께 생긴다.
    project: str = ""


class ProjectIn(BaseModel):
    #: 빈 문자열은 `_미분류` 상자다.
    name: str = ""


class EditIn(BaseModel):
    #: 준 것만 바뀐다. None은 "안 건드림"이다.
    title: str | None = None
    description: str | None = None
    #: 주면 그 프로젝트로 옮긴다 (번호가 새로 난다).
    project: str | None = None


def _page_stamp() -> str:
    """화면 파일의 도장. 재설치로 파일이 바뀌면 열린 탭이 이걸 보고 스스로 새로고침한다."""
    try:
        return f"{__version__}-{int(PAGE.stat().st_mtime)}"
    except OSError:
        return __version__


def _ref(project: str, tid: str) -> str:
    return f"{project}/{tid}"


def _run(fn, *args):
    """core 호출 하나를 HTTP 오류로 옮긴다. 규칙 위반은 400, 디스크는 500."""
    try:
        return fn(*args)
    except core.DuetError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=str(e))


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
            # 타스크가 하나도 없는 프로젝트 폴더도 상자로 뜬다.
            "projects": core.projects(),
            "statuses": list(core.TASK_STATUSES),
            "home": str(core.home()),
            "archived": len({t.project for t in core.list_archived()}),
            "version": __version__,
            "repo": REPO_URL,
            "page": _page_stamp(),
        }

    @app.get("/api/events")
    async def events():
        """폴더가 바뀔 때마다 `change` 한 줄. 15초마다 `ping`으로 연결이 살아 있음을 알린다."""
        import asyncio

        async def stream():
            last = core.fingerprint()
            yield f"event: hello\ndata: {_page_stamp()}\n\n"
            quiet = 0.0
            while True:
                await asyncio.sleep(0.5)
                quiet += 0.5
                now = await asyncio.to_thread(core.fingerprint)
                if now != last:
                    last, quiet = now, 0.0
                    yield "event: change\ndata: 1\n\n"
                elif quiet >= 15:
                    quiet = 0.0
                    yield f"event: ping\ndata: {_page_stamp()}\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"cache-control": "no-cache", "x-accel-buffering": "no"})

    @app.post("/api/tasks", status_code=201)
    def new_task(body: TaskIn) -> dict[str, Any]:
        return _run(core.create_task, body.title, body.description, body.project).to_detail()

    @app.get("/api/archive")
    def archive() -> dict[str, Any]:
        rows = [t.to_detail() for t in core.list_archived()]
        return {"tasks": rows, "archived": len({t.project for t in core.list_archived()}), "home": str(core.home()),
                "version": __version__, "repo": REPO_URL, "page": _page_stamp()}

    @app.post("/api/projects/archive")
    def archive_project(body: ProjectIn) -> dict[str, Any]:
        """프로젝트 폴더째 보관함으로."""
        moved = _run(core.archive_project, body.name)
        return {"moved": [t.ref for t in moved], "note": f"Archived '{body.name or core.UNSORTED_DIRNAME}'"}

    @app.post("/api/projects/unarchive")
    def unarchive_project(body: ProjectIn) -> dict[str, Any]:
        moved = _run(core.unarchive_project, body.name)
        return {"moved": [t.ref for t in moved], "note": f"Restored '{body.name or core.UNSORTED_DIRNAME}'"}

    @app.delete("/api/projects/{name}")
    def delete_project(name: str) -> dict[str, Any]:
        """프로젝트 폴더를 타스크째 지운다. 살아 있는 세션이 있으면 거부."""
        gone = _run(core.delete_project, "" if name == core.UNSORTED_DIRNAME else name)
        return {"deleted": gone, "note": f"Deleted project '{gone}'"}

    @app.get("/api/tasks/{project}/{tid}")
    def task(project: str, tid: str) -> dict[str, Any]:
        try:
            return core.get_task(_ref(project, tid)).to_detail()
        except (core.DuetError, OSError) as e:
            raise HTTPException(status_code=404, detail=str(e))

    @app.patch("/api/tasks/{project}/{tid}")
    def edit_task(project: str, tid: str, body: EditIn) -> dict[str, Any]:
        """제목·설명 인라인 편집. 사람이 정한 상태는 건드리지 않는다."""
        return _run(core.update_task, _ref(project, tid), body.title, body.description, body.project).to_detail()

    @app.delete("/api/tasks/{project}/{tid}")
    def delete_task(project: str, tid: str) -> dict[str, Any]:
        """타스크 폴더를 지운다. 살아 있는 세션이 있으면 거부."""
        ref = _run(core.delete_task, _ref(project, tid))
        return {"deleted": ref, "note": f"Deleted {ref}"}

    @app.post("/api/tasks/{project}/{tid}/status")
    def set_status(project: str, tid: str, body: StatusIn) -> dict[str, Any]:
        """사람이 정한 상태를 `task.md`에 적거나(=자동 규칙보다 우선) 지운다."""
        return _run(core.set_status, _ref(project, tid), body.status).to_detail()

    @app.post("/api/tasks/{project}/{tid}/sessions/{session_id}/status")
    def set_session_status(project: str, tid: str, session_id: str, body: StatusIn) -> dict[str, Any]:
        """끝난 세션을 사람이 완료/중지로 바꾼다. 살아 있는 세션은 거부된다."""
        if body.status is None:
            raise HTTPException(status_code=400, detail="세션은 자동으로 되돌릴 수 없다.")
        return _run(core.set_session_status, _ref(project, tid), session_id, body.status).to_detail()

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
