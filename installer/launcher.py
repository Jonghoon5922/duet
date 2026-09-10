"""`duet.exe` 진입점 (PyInstaller 로 묶이는 대상, 콘솔 모드).

실행 파일이 하나다. 서재는 창 모드 앱과 콘솔 모드 MCP로 둘이었지만, Duet의 창(`app`)은
아직 없고 보드는 브라우저로 연다. 그래서 콘솔 하나로 `serve`도 `ui`도 한다.
콘솔이어야 하는 이유: MCP는 stdin/stdout으로 대화한다. 창 모드 실행 파일에는 그게 없다.

    duet.exe serve          MCP 서버 (Claude Code·Desktop이 이렇게 부른다)
    duet.exe ui             보드 (시작 메뉴 바로가기가 이걸 부른다)
    duet.exe register       Claude Code(모든 프로젝트)에 등록 (인스톨러가 부른다)
    duet.exe register claude-desktop
    duet.exe unregister     등록 해제 (제거 프로그램이 부른다)
    duet.exe list           어느 앱에 붙어 있는지

인자 없이 실행되면 안내를 보여준다. 아이콘을 잘못 눌렀을 때 검은 창만 깜빡이고
사라지는 것보다 낫다.
"""

from __future__ import annotations

import sys

# 윈도우 콘솔·파이프의 기본 인코딩은 cp949다. CLI를 import 하기 전에 안내를 찍으므로
# 여기서 먼저 UTF-8로 돌린다. 서재에서 `--help` 가 이것 때문에 죽었다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass

#: 짧게 쓰라고 둔 별칭. 사람도 LLM도 `register claude-desktop` 이라고 친다.
ALIASES = {"register": "mcp-register", "unregister": "mcp-unregister", "list": "mcp-list"}

USAGE = """Duet — 나와 Claude가 함께 하는 일의 보드

  duet.exe ui                     보드를 연다 (브라우저)
  duet.exe serve                  MCP 서버 (Claude가 이렇게 부른다. 직접 칠 일은 없다)

연결:
  duet.exe list                   어느 앱에 붙어 있는지
  duet.exe register               Claude Code (모든 프로젝트)에 연결
  duet.exe register claude-desktop
  duet.exe unregister             연결 해제

그 밖의 명령: duet.exe --help
"""


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] in ("/?", "-h"):
        print(USAGE)
        return 0

    if args[0] in ALIASES:
        rest = list(args[1:])
        if rest and not rest[0].startswith("-"):
            rest = ["--client", *rest]  # `register cursor` 처럼 앱 이름을 바로 받는다
        args = [ALIASES[args[0]], *rest]

    # CLI를 그대로 쓴다. 인코딩·오류 처리를 두 벌로 두지 않기 위해서다.
    sys.argv = ["duet", *args]
    from duet.cli import app

    app()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
