"""이 프로세스 = 이 세션.

MCP(stdio)는 클라이언트 창 하나마다 서버 프로세스를 하나 띄운다. 그래서 별도의 훅
설정 없이도 **프로세스 수명이 곧 세션 수명**이다 (SPEC 4절).

- 뜰 때: 세션 id를 만들고 `unassigned/`에 자기 파일 생성 (`미참여`)
- 도는 동안: 30초마다 자기 파일에 하트비트
- 끝날 때: 종료 훅에서 `중지` 기록. 훅조차 못 돌면 하트비트 90초 초과를 읽는 쪽이 잡는다

세션 id를 어디에도 저장하지 않는다. 프로세스가 죽으면 그 id는 다시 쓰이지 않아야 한다.
파일을 쓰는 것은 하트비트 스레드와 도구 호출 두 갈래라 락으로 감싼다.
"""

from __future__ import annotations

import atexit
import os
import signal
import threading
from pathlib import Path

from . import sessionfile, store
from .sessionfile import SessionFile, new_session_id
from .states import HEARTBEAT_SEC, SESSION_STOPPED

UNKNOWN_CLIENT = "알 수 없음"

#: MCP `initialize`에서 클라이언트가 밝히는 이름 → 사람이 읽는 이름.
_CLIENT_LABELS = {
    "claude-ai": "Claude Desktop",
    "claude-code": "Claude Code",
    "cursor": "Cursor",
    "cline": "Cline",
    "continue": "Continue",
    "windsurf": "Windsurf",
}

#: Claude Desktop이 실제로 보내는 이름은 `local-agent-mode-<서버이름>` 이다 (서재에서 실측).
_AGENT_PREFIX = "local-agent-mode-"


def label_client(name: str | None, version: str | None = None) -> str:
    """클라이언트가 밝힌 이름을 대시보드에 그대로 띄울 이름으로 바꾼다."""
    if not name:
        return UNKNOWN_CLIENT
    lowered = name.lower()
    if lowered.startswith(_AGENT_PREFIX):
        return "Claude Desktop"
    label = _CLIENT_LABELS.get(lowered, name)
    return f"{label} {version}" if version else label


def guess_client() -> str:
    """등록 시점의 추정치. 아직 `initialize`가 오기 전이라 환경변수만 보고 찍는다.

    첫 도구 호출 때 클라이언트가 밝힌 진짜 이름으로 덮어쓴다.
    """
    env = os.environ
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "Claude Code"
    if env.get("CURSOR_TRACE_ID") or env.get("CURSOR_AGENT"):
        return "Cursor"
    if env.get("TERM_PROGRAM") == "vscode":
        return "VS Code"
    return UNKNOWN_CLIENT


class SessionHandle:
    """이 프로세스의 세션 파일. 하트비트와 종료 기록을 책임진다."""

    def __init__(
        self,
        home: Path,
        *,
        session_id: str | None = None,
        client: str | None = None,
        cwd: str | None = None,
    ) -> None:
        self.home = home
        self.id = session_id or new_session_id()
        self.client = client or guess_client()
        self.cwd = cwd if cwd is not None else str(Path.cwd())
        self.lock = threading.Lock()
        self.file: SessionFile | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._closed = False
        self._client_confirmed = False

    # --- 수명 ---

    def register(self) -> SessionFile:
        with self.lock:
            self.file = store.register_session(
                self.home, self.id, client=self.client, pid=os.getpid(), cwd=self.cwd
            )
        return self.file

    def start(self) -> None:
        """등록하고 하트비트를 돌린다. 종료 훅도 여기서 건다."""
        self.register()
        self._thread = threading.Thread(target=self._beat, name="duet-heartbeat", daemon=True)
        self._thread.start()
        atexit.register(self.stop)
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                signal.signal(sig, self._on_signal)
            except (ValueError, OSError):
                # 메인 스레드가 아니거나 그 플랫폼에 없는 시그널이면 넘어간다.
                # 하트비트 그물이 있으므로 여기서 죽을 이유가 없다.
                pass

    def _beat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SEC):
            try:
                with self.lock:
                    if self.file is not None:
                        store.heartbeat(self.file)
            except OSError:
                # 잠깐 못 썼을 뿐일 수 있다. 다음 박자에 다시 친다.
                continue

    def _on_signal(self, signum, frame) -> None:  # noqa: ANN001
        self.stop(reason=f"신호 {signum} 수신")
        raise SystemExit(0)

    def stop(self, reason: str = "세션 창이 닫힘 (보고 없이 종료)") -> None:
        """프로세스가 끝날 때 `중지`로 적는다.

        이미 `complete_session`으로 끝난 세션이면 store가 알아서 덮지 않는다.
        """
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        try:
            with self.lock:
                if self.file is not None:
                    store.close_session(self.home, self.file, SESSION_STOPPED, reason)
        except Exception:
            # 종료 경로다. 여기서 예외를 올리면 사용자에게 보이는 건 스택 뿐이다.
            pass

    # --- 클라이언트 이름 ---

    def confirm_client(self, name: str | None, version: str | None = None) -> None:
        """`initialize`에서 온 진짜 클라이언트 이름으로 한 번만 덮어쓴다."""
        if self._client_confirmed or not name:
            return
        label = label_client(name, version)
        if label == UNKNOWN_CLIENT:
            return
        self._client_confirmed = True
        self.client = label
        try:
            with self.lock:
                if self.file is not None:
                    self.file.client = label
                    sessionfile.write(self.file)
        except OSError:
            pass
