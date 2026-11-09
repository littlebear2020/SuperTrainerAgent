"""Regression-lock tests for ViMax video-backend routing (P3c + Stage 5.2 + C5a).

These are CHARACTERIZATION tests, not TDD: they pin the routing behavior in
_resolve_vimax_video_backend. Updated for C5a (2026-10-12): auto/default →
seedance (360-gateway r2v, 主力). Stage 5.2 (2026-07) 曾默认 seedance-ark——当时
360 拦一切人像（InputImageSensitiveContentDetected）故迁 Ark；2026-10-12 用户
决策迁回 360，拦截风险以 C5c 角色 spike 实测为准。seedance-ark 保留为回滚位
(explicit override)。

Policy locked here:
- Explicit override (seedance-ark/seedance/hailuo/happyhorse) always wins.
- auto/None → seedance (360) regardless of style.
- style is accepted for signature stability but does not change auto's result.
- Unknown values raise ValueError (typo 防呆, C5).
"""

from types import SimpleNamespace

import pytest

from app.gateway.video_gen.service import _resolve_vimax_video_backend


def _cfg():
    return SimpleNamespace(
        seedance_video_model="volcengine/doubao-seedance-2-0",
        seedance_ark_video_model="doubao-seedance-2-0-260128",
        happyhorse_video_model="alibaba/happyhorse-1.0-i2v",
        qihu_video_model="minimax/MiniMax-Hailuo-2.3",
    )


def test_auto_cartoon_routes_to_seedance_360():
    """C5a (2026-10-12): auto/default + Cartoon → seedance (360 r2v). Stage 5.2
    曾默认 seedance-ark（360 拦人像时期）；迁回 360 后 seedance-ark 是回滚位。"""
    backend, model = _resolve_vimax_video_backend("Cartoon", "auto", _cfg())
    assert backend == "seedance"
    assert model == "volcengine/doubao-seedance-2-0"


def test_auto_realistic_routes_to_seedance_360():
    backend, _ = _resolve_vimax_video_backend("realistic movie style", "auto", _cfg())
    assert backend == "seedance"


def test_auto_none_style_routes_to_seedance_360():
    backend, _ = _resolve_vimax_video_backend(None, "auto", _cfg())
    assert backend == "seedance"


def test_unknown_backend_value_raises_value_error():
    """C5 防呆：未知值（typo）不得静默落分支——ValueError。"""
    for bad in ("qihu", " 360", "seednce"):
        with pytest.raises(ValueError, match="vimax_video_backend"):
            _resolve_vimax_video_backend(None, bad, _cfg())


def test_explicit_seedance_ark_override_wins():
    """Stage 5.2: explicit seedance-ark is honored."""
    backend, model = _resolve_vimax_video_backend("realistic", "seedance-ark", _cfg())
    assert backend == "seedance-ark"
    assert model == "doubao-seedance-2-0-260128"


def test_explicit_seedance_override_wins():
    """Explicit seedance (360-gateway r2v) is honored (caller accepts the rejection risk)."""
    backend, model = _resolve_vimax_video_backend("Cartoon", "seedance", _cfg())
    assert backend == "seedance"
    assert model == "volcengine/doubao-seedance-2-0"


def test_explicit_happyhorse_override_wins():
    """Stage 5.2: happyhorse retained as a fallback backend (explicit override)."""
    backend, model = _resolve_vimax_video_backend("realistic", "happyhorse", _cfg())
    assert backend == "happyhorse"
    assert model == "alibaba/happyhorse-1.0-i2v"


def test_explicit_hailuo_override_wins():
    backend, model = _resolve_vimax_video_backend("realistic", "hailuo", _cfg())
    assert backend == "hailuo"
    assert model == "minimax/MiniMax-Hailuo-2.3"


def test_auto_uppercase_override_normalized():
    """'AUTO' (uppercase) is treated as 'auto'."""
    backend, _ = _resolve_vimax_video_backend("Cartoon", "AUTO", _cfg())
    assert backend == "seedance"
