"""MCP 클라이언트 설정에 Duet을 등록한다.

인스톨러가 이 코드를 불러 클라이언트 설정 파일에 항목 하나를 더한다. 사용자가 JSON을
직접 고칠 필요가 없어진다. 서재(`seojae.mcp_clients`)에서 가져와 Duet에 맞췄다.

**남의 설정을 건드리지 않는 것이 이 파일의 전부다.** 그 파일에는 다른 MCP 서버와
앱의 설정이 같이 들어 있고, 그건 우리 것이 아니다. 그래서:

- 통째로 덮어쓰지 않고 서버 목록 안의 우리 키 하나만 손댄다
- 고치기 전에 원본을 백업한다
- 임시 파일에 다 쓴 뒤 바꿔치기한다. 설치가 중간에 끊겨도 설정이 잘리지 않게
- 읽을 수 없는 JSON이면 **멈춘다.** 망가진 파일을 새로 쓰면 남의 설정이 통째로 사라진다

Duet은 Claude Code가 첫째다. **사용자 범위**(`~/.claude.json`)에 등록해야 모든 프로젝트
창에서 붙는다 — 프로젝트마다 `.mcp.json`을 두면 창마다 물어보고, 새 프로젝트에는 없다.
그 파일은 세션 캐시까지 든 큰 파일이라 서재는 피했지만, Duet에게는 그것이 핵심이다.
백업과 바꿔치기가 그래서 있다.
"""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

SERVER_KEY = "duet"
DEFAULT_CLIENT = "claude-code"


class ConfigError(Exception):
    """설정 파일을 안전하게 고칠 수 없을 때."""


class UnknownClient(ConfigError):
    """모르는 클라이언트 이름."""


def _roaming() -> Path:
    base = os.environ.get("APPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Roaming"


def _app_support() -> Path:
    """앱 설정이 놓이는 곳. 윈도우는 Roaming, 맥은 Application Support."""
    if sys.platform == "win32":
        return _roaming()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support"
    base = os.environ.get("XDG_CONFIG_HOME")
    return Path(base) if base else Path.home() / ".config"


@dataclass(frozen=True)
class Client:
    """MCP 클라이언트 하나. 앱마다 다른 것은 파일 위치와 키 이름뿐이다."""

    name: str
    label: str
    path: Callable[[], Path]
    #: 서버 목록이 담기는 키. 대부분 mcpServers 지만 VS Code는 servers 다.
    servers_key: str = "mcpServers"
    #: 등록한 뒤 사용자가 해야 할 일. **화면에 그대로 나가므로 존댓말로 쓴다.**
    after: str = "앱을 껐다 켜면 Duet이 붙습니다."


#: 아는 클라이언트들. 새로 생기면 여기 한 줄 더한다.
CLIENTS: dict[str, Client] = {
    "claude-code": Client(
        name="claude-code",
        label="Claude Code (모든 프로젝트)",
        # 사용자 범위. `claude mcp add -s user` 가 쓰는 바로 그 파일이다.
        path=lambda: Path.home() / ".claude.json",
        after="새 창을 열면 붙습니다. 이미 열린 창은 다시 열어야 합니다.",
    ),
    "claude-desktop": Client(
        name="claude-desktop",
        label="Claude Desktop",
        path=lambda: _app_support() / "Claude" / "claude_desktop_config.json",
        after="Claude Desktop을 껐다 켜세요. 설정은 시작할 때만 읽습니다.",
    ),
    "cursor": Client(
        name="cursor",
        label="Cursor",
        path=lambda: Path.home() / ".cursor" / "mcp.json",
        after="Cursor를 껐다 켜거나, 설정에서 MCP 서버를 새로 고치세요.",
    ),
    "vscode": Client(
        name="vscode",
        label="VS Code (Copilot)",
        path=lambda: _app_support() / "Code" / "User" / "mcp.json",
        servers_key="servers",
        after="VS Code를 껐다 켜세요.",
    ),
    "windsurf": Client(
        name="windsurf",
        label="Windsurf",
        path=lambda: Path.home() / ".codeium" / "windsurf" / "mcp_config.json",
    ),
}


def get_client(name: str | None = None) -> Client:
    key = (name or DEFAULT_CLIENT).strip().lower()
    if key not in CLIENTS:
        raise UnknownClient(f"모르는 클라이언트다: {name}\n아는 것: {', '.join(CLIENTS)}")
    return CLIENTS[key]


def _load(path: Path) -> dict:
    """기존 설정을 읽는다. 없으면 빈 것, 망가졌으면 예외."""
    if not path.is_file():
        return {}
    try:
        # utf-8-sig: 메모장이나 Out-File 로 고친 파일에 붙는 BOM을 뗀다. 실제로 걸렸다.
        text = path.read_text(encoding="utf-8-sig")
    except OSError as e:
        raise ConfigError(f"설정 파일을 읽지 못했다: {e}") from e
    if not text.strip():
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"설정 파일의 형식이 깨져 있다 ({path}, {e.lineno}번째 줄).\n"
            "직접 고친 뒤 다시 시도하라. 여기서 새로 쓰면 다른 MCP 서버 설정이 사라진다."
        ) from e
    if not isinstance(data, dict):
        raise ConfigError(f"설정 파일의 최상위가 객체가 아니다: {path}")
    return data


def _backup(path: Path) -> Path | None:
    """고치기 전 원본을 남긴다."""
    if not path.is_file():
        return None
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = path.with_name(f"{path.stem}.{stamp}.bak")
    try:
        target.write_bytes(path.read_bytes())
    except OSError:
        return None
    return target


def _save(path: Path, data: dict) -> None:
    """임시 파일에 다 쓴 뒤 바꿔치기한다. 중간에 끊겨도 원본이 잘리지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        tmp.unlink(missing_ok=True)
        raise ConfigError(f"설정 파일을 쓰지 못했다: {e}") from e


def register(command: Path | str, client: str | None = None, args: list[str] | None = None) -> str:
    """Duet을 그 클라이언트에 등록한다. 이미 있으면 명령 경로를 갱신한다."""
    app = get_client(client)
    path = app.path()
    data = _load(path)

    servers = data.get(app.servers_key)
    if servers is None:
        servers = {}
        data[app.servers_key] = servers
    elif not isinstance(servers, dict):
        raise ConfigError(f"설정 파일의 {app.servers_key} 가 객체가 아니다: {path}")

    entry: dict = {"command": str(command)}
    if args:
        entry["args"] = list(args)
    if app.name == "claude-code":
        entry["type"] = "stdio"  # `claude mcp add` 가 적는 꼴과 같게

    existed = SERVER_KEY in servers
    if existed and servers[SERVER_KEY] == entry:
        return f"{app.label}: 이미 등록되어 있다 ({path})"

    backup = _backup(path)
    servers[SERVER_KEY] = entry
    _save(path, data)

    what = "갱신했다" if existed else "등록했다"
    others = [k for k in servers if k != SERVER_KEY]
    note = f" (다른 서버 {len(others)}개는 그대로)" if others else ""
    tail = f"\n원본 백업: {backup}" if backup else ""
    return f"{app.label}에 Duet을 {what}{note}: {path}{tail}"


def unregister(client: str | None = None) -> str:
    """등록을 지운다. 우리 항목만 지우고 나머지는 건드리지 않는다."""
    app = get_client(client)
    path = app.path()
    if not path.is_file():
        return f"{app.label}: 등록된 것이 없다 (설정 파일 없음)."

    data = _load(path)
    servers = data.get(app.servers_key)
    if not isinstance(servers, dict) or SERVER_KEY not in servers:
        return f"{app.label}: 등록된 것이 없다."

    backup = _backup(path)
    del servers[SERVER_KEY]
    _save(path, data)
    tail = f"\n원본 백업: {backup}" if backup else ""
    return f"{app.label}에서 Duet 등록을 지웠다: {path}{tail}"


def registered_command(client: str | None = None) -> str | None:
    """지금 등록된 실행 명령. 없으면 None."""
    app = get_client(client)
    try:
        servers = _load(app.path()).get(app.servers_key)
    except ConfigError:
        return None
    if not isinstance(servers, dict):
        return None
    entry = servers.get(SERVER_KEY)
    return entry.get("command") if isinstance(entry, dict) else None


def survey() -> list[dict]:
    """아는 클라이언트를 전부 훑는다. 설치돼 있지 않은 앱도 남긴다 — 없다고 말하는 편이 낫다."""
    out = []
    for app in CLIENTS.values():
        path = app.path()
        try:
            command = registered_command(app.name)
            broken = False
        except Exception:
            command, broken = None, True
        out.append({
            "name": app.name, "label": app.label, "path": str(path),
            "exists": path.is_file(), "registered": command is not None,
            "command": command, "after": app.after, "broken": broken,
        })
    return out
