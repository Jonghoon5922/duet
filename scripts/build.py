"""인스톨러를 만든다. 검사가 먼저고, 하나라도 걸리면 빌드하지 않는다.

    uv run --no-sync python scripts/build.py

순서: 단위 테스트 → stdio 스모크(진짜 서버 프로세스) → 모듈 import → PyInstaller → Inno Setup.
server.py가 깨진 채로 인스톨러가 만들어져 설치된 적이 있다 — 단위 테스트는 server.py를
안 건드려서 통과했고, 스모크를 안 돌렸다. 그래서 이 파일이 있다.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 윈도우 콘솔은 cp949라 한글 단계 이름이 깨진다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError, ValueError):
        pass
ISCC = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"
PY = [sys.executable]


def step(label: str, cmd: list[str], **kw) -> None:
    print(f"\n== {label}")
    started = time.monotonic()
    result = subprocess.run(cmd, cwd=ROOT, **kw)
    if result.returncode != 0:
        print(f"\n[FAIL] {label} (exit {result.returncode}) — 빌드하지 않는다.")
        sys.exit(result.returncode)
    print(f"   ok ({time.monotonic() - started:.0f}s)")


def main() -> None:
    sys.path.insert(0, str(ROOT / "src"))
    from duet import __version__

    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")

    step("단위 테스트", PY + ["-m", "pytest", "-q"], env=env)
    step("stdio 스모크", PY + ["scripts/smoke.py"], env=env)
    step("모듈 import", PY + ["-c", "import duet.server, duet.web, duet.cli, duet.mcp_clients"], env=env)
    step("PyInstaller", PY + ["-m", "PyInstaller", "installer/duet.spec", "--noconfirm", "--distpath", "dist/app"],
         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 묶인 실행 파일이 실제로 뜨는지. 서버는 stdin이 닫히면 스스로 끝난다.
    exe = ROOT / "dist" / "app" / "Duet" / "duet.exe"
    step("duet.exe 기동", [str(exe), "version"], env=dict(env, DUET_NO_BOARD="1"), stdout=subprocess.DEVNULL)

    if not ISCC.exists():
        print(f"\n[FAIL] Inno Setup이 없다: {ISCC}")
        sys.exit(1)
    step("Inno Setup", [str(ISCC), f"/DAppVersion={__version__}", "installer/duet.iss"], stdout=subprocess.DEVNULL)

    out = ROOT / "dist" / f"duet-setup-{__version__}.exe"
    print(f"\n{out}  ({out.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
