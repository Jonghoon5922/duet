# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 빌드 설정 — 파이썬과 의존성까지 통째로 묶는다.

uv도 파이썬도 없는 컴퓨터에서 받아서 바로 쓰는 것이 목적이다. 그리고 하나 더:
uv 트램폴린·가상 AppData 같은 것에 기대지 않는 실행 파일이 필요했다. 실제로 Claude
데스크탑 안 터미널에서는 그것들이 깨졌다.

실행 파일은 하나다 (`duet.exe`, 콘솔 모드). 서재와 달리 창 모드 앱이 아직 없다.

빌드:
    .venv\\Scripts\\pyinstaller.exe installer\\duet.spec --noconfirm --distpath dist\\app

빠뜨리면 안 되는 것: static/board.html (없으면 보드가 404), uvicorn 이 런타임에
동적으로 부르는 하위 모듈, mcp 가 윈도우에서 쓰는 pywin32.
"""

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

SPEC_DIR = Path(SPECPATH)
PROJECT = SPEC_DIR.parent

datas = [(str(PROJECT / "src" / "duet" / "static" / "board.html"), "duet/static")]

hiddenimports = [
    "duet.cli", "duet.core", "duet.server", "duet.web", "duet.mcp_clients",
    # 웹 서버가 런타임에 동적으로 부르는 것들
    "uvicorn.logging", "uvicorn.loops.auto", "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto", "uvicorn.lifespan.on",
    # mcp 가 윈도우에서 stdio 핸들을 다시 묶을 때 쓴다
    "pywintypes", "win32api", "win32con", "win32file",
] + collect_submodules("mcp")

def analyze(entry):
    """두 진입점을 같은 조건으로 분석한다."""
    return Analysis(
        [str(SPEC_DIR / entry)],
        pathex=[str(PROJECT / "src")],
        binaries=[],
        datas=datas,
        hiddenimports=hiddenimports,
        hookspath=[],
        excludes=["tkinter", "pytest", "IPython", "matplotlib", "PIL"],
        noarchive=False,
    )


# 실행 파일이 둘이다. 파이썬이 python.exe 와 pythonw.exe 로 나뉜 것과 같은 이유다.
#   duet.exe        콘솔 모드. MCP는 stdin/stdout으로 대화하므로 콘솔이어야 한다
#   duet-board.exe  창 모드. 시작 메뉴에서 보드를 열 때 검은 창이 안 뜨게
mcp_analysis = analyze("launcher.py")
board_analysis = analyze("board_launcher.py")

mcp_exe = EXE(
    PYZ(mcp_analysis.pure), mcp_analysis.scripts, [],
    exclude_binaries=True, name="duet",
    debug=False, strip=False, upx=False,
    console=True,
)
board_exe = EXE(
    PYZ(board_analysis.pure), board_analysis.scripts, [],
    exclude_binaries=True, name="duet-board",
    debug=False, strip=False, upx=False,
    console=False,
)

# 무거운 자원(_internal)은 한 벌. 양쪽 분석 결과를 합쳐 빠지는 것이 없게 한다.
COLLECT(
    mcp_exe, board_exe,
    mcp_analysis.binaries + board_analysis.binaries,
    mcp_analysis.datas + board_analysis.datas,
    strip=False, upx=False, name="Duet",
)
