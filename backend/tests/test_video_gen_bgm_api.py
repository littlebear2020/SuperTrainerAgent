"""Tests for the BGM list endpoint contract and unknown-id graceful degradation.

Task C2 covers `GET /api/video/bgm` exposing `tags`/`duration`; Task C3 covers
`_resolve_bgm_or_warn` degrading an unknown id to "no BGM" with a warning
instead of raising (needed after the 2026-07-31 legacy-track removal, so old
payloads still re-render).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import router


def _make_app(tmp_path):
    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    mock_svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = mock_svc
    return app


def test_resolve_bgm_or_warn_unknown_id_returns_none(caplog):
    from app.gateway.video_gen.service import _resolve_bgm_or_warn

    with caplog.at_level("WARNING"):
        # 用从未存在的 id：与曲库内容无耦合，C3 可先于 C1（删旧曲）转绿。
        # 「已下架的旧 id」场景覆盖由 C1 的 test_removed_legacy_ids_gone 负责。
        assert _resolve_bgm_or_warn("deleted_legacy_track") is None
    assert any("deleted_legacy_track" in r.message for r in caplog.records)


def test_preview_unknown_bgm_returns_404(tmp_path):
    client = TestClient(_make_app(tmp_path))
    resp = client.get("/api/video/bgm/no_such_track/preview")
    assert resp.status_code == 404


def test_list_bgm_exposes_tags_and_duration(tmp_path):
    client = TestClient(_make_app(tmp_path))
    resp = client.get("/api/video/bgm")
    assert resp.status_code == 200
    built_in = resp.json()["built_in"]
    assert len(built_in) == 4
    track = next(t for t in built_in if t["id"] == "morning_piano")
    assert track["duration"] == 179
    assert "钢琴" in track["tags"]
