from __future__ import annotations

import pytest

from duet import paths


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """테스트마다 새 ~/.duet. DUET_HOME으로 진짜 폴더를 건드리지 않게 막는다."""
    monkeypatch.setenv(paths.HOME_ENV, str(tmp_path))
    return paths.home()
