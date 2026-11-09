"""Tests for voice_id helpers + the shared voice catalog (A1, 2026-08-09).

原 6 条 PRESET_VOICES 白名单已删除，目录事实源迁至
``app.gateway._shared.voice_catalog``（35 条，与 video-gen 共用）。
"""

from __future__ import annotations

import pytest

from app.gateway._shared.voice_catalog import CURATED_VOICES
from app.gateway.voice_twin.preset_voices import (
    get_preset_voice_name,
    is_doubao_voice_id,
    is_edge_voice_id,
    is_preset_voice_id,
    parse_doubao_voice_id,
    parse_edge_voice_id,
    parse_preset_voice_id,
)


class TestIsPresetVoiceId:
    def test_valid_preset(self) -> None:
        assert is_preset_voice_id("preset:146") is True

    def test_valid_preset_another(self) -> None:
        assert is_preset_voice_id("preset:284") is True

    def test_all_catalog_qihu_ids(self) -> None:
        for voice in CURATED_VOICES:
            if voice.provider == "qihu":
                assert is_preset_voice_id(voice.id) is True

    def test_non_preset_uuid(self) -> None:
        assert is_preset_voice_id("abc123def456") is False

    def test_preset_prefix_unlisted_id(self) -> None:
        """白名单外 id 仍为 preset（2026-07-22 起纯语法判断，白名单仅展示名）。"""
        assert is_preset_voice_id("preset:999") is True

    def test_preset_prefix_non_numeric(self) -> None:
        assert is_preset_voice_id("preset:abc") is False

    def test_empty_string(self) -> None:
        assert is_preset_voice_id("") is False

    def test_just_prefix(self) -> None:
        assert is_preset_voice_id("preset:") is False


def test_is_preset_voice_id_is_syntactic():
    """任意 preset:{int} 均为真（与 video-gen 对齐；白名单只用于展示名）。"""
    assert is_preset_voice_id("preset:332") is True  # 目录有、旧白名单无——曾 False → 404
    assert is_preset_voice_id("preset:999") is True
    assert is_preset_voice_id("preset:abc") is False
    assert is_preset_voice_id("preset:") is False
    assert is_preset_voice_id("doubao:x") is False


class TestParsePresetVoiceId:
    def test_parses_id(self) -> None:
        assert parse_preset_voice_id("preset:146") == 146

    def test_parses_different_id(self) -> None:
        assert parse_preset_voice_id("preset:340") == 340

    def test_raises_on_bad_format(self) -> None:
        with pytest.raises((ValueError, IndexError)):
            parse_preset_voice_id("not_preset")


class TestEdgeVoiceId:
    """edge:{ShortName} 语法（A2 接入的前缀形态，A1 随目录统一落地）。"""

    def test_is_edge(self) -> None:
        assert is_edge_voice_id("edge:zh-CN-XiaoxiaoNeural") is True

    def test_bare_shortname_is_not_edge(self) -> None:
        """video-gen legacy 裸 id 不属于 voice-twin 的 edge: 语法。"""
        assert is_edge_voice_id("zh-CN-XiaoxiaoNeural") is False

    def test_just_prefix(self) -> None:
        assert is_edge_voice_id("edge:") is False

    def test_parse(self) -> None:
        assert parse_edge_voice_id("edge:zh-CN-XiaoxiaoNeural") == "zh-CN-XiaoxiaoNeural"

    def test_parse_keeps_inner_colons(self) -> None:
        assert parse_edge_voice_id("edge:a:b") == "a:b"


class TestDoubaoVoiceId:
    def test_is_doubao(self) -> None:
        assert is_doubao_voice_id("doubao:zh_female_vv_uranus_bigtts") is True

    def test_parse(self) -> None:
        assert parse_doubao_voice_id("doubao:spk_x") == "spk_x"

    def test_parse_rejects_path_traversal(self) -> None:
        """D3 纵深防御：doubao:../../etc/passwd 一类输入必须被拒。"""
        with pytest.raises(ValueError):
            parse_doubao_voice_id("doubao:../../etc/passwd")

    def test_parse_rejects_backslash_and_dotdot(self) -> None:
        with pytest.raises(ValueError):
            parse_doubao_voice_id("doubao:a\\b")
        with pytest.raises(ValueError):
            parse_doubao_voice_id("doubao:a..b")


class TestPreviewCacheKeySanitize:
    """D3：preview 缓存键对恶意 voice_id 不逃逸目录。"""

    def test_traversal_neutralized(self) -> None:
        from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

        safe = _safe_cache_voice_id("doubao:../../etc/passwd")
        # 路径分隔符必须被洗白（".." 白名单内保留点号无害——缓存键恒为
        # 单文件名 {safe}_{md5}.{ext}，不会构成独立 ".." 路径段）
        assert "/" not in safe and "\\" not in safe
        from pathlib import Path

        resolved = (Path("/cache/root") / f"{safe}_abc12345.mp3").resolve()
        assert resolved.parent == Path("/cache/root")

    def test_normal_ids_stable(self) -> None:
        from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

        assert _safe_cache_voice_id("preset:146") == "preset_146"
        assert _safe_cache_voice_id("edge:zh-CN-XiaoxiaoNeural") == "edge_zh-CN-XiaoxiaoNeural"
        assert _safe_cache_voice_id("a1b2c3d4e5f60708") == "a1b2c3d4e5f60708"


class TestGetPresetVoiceName:
    def test_known_voice(self) -> None:
        """目录内音色返回共享目录名（A1：以共享目录名为准，含「（360）」后缀）。"""
        name = get_preset_voice_name("preset:146")
        assert name == "知性女声（360）"

    def test_known_male_voice(self) -> None:
        name = get_preset_voice_name("preset:340")
        assert name == "儒雅公子（360）"

    def test_old_whitelist_only_voice_now_unknown(self) -> None:
        """旧 6 条白名单里目录外的 id（284/292/142）→ None，调用方有兜底文案。"""
        assert get_preset_voice_name("preset:284") is None

    def test_unknown_voice(self) -> None:
        assert get_preset_voice_name("preset:999") is None

    def test_non_preset_id(self) -> None:
        assert get_preset_voice_name("doubao:x") is None


class TestSharedCatalog:
    """共享目录结构性断言（35 条、字段齐全、id 带前缀唯一）。"""

    def test_count(self) -> None:
        assert len(CURATED_VOICES) == 35

    def test_ids_unique_and_prefixed(self) -> None:
        ids = [v.id for v in CURATED_VOICES]
        assert len(ids) == len(set(ids))
        for v in CURATED_VOICES:
            prefix = {"edge-tts": "edge:", "qihu": "preset:", "doubao": "doubao:"}[v.provider]
            assert v.id.startswith(prefix), v.id

    def test_all_providers_present(self) -> None:
        providers = {v.provider for v in CURATED_VOICES}
        assert providers == {"edge-tts", "qihu", "doubao"}

    def test_all_have_required_fields(self) -> None:
        for voice in CURATED_VOICES:
            assert voice.name
            assert voice.gender in ("female", "male")
            assert voice.locale
            assert voice.description
            # A3 标签：五字段齐全（scenario/tone 至少一个）
            assert voice.scenario, voice.id
            assert voice.tone, voice.id

    def test_locale_coverage(self) -> None:
        """7 语言覆盖（zh/en/ja/ko/fr/de/es）。"""
        locales = {v.locale.split("-")[0] for v in CURATED_VOICES}
        assert {"zh", "en", "ja", "ko", "fr", "de", "es"} <= locales


class TestPresetVoicesEndpoint:
    """GET /api/voice-twins/preset-voices（A1：共享目录 + 按凭证过滤分组）。"""

    def _make_app(self, *, qihu: bool, doubao: bool, edge: bool = True):
        from unittest.mock import MagicMock

        from fastapi import FastAPI

        from app.gateway.routers.voice_twin import router

        app = FastAPI()
        app.include_router(router)
        svc = MagicMock()
        svc.qihu_client = MagicMock() if qihu else None
        svc.doubao_client = MagicMock() if doubao else None
        svc.edge_client = MagicMock() if edge else None
        app.state.voice_clone_service = svc
        return app

    def test_full_catalog_when_all_configured(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self._make_app(qihu=True, doubao=True)) as client:
            resp = client.get("/api/voice-twins/preset-voices")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 35
        providers = {v["provider"] for v in data}
        assert providers == {"edge-tts", "qihu", "doubao"}
        for v in data:
            assert v["id"].startswith(("edge:", "preset:", "doubao:"))
            assert v["gender"] in ("male", "female")
            assert v["scenario"] and v["tone"]

    def test_qihu_unconfigured_still_returns_others(self) -> None:
        """qihu 未配置 → 仍返回 edge+doubao 子集（修复静默空列表）。"""
        from fastapi.testclient import TestClient

        with TestClient(self._make_app(qihu=False, doubao=True)) as client:
            resp = client.get("/api/voice-twins/preset-voices")
        data = resp.json()
        assert len(data) == 35 - 12
        assert "qihu" not in {v["provider"] for v in data}
        assert {"edge-tts", "doubao"} == {v["provider"] for v in data}

    def test_only_edge_when_no_credentials(self) -> None:
        from fastapi.testclient import TestClient

        with TestClient(self._make_app(qihu=False, doubao=False)) as client:
            resp = client.get("/api/voice-twins/preset-voices")
        data = resp.json()
        assert data, "edge 组无需凭证，始终可见"
        assert {v["provider"] for v in data} == {"edge-tts"}

    def test_edge_disabled_hides_edge_group(self) -> None:
        """edge_tts.enabled=false（edge_client=None）→ edge 组也不列出。"""
        from fastapi.testclient import TestClient

        with TestClient(self._make_app(qihu=True, doubao=True, edge=False)) as client:
            resp = client.get("/api/voice-twins/preset-voices")
        data = resp.json()
        assert "edge-tts" not in {v["provider"] for v in data}
        assert {v["provider"] for v in data} == {"qihu", "doubao"}

    def test_service_disabled_returns_empty(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.gateway.routers.voice_twin import router

        app = FastAPI()
        app.include_router(router)
        app.state.voice_clone_service = None
        with TestClient(app) as client:
            resp = client.get("/api/voice-twins/preset-voices")
        assert resp.status_code == 200
        assert resp.json() == []


class TestVideoGenLegacyReExport:
    """video-gen 侧 legacy 形态零回归（/api/video/voices 响应逐字节不变）。"""

    def test_legacy_shape(self) -> None:
        from app.gateway.video_gen.orchestration.voices import CURATED_VOICES as LEGACY

        assert len(LEGACY) == 35
        for v in LEGACY:
            assert set(v.keys()) == {"id", "name", "locale", "gender", "provider"}
            assert v["gender"] in ("Male", "Female")

    def test_legacy_edge_ids_unprefixed(self) -> None:
        from app.gateway.video_gen.orchestration.voices import CURATED_VOICES as LEGACY

        edge = [v for v in LEGACY if v["provider"] == "edge-tts"]
        assert edge, "edge voices must exist"
        for v in edge:
            assert not v["id"].startswith("edge:"), v["id"]

    def test_legacy_cloud_ids_prefixed(self) -> None:
        from app.gateway.video_gen.orchestration.voices import CURATED_VOICES as LEGACY

        for v in LEGACY:
            if v["provider"] == "qihu":
                assert v["id"].startswith("preset:")
            elif v["provider"] == "doubao":
                assert v["id"].startswith("doubao:")

    def test_legacy_names_unchanged(self) -> None:
        """抽查若干条目与迁移前逐字节一致。"""
        from app.gateway.video_gen.orchestration.voices import CURATED_VOICES as LEGACY

        by_id = {v["id"]: v for v in LEGACY}
        assert by_id["zh-CN-YunjianNeural"]["name"] == "云健（男）"
        assert by_id["preset:146"]["name"] == "知性女声（360）"
        assert by_id["preset:146"]["gender"] == "Female"
        assert by_id["doubao:zh_female_vv_uranus_bigtts"]["name"] == "Vivi2.0（女·字节）"
