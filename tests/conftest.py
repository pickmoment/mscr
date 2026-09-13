"""테스트가 개발 머신의 `~/.mscr` 설정에 좌우되지 않게 막는다.

시장 모드는 컨텍스트가 비면 저장된 기본값(`~/.mscr/settings.json`의 `market`)으로 떨어진다.
그대로 두면 개발자가 화면에서 미국 모드를 켜 둔 것만으로 한국 기준 테스트가 전부 깨진다.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_mscr_home(monkeypatch, tmp_path_factory):
    home = tmp_path_factory.mktemp("mscr-home")
    monkeypatch.setattr("mscr.credentials.MSCR_HOME", home)
    # 경로를 넘기지 않은 DB 접근이 실제 ~/.mscr/mscr.db 로 새지 않게 기본 경로도 임시로 돌린다.
    monkeypatch.setattr("mscr.db.DB_PATH", home / "mscr.db")
    for name in ("MSCR_MARKET", "MSCR_REQUEST_DELAY_SEC", "MSCR_MASSIVE_DELAY_SEC", "MASSIVE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
