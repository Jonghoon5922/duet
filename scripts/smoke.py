"""진짜 stdio MCP 서버 프로세스로 한 바퀴 돌려 본다.

단위 테스트가 못 보는 것을 본다 — 프로세스가 뜨고, 죽고, 창이 닫힐 때 무슨 일이
벌어지는가. 단계를 끝낼 때마다 이걸 돌린다.

    uv run python scripts/smoke.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
HOME = Path(tempfile.mkdtemp(prefix="duet-smoke-"))
sys.path.insert(0, str(PROJECT / "src"))
os.environ["DUET_HOME"] = str(HOME)

from duet import core  # noqa: E402


class Client:
    """MCP stdio 클라이언트 최소 구현. 창 하나를 흉내 낸다."""

    def __init__(self, client_name: str = "claude-code"):
        self.proc = subprocess.Popen(
            ["uv", "run", "duet", "serve"],
            cwd=PROJECT,
            env=dict(os.environ, DUET_HOME=str(HOME), PYTHONIOENCODING="utf-8"),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0
        self.instructions = self._initialize(client_name)

    def _rpc(self, method: str, params: dict, notify: bool = False) -> dict:
        if notify:
            self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method, "params": params}) + "\n")
            self.proc.stdin.flush()
            return {}
        self._id += 1
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}) + "\n"
        )
        self.proc.stdin.flush()
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"서버가 응답 없이 끝났다\n{self.proc.stderr.read()}")
            msg = json.loads(line)
            if msg.get("id") == self._id:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg["result"]

    def _initialize(self, client_name: str) -> str:
        result = self._rpc("initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": client_name, "version": "9.9.9"},
        })
        self._rpc("notifications/initialized", {}, notify=True)
        return result.get("instructions", "")

    def tool(self, name: str, args: dict | None = None) -> dict:
        result = self._rpc("tools/call", {"name": name, "arguments": args or {}})
        content = result.get("structuredContent")
        return content if content is not None else json.loads(result["content"][0]["text"])

    def close(self, kill: bool = False) -> None:
        self.proc.kill() if kill else self.proc.stdin.close()
        self.proc.wait(timeout=30)


FAILED = False


def check(label: str, ok: bool, detail: str = "") -> None:
    global FAILED
    print(f"[{'OK  ' if ok else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    FAILED = FAILED or not ok


print(f"DUET_HOME = {HOME}\n")

# 세션 A: 타스크를 만들고 참여한다
a = Client()
check("A 연결, instructions에 안내가 있다", "join_task" in a.instructions)
task_id = a.tool("create_task", {"title": "pc101pm 전환", "description": "NEFSS→BXM"})["task"]["id"]

again = a.tool("create_task", {"title": "pc101pm 전환 2차"})
check("비슷한 제목이면 만들지 않고 후보를 준다", "만들지_않았다" in again,
      str([t["title"] for t in again.get("후보", [])]))
forced = a.tool("create_task", {"title": "pc101pm 전환 2차", "confirm": True})
check("confirm을 주면 그래도 만든다", forced.get("task", {}).get("id") == "T002")
md_before = (core.find_task(task_id) / "task.md").read_text(encoding="utf-8")

joined = a.tool("join_task", {"task_id": task_id, "session_title": "DBIO 계층 전환"})
check("A join → 타스크 진행중", joined["task"]["status"] == "진행중", str(joined["task"]["counts"]))
a.tool("report_progress", {"message": "DBIO 12개 중 5개 전환", "percent": 40})

# 세션 B: A가 살아 있는 동안은 같은 타스크에 못 붙는다
b = Client("claude-ai")
check("B의 instructions에 열린 타스크가 보인다", f"[{task_id}] pc101pm 전환" in b.instructions,
      [ln for ln in b.instructions.splitlines() if ln.startswith(f"- [{task_id}]")][0])
blocked = b.tool("join_task", {"task_id": task_id, "session_title": "Bean 계층 전환"})
check("진행중인 타스크에는 같이 못 붙는다", "이미 살아 있는 세션" in blocked.get("error", ""),
      blocked.get("error", "")[:60])

# A가 끝내면 타스크는 일단 완료. B가 이어받으면 다시 진행중.
check("A 완료 → 세션 하나뿐이니 타스크 완료",
      a.tool("complete_session", {"summary": "DBIO 끝"})["task"]["status"] == "완료")
joined_b = b.tool("join_task", {"task_id": task_id, "session_title": "Bean 계층 전환"})
check("앞 세션이 끝난 뒤엔 이어받는다 → 다시 진행중", joined_b["task"]["status"] == "진행중")
b.tool("report_progress", {"message": "Bean 8개 전환", "percent": 80})

check("세션 2개가 각자 클라이언트 이름으로 남았다",
      [s.client for s in core.get_task(task_id).sessions] == ["Claude Code", "Claude Desktop"])
check("세션이 붙고 보고해도 task.md는 그대로다 (충돌 없음)",
      (core.find_task(task_id) / "task.md").read_text(encoding="utf-8") == md_before)

done = b.tool("complete_session", {"summary": "Bean 끝"})
check("B 완료 → 타스크 자동 완료", done["task"]["status"] == "완료", done.get("note", ""))

a.close()
b.close()
check("창을 닫아도 완료 세션은 완료로 남는다",
      [s.status for s in core.get_task(task_id).sessions] == ["완료", "완료"])

# 세션 C-0: get_task로 앞 세션들이 뭘 했는지 읽는다 (인수인계)
reader = Client()
detail = reader.tool("get_task", {"task_id": task_id})
check("get_task가 다른 세션의 로그까지 준다",
      [p["msg"] for s in detail["sessions"] for p in s["progress"]]
      == ["DBIO 12개 중 5개 전환", "Bean 8개 전환"], str([s["title"] for s in detail["sessions"]]))
check("get_task에 설명과 폴더가 들어 있다", detail["description"] == "NEFSS→BXM" and detail["folder"])

# 그 세션은 붙었다가 도중에 멈춘다 (pause_session)
reader.tool("join_task", {"task_id": task_id, "session_title": "이어받기 검토"})
reader.tool("report_progress", {"message": "A와 B 로그 읽음. Service 계층이 남았다"})
paused = reader.tool("pause_session", {"reason": "여기까지 — Service는 다음 세션에서"})
check("pause_session → 세션 중지", paused["status"] == "중지", paused.get("note", ""))
check("중지가 있으면 타스크는 열린 채", paused["task"]["status"] == "진행중")
reader.close()
check("멈춘 뒤 창을 닫아도 이유가 남는다",
      [s.summary for s in core.get_task(task_id).sessions if s.title == "이어받기 검토"]
      == ["여기까지 — Service는 다음 세션에서"])

# update_task: 사용자가 시켜서 제목·설명을 고친다
fixer = Client()
renamed = fixer.tool("update_task", {"task_id": task_id, "title": "pc101pm 전환 (1차)",
                                    "description": "NEFSS→BXM. Service 계층은 다음 주."})
check("update_task가 제목을 고친다", renamed["task"]["title"] == "pc101pm 전환 (1차)",
      str(renamed["changed"]))
check("폴더 이름은 그대로다", core.find_task(task_id).name.endswith("pc101pm-전환"),
      core.find_task(task_id).name)
check("설명도 함께 바뀐다", core.get_task(task_id).description.endswith("다음 주."))
fixer.close()

# 세션 C: 보고 없이 창을 닫는다 → 종료 훅이 중지로 적는다
c = Client()
c.tool("join_task", {"task_id": task_id, "session_title": "설계서 docx 빌드"})
check("완료된 타스크에 새 세션이 붙으면 다시 진행중", core.get_task(task_id).status == "진행중")
c.close()
time.sleep(0.5)
stopped = [s for s in core.get_task(task_id).sessions if s.title == "설계서 docx 빌드"][0]
check("보고 없이 닫힌 세션은 중지", stopped.status == "중지", stopped.summary)
check("중지가 섞이면 타스크는 열린 채로 남는다", core.get_task(task_id).status == "진행중",
      core.get_task(task_id).warning)

# 사람이 정한 상태가 이긴다
core.set_status(task_id, "완료")
d = Client()
d.tool("join_task", {"task_id": task_id, "session_title": "잠긴 타스크에 붙는 세션"})
check("사람이 정한 상태는 세션이 붙어도 안 바뀐다", core.get_task(task_id).status == "완료")
d.close()
check("그 줄을 지우면 다시 센다", core.set_status(task_id, None).status == "진행중")

# 사람이 손대는 자리: 중지 세션을 완료로 → 타스크가 스스로 닫힌다
stopped = [s for s in core.get_task(task_id).sessions if s.status == "중지"]
core.set_session_status(task_id, stopped[0].id, "완료")
check("중지 세션을 완료로 바꾸면 세는 값이 달라진다",
      core.get_task(task_id).counts["중지"] == len(stopped) - 1)

closed = core.close_task(task_id)
check("타스크 닫기 → 남은 중지가 정리되고 완료", closed.status == "완료" and closed.override == "완료")
check("닫은 뒤에는 경고가 없다", closed.warning == "", closed.warning)

core.archive_task(task_id)
check("보관하면 보드에서 빠진다",
      task_id not in [t.id for t in core.list_tasks()]
      and [t.id for t in core.list_archived()] == [task_id])
check("되돌리면 돌아온다",
      core.unarchive_task(task_id).id == task_id and core.list_archived() == [])

print()
print("실패 있음" if FAILED else "전부 통과")
sys.exit(1 if FAILED else 0)
