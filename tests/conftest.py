from __future__ import annotations

import pytest

from duet import core


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """테스트마다 새 ~/.duet. 진짜 폴더를 건드리지 않게 막는다."""
    monkeypatch.setenv(core.HOME_ENV, str(tmp_path))
    return core.home()
