"""`duet-board.exe` 진입점 (PyInstaller 로 묶이는 대상, 창 모드).

시작 메뉴 [Duet 보드]가 이걸 부른다. 검은 콘솔 창 없이 브라우저에 보드만 뜬다.

- 이미 보드가 떠 있으면 (Claude 창의 MCP 서버가 겸하고 있으면) 브라우저만 열고 끝낸다.
- 아니면 여기서 띄우고 브라우저를 연다. 끄는 손이 없으므로 **10분 동안 아무도 안 보면
  스스로 내려간다.**

창 모드 실행 파일에는 콘솔이 없다. 오류가 나면 화면에 아무것도 안 나오므로 메시지
상자로 알린다 — 안 그러면 "아이콘 눌렀는데 아무 일도 안 남"이 된다.
"""

from __future__ import annotations

import sys
import traceback
import webbrowser

IDLE_MINUTES = 10


def show_error(message: str) -> None:
    try:
        import ctypes

        ctypes.windll.user32.MessageBoxW(None, message, "Duet 보드 — 열 수 없습니다", 0x10)
    except Exception:
        print(message, file=sys.stderr)


def main() -> int:
    try:
        from duet import web

        if not web.is_port_free():
            webbrowser.open(web.board_url())  # 누군가 이미 띄웠다. 열어 주기만
            return 0
        web.serve_ui(open_browser=True, idle_minutes=IDLE_MINUTES)
    except Exception:
        show_error("보드를 띄우지 못했습니다.\n\n" + traceback.format_exc(limit=3))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
