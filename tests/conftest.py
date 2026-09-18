"""테스트 전역 설정.

`approved_store`의 저장 경로 기본값은 상대경로라 cwd 기준으로 풀린다. pytest 는
저장소 루트에서 돌기 때문에, 명시적으로 `store_path`를 넘기지 않는 테스트가
**저장소 루트의 `approved.jsonl` / `new_term_candidates.json` 을 덮어쓴다.**

실제로 그렇게 됐다 — 테스트를 한 번 돌리면 루트의 `new_term_candidates.json` 이
`evil_card`·`flavorcard` 같은 픽스처 카드 id 20건으로 채워지고 `ko_rendering` 이
전부 빈 문자열이 된다. 결정 ④는 기록형 용어를 탐지 시점에 그 파일에 적재하라고
요구하므로, 생산 경로와 테스트가 같은 파일을 공유하는 상태였다.

여기서 한 번에 막는다. 각 테스트는 자기 tmp 디렉터리에 쓴다.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))


@pytest.fixture(autouse=True)
def _isolate_stores(tmp_path, monkeypatch):
    """저장소 기본 경로를 테스트별 tmp 디렉터리로 돌린다."""
    import approved_store

    monkeypatch.setattr(
        approved_store, "DEFAULT_APPROVED_STORE", tmp_path / "approved.jsonl",
        raising=False,
    )
    monkeypatch.setattr(
        approved_store, "DEFAULT_NEW_TERM_STORE", tmp_path / "new_term_candidates.json",
        raising=False,
    )
    yield
