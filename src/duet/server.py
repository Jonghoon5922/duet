"""MCP 서버 (stdio). 이 프로세스가 세션 하나다.

뜰 때 `_미참여/`에 자기 파일을 만들고, 30초마다 하트비트를 찍고, 끝날 때 `중지`로
적는다. 훅조차 못 돌면 하트비트 90초 초과를 읽는 쪽이 잡는다 (core.reap).

stdout은 MCP 프로토콜 채널이다. 이 모듈은 stdout에 아무것도 출력하지 않는다.
"""

from __future__ import annotations

import atexit
import os
import threading
from pathlib import Path
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
    open_tasks = [
        t for t in core.list_tasks() if t.status in (core.WAITING, core.RUNNING, core.ATTENTION)
    ]
    here = core.project_name(str(Path.cwd()))
    lines = [
        "이 세션이 어떤 일감(타스크)에 속하는지 기록하는 도구다. "
        "진행 로그는 네가 직접 쓴다 — 이 도구는 아무것도 요약해주지 않는다.",
        "",
        f"이 창의 프로젝트: {here}" if here else "이 창은 프로젝트 폴더가 아닌 곳에서 떴다 (타스크는 _미분류로 간다).",
        "",
    ]
    if open_tasks:
        mine = [t for t in open_tasks if t.project == here] if here else []
        others = [t for t in open_tasks if t not in mine]

        def line(t):
            return f"- [{t.id}] {t.title} ({t.status}, 세션 {t.counts[core.RUNNING]}개 진행중)"

        if mine:
            lines.append(f"이 프로젝트({here})의 열린 타스크:")
            lines += [line(t) for t in mine[:12]]
            if others:
                lines.append("")
        if others:
            lines.append("다른 프로젝트의 열린 타스크 (부를 때는 프로젝트/T00n):" if mine else "열린 타스크:")
            lines += [f"- [{t.ref}] {t.title} ({t.status})" for t in others[:12]]
    else:
        lines.append("지금 열린 타스크가 없다. 새 일이면 create_task로 만든다.")

    lines += [
        "",
        "**모든 일은 타스크를 타고 간다.** 사용자가 자연어로 일을 시키면 묻지 말고: "
        "(1) 열린 타스크 중 그 일이면 join_task, 아니면 create_task 후 join_task — "
        "(2) 일하면서 report_progress로 한 줄씩 — (3) 끝나면 complete_session. "
        "이 순서를 사용자가 매번 말하지 않아도 스스로 한다.",
        "타스크는 끝을 판정할 수 있는 한 덩어리다. 한두 마디로 끝날 질문·잔일은 타스크 없이 그냥 한다.",
        "**계획을 세웠으면 그 자리에서 타스크다.** 사용자에게 계획을 보여주는 순간 create_task로 만들고 "
        "계획의 요지를 description에 적어라 — 사용자가 '타스크에 넣어'라고 따로 말하지 않는다. "
        "일을 실제로 시작할 때 join_task, 그 뒤로는 report_progress.",
        "세션 하나는 타스크 하나다. 끝내고 다른 일을 시키면 그냥 join_task를 불러라 — "
        "새 세션으로 이어진다. 한 창이 타스크를 순서대로 여러 개 해도 된다.",
        "프로젝트는 폴더다. 이 창의 타스크는 이 창의 프로젝트에 만들어지고 번호는 프로젝트 안에서 1부터다. "
        "같은 프로젝트 안에서는 T001로, 다른 프로젝트 것은 duet/T001처럼 부른다.",
    ]
    return "\n".join(lines)


class Current:
    """이 프로세스가 지금 붙어 있는 세션.

    한 창이 타스크를 순서대로 여러 개 할 수 있다. 앞 타스크를 끝내고 다음에 붙으면
    **새 세션 파일**로 갈아탄다 — 앞 파일은 닫힌 채로 남고, 하트비트와 종료 훅은 지금
    것에만 간다. 같은 창이라는 사실은 `client_session`(대화 id)이 묶어 준다.
    """

    def __init__(self, session: core.Session) -> None:
        self.session = session

    @property
    def project(self) -> str:
        """이 창의 프로젝트. 번호만으로 부른 타스크를 어디서 찾을지의 기준이다."""
        return core.project_name(self.session.cwd)

    def renew(self) -> core.Session:
        prev = self.session
        self.session = core.register_session(
            client=prev.client, cwd=prev.cwd, client_session=prev.client_session
        )
        return self.session


def create_server(cur: Current, lock: threading.Lock) -> MCPServer:
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
                    cur.session.client = label
                core.heartbeat(cur.session)
        except Exception:
            pass

    @server.tool(
        name="list_tasks",
        description="타스크 목록. 어느 타스크에 참여할지 고를 때 먼저 호출한다. ref가 부르는 이름이다 (duet/T001).",
    )
    def list_tasks_tool(ctx: Context, status: str | None = None) -> dict[str, Any]:
        touch(ctx)
        joined = core.task_of(cur.session)
        return {
            "project": cur.project,
            "joined_task": joined.ref if joined else None,
            "tasks": [t.to_dict() for t in core.list_tasks(status)],
        }

    @server.tool(
        name="create_task",
        description=(
            "새 타스크를 만든다(`대기`). 참여까지 하지는 않으므로, 이 세션이 그 일을 "
            "할 것이면 이어서 join_task를 호출하라.\n"
            "**제목 짓는 법**: 끝을 판정할 수 있는 한 덩어리로. 결과물로 쓰고 대화로 쓰지 마라 "
            "(X: '리팩터링 논의', '이것저것 수정' / O: '결제 모듈 리팩터링', '세션 타임라인 붙이기'). "
            "프로젝트 이름은 넣지 마라 — 이미 프로젝트로 묶여 있다 "
            "(X: 'Duet 대시보드 만들기' / O: '대시보드 만들기'). "
            "세션 하나로 끝날 잔일이면 타스크를 만들지 말고 그냥 해라.\n"
            "**description**: 사람이 메모하듯 한두 문장. 무엇을 왜 하는지, 끝나면 뭐가 되는지만 — "
            "계획을 세웠으면 그 요지가 여기 들어간다. "
            "번호 매기기·괄호 설명·'끝:' 같은 틀을 쓰지 마라.\n"
            "**project**: 보통 비워 둔다 — 이 창이 뜬 폴더의 프로젝트에 만들어진다. "
            "다른 프로젝트 일을 대신 만들 때만 적는다. 번호는 그 프로젝트 안에서 1부터 난다.\n"
            "비슷한 타스크가 이미 있으면 만들지 않고 후보를 돌려준다. 같은 일이면 그것에 "
            "join하고, 정말 다른 일이면 confirm=true로 다시 불러라."
        ),
    )
    def create_task_tool(
        ctx: Context,
        title: str,
        description: str = "",
        project: str = "",
        confirm: bool = False,
    ) -> dict[str, Any]:
        touch(ctx)
        if not confirm:
            # 같은 일이 두 벌로 쌓이는 것이 이 도구에서 제일 흔한 실수다.
            similar = core.similar_tasks(title)
            if similar:
                return {
                    "만들지_않았다": "비슷한 타스크가 이미 있다.",
                    "후보": [t.to_dict() for t in similar],
                    "next": "같은 일이면 join_task로 붙어라. 정말 다른 일이면 confirm=true로 다시 불러라.",
                }
        try:
            task = core.create_task(title, description, project.strip() or cur.project)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        return {"task": task.to_dict(), "next": "이 세션이 이 일을 한다면 join_task를 호출하라."}

    @server.tool(
        name="join_task",
        description=(
            "이 세션을 타스크에 묶는다. 타스크가 `진행중`이 되고 이 세션의 보고가 "
            "그 타스크에 쌓인다. 세션 하나는 타스크 하나에만 붙는다. "
            "task_id는 이 프로젝트 것이면 T001, 다른 프로젝트 것이면 duet/T001. "
            "session_title은 이 창이 이 타스크에서 맡은 몫을 짧게 ('DBIO 전환', '설계서 빌드')."
        ),
    )
    def join_task_tool(ctx: Context, task_id: str, session_title: str = "") -> dict[str, Any]:
        touch(ctx)
        renewed = False
        try:
            with lock:
                if cur.session.status in core.CLOSED:
                    # 앞 타스크를 끝낸 창이 다음 타스크를 잡는다. 새 세션으로 이어진다.
                    cur.renew()
                    renewed = True
                task = core.join(cur.session, task_id, session_title)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        note = "작업 중간에 report_progress, 끝나면 complete_session을 호출하라."
        if renewed:
            note = f"앞 세션은 닫힌 채 두고 새 세션 {cur.session.id}로 이어진다. " + note
        return {"task": task.to_dict(), "session_id": cur.session.id, "note": note}

    @server.tool(
        name="get_task",
        description=(
            "타스크 하나의 속을 본다 — 설명, 붙어 있는 세션들, 각 세션이 남긴 진행 로그. "
            "**다른 세션이 어디까지 했는지 읽고 이어받을 때 쓴다.** 같은 타스크에 참여했다면 "
            "일을 시작하기 전에 한 번 읽어라."
        ),
    )
    def get_task_tool(ctx: Context, task_id: str) -> dict[str, Any]:
        touch(ctx)
        try:
            task = core.get_task(task_id, hint=cur.project)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}

        detail = task.to_detail()
        mine = core.task_of(cur.session)
        if mine and mine.ref == task.ref:
            # 자기 것을 빼주면 "남이 뭘 했나"만 남는다. 이어받을 때 읽는 자리다.
            detail["내_세션"] = cur.session.id
        return detail

    @server.tool(
        name="update_task",
        description=(
            "타스크의 제목·설명·상태를 고친다. **사용자가 그렇게 하라고 했을 때만 쓴다** — "
            "일이 어디까지 됐는지는 report_progress로 남기는 것이지 설명을 고쳐 적는 게 아니다. "
            "status는 완료·보류·취소 중 하나. 대기·진행중·확인 필요는 세션이 없느냐 살아 있느냐 끊겼느냐는 사실이라 고를 수 없다. "
            "사람이 정한 상태로 적혀 자동 규칙(세션을 세는 것)을 덮는다. "
            "`자동`을 주면 그 줄을 지워 다시 세게 한다. "
            "project를 주면 그 프로젝트로 옮긴다 — 번호가 새로 난다."
        ),
    )
    def update_task_tool(
        ctx: Context,
        task_id: str,
        title: str | None = None,
        description: str | None = None,
        status: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        touch(ctx)
        try:
            task = core.update_task(task_id, title, description, project, hint=cur.project)
            if status is not None:
                task = core.set_status(task.ref, None if status == "자동" else status)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}

        changed = [name for name, value in
                   (("제목", title), ("설명", description), ("상태", status),
                    ("프로젝트", project)) if value is not None]
        return {
            "task": task.to_dict(),
            "changed": changed or ["없음"],
            "note": f"{task.dir.name} 폴더 이름은 그대로 둔다 — 판별자는 {task.ref}다.",
        }

    @server.tool(
        name="pause_session",
        description=(
            "이 세션을 여기서 멈춘다(`중지`). 일이 끝나지 않았는데 사용자가 "
            "\"여기까지\"라고 할 때 쓴다. 끝냈으면 complete_session이다. "
            "중지된 세션이 있으면 타스크는 닫히지 않고 사람이 판단하도록 열린 채 남는다."
        ),
    )
    def pause_session_tool(ctx: Context, reason: str) -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                task = core.finish(cur.session, core.STOPPED, reason)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        if task is None:
            return {"status": core.STOPPED, "note": "타스크에 참여하지 않아 닫히기만 했다."}
        return {
            "status": core.STOPPED,
            "task": task.to_dict(),
            "note": (
                f"[{task.ref}] {task.title} 은(는) 열어 둔다. 다음 세션이 get_task로 "
                "여기까지의 로그를 읽고 이어받을 수 있다."
            ),
        }

    @server.tool(
        name="report_progress",
        description=(
            "이 세션의 진행 로그를 한 줄 남긴다. 다음 세션이 읽고 이어받을 수 있게 "
            "무엇을 했고 어디까지 됐는지 사실만 쓴다. 한 문장, 사람이 수첩에 적듯 "
            "(O: '로그인 화면 DBIO 3개 전환, 컴파일 통과' / X: '(1) … (2) … 다음 단계로 …'). "
            "일을 실제로 시작할 때 join하고, 시작한 뒤에 남겨라 — 계획은 로그가 아니다."
        ),
    )
    def report_progress_tool(
        ctx: Context, message: str, percent: int | None = None
    ) -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                entry = core.report(cur.session, message, percent)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        task = core.task_of(cur.session)
        result = {"t": entry["t"], "task_id": task.ref if task else None}
        if task is None:
            result["note"] = (
                "아직 타스크에 참여하지 않았다. join_task를 부르면 이 보고도 함께 따라간다."
            )
        return result

    @server.tool(
        name="complete_session",
        description=(
            "이 세션의 일이 끝났다고 보고한다. 타스크의 세션이 전부 완료면 타스크도 "
            "`완료`가 된다. summary는 사람이 읽는 마무리 한 줄 — 무엇이 됐는지만, 한 문장."
        ),
    )
    def complete_session_tool(ctx: Context, summary: str) -> dict[str, Any]:
        touch(ctx)
        try:
            with lock:
                task = core.finish(cur.session, core.DONE, summary)
        except (core.DuetError, OSError) as e:
            return {"error": str(e)}
        if task is None:
            return {"status": core.DONE, "note": "타스크에 참여하지 않아 닫히기만 했다."}

        if task.status == core.DONE:
            note = f"[{task.ref}] {task.title} 타스크가 자동 완료됐다."
        elif task.warning:
            note = task.warning
        else:
            note = f"아직 진행중인 세션이 {task.counts[core.RUNNING]}개 있다."
        return {"status": core.DONE, "task": task.to_dict(), "note": note}

    return server


def host_board_in_background() -> None:
    """이 MCP 서버가 보드도 겸한다. Claude 창이 열려 있는 동안 보드는 늘 살아 있다.

    창마다 이 프로세스가 뜨므로, 포트가 비어 있는 첫 프로세스가 보드를 맡고 나머지는
    5초마다 다시 잡아 본다 — 맡은 창이 닫히면 다음 창이 이어받는다. 여기서 무슨 일이
    나도 MCP 쪽은 건드리지 않는다. 그쪽이 본업이다.
    """
    import threading
    import time

    def run() -> None:
        from . import web

        while True:
            try:
                if web.is_port_free():
                    web.serve_ui(open_browser=False)  # 내려갈 때까지 돌아오지 않는다
            except Exception:
                pass  # 포트 경합·바인드 실패·앱 오류. 조용히 다음 기회를 기다린다
            time.sleep(5)

    threading.Thread(target=run, name="duet-board", daemon=True).start()


def serve() -> None:
    """이 함수가 도는 동안이 곧 세션 하나의 수명이다."""
    cur = Current(core.register_session(
        client=_guess_client(),
        cwd=str(Path.cwd()),
        # Claude Code가 이 대화에 붙인 id. 대화 기록 파일 이름이 이것이다.
        client_session=os.environ.get("CLAUDE_CODE_SESSION_ID", ""),
    ))
    lock = threading.Lock()
    stop = threading.Event()
    closed = threading.Event()

    def beat() -> None:
        while not stop.wait(core.HEARTBEAT_SEC):
            try:
                with lock:
                    core.heartbeat(cur.session)
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
                core.finish(cur.session, core.STOPPED, "세션 창이 닫힘 (보고 없이 종료)")
        except Exception:
            pass  # 종료 경로다. 여기서 예외를 올리면 보이는 건 스택뿐이다

    threading.Thread(target=beat, name="duet-heartbeat", daemon=True).start()
    if os.environ.get("DUET_NO_BOARD") != "1":
        host_board_in_background()  # 시험에서는 끈다. 스모크가 프로세스를 여럿 띄운다
    atexit.register(close)
    try:
        create_server(cur, lock).run("stdio")
    finally:
        close()


def _guess_client() -> str:
    """등록 시점 추정치. 첫 도구 호출 때 진짜 이름으로 덮어쓴다."""
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE_ENTRYPOINT"):
        return "Claude Code"
    if os.environ.get("CURSOR_TRACE_ID"):
        return "Cursor"
    return "알 수 없음"
