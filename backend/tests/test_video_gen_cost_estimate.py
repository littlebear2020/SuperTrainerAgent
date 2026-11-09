"""Task 6（2026-10-20）：ViMax 费用与成片时长预估（只读，不触发 provider）。

口径（已拍板）：近 10 个成功 vimax 任务每镜中位数 + 价目表；只给区间；
token 计费/无历史标 partial；同时返回草稿已花费。禁止伪造精确金额。
"""

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.gateway.video_gen.cost_estimate import VideoCostEstimate, estimate_vimax_cost

PRICES = {
    "image": {"unit": "per_image", "unit_price": 0.5, "currency": "CNY"},
    "video": {"unit": "per_second", "unit_price": 0.58, "currency": "CNY"},
    "tts": {"unit": "per_char", "unit_price": 0.0001, "currency": "CNY"},
}


def test_estimate_8_shots_64s_per_second_price_table():
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=1, tts_chars=100, prices=PRICES)
    # image = (8 + 3*1) * 0.5 = 5.5; video = 64 * 0.58 = 37.12; tts = 0.01
    assert est.n_shots == 8
    assert est.video_seconds == 64
    assert est.point_cny == pytest.approx(42.63, abs=0.01)
    assert est.low_cny is not None and est.high_cny is not None
    assert est.low_cny < est.point_cny < est.high_cny
    assert est.basis == "partial"
    assert "transition" in est.unpriced
    assert est.breakdown["image"] == pytest.approx(5.5)
    assert est.breakdown["video"] == pytest.approx(37.12)


def test_estimate_35_shots_280s():
    est = estimate_vimax_cost(n_shots=35, video_seconds=280, n_characters=2, tts_chars=1180, prices=PRICES)
    assert est.n_shots == 35
    assert est.video_seconds == 280
    assert est.point_cny == pytest.approx((35 + 6) * 0.5 + 280 * 0.58 + 1180 * 0.0001, abs=0.01)


def test_token_priced_video_uses_history_median():
    prices = {**PRICES, "video": {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}}
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=0, tts_chars=0, prices=prices, history_per_shot=1.5)
    assert est.basis == "history"
    assert est.breakdown["video"] == pytest.approx(12.0)
    # 历史 = 整任务每镜总成本（已含图片/TTS），不得与显式分项重复相加。
    assert est.breakdown["image"] is None
    assert est.point_cny == pytest.approx(12.0)


def test_history_single_sample_uses_forty_percent_range():
    prices = {**PRICES, "video": {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}}
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=0, tts_chars=0, prices=prices, history_per_shot=1.5, history_sample_count=1)
    assert est.basis == "history"
    assert est.low_cny == pytest.approx(7.2)
    assert est.high_cny == pytest.approx(16.8)


def test_token_priced_video_without_history_is_partial_and_unpriced():
    prices = {**PRICES, "video": {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}}
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=0, tts_chars=0, prices=prices)
    assert est.basis == "partial"
    assert est.point_cny is None
    assert est.low_cny is None and est.high_cny is None
    assert "video" in est.unpriced


def test_tts_per_kilo_char_is_supported():
    prices = {**PRICES, "tts": {"unit": "per_kilo_char", "unit_price": 30.0, "currency": "CNY"}}
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=0, tts_chars=1180, prices=prices)
    assert est.breakdown["tts"] == pytest.approx(35.4)
    assert "tts" not in est.unpriced


def test_no_prices_at_all_is_partial_without_fake_point():
    est = estimate_vimax_cost(n_shots=8, video_seconds=64, n_characters=0, tts_chars=0, prices={})
    assert est.basis == "partial"
    assert est.point_cny is None
    assert {"image", "video"} <= set(est.unpriced)


def test_cost_resolve_prices_uses_payload_and_config_providers(monkeypatch):
    """费用预估必须复用 ViMax 实际 backend 选择（360→seedream-360 / seedance）。"""
    import app.gateway._shared.media_pricing_resolver as mpr
    from app.gateway.routers.video_gen import _cost_resolve_prices

    calls: list[tuple] = []

    def fake_resolve(provider, model_id, media_type):
        calls.append((provider, model_id, media_type))
        return {"unit": "per_image", "unit_price": 0.5, "currency": "CNY"}

    monkeypatch.setattr(mpr, "resolve_media_price", fake_resolve)
    cfg = types.SimpleNamespace(
        vimax_image_backend="360",
        vimax_video_backend="seedance",
        seedream_360_image_model="bytedance/doubao-seedream-5.0-pro",
        seedream_image_model="doubao-seedream-5-0-pro-260628",
        seedance_video_model="bytedance/doubao-seedance-2-0",
    )
    prices = _cost_resolve_prices({}, cfg, voice_id="doubao:zh_male_A")

    assert calls[:3] == [("seedream-360", None, "image"), ("seedance", None, "video"), ("doubao_tts", None, "tts")]
    assert prices["image"] is not None and prices["video"] is not None


def test_default_vimax_backend_maps_to_vimax_pricing_providers():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import _resolve_vimax_backend_selection

    selection = _resolve_vimax_backend_selection(VideoGenConfig(qihu_api_key="t"))
    assert selection["image_pricing_provider"] == "seedream-360"
    assert selection["video_pricing_provider"] == "seedance"


@pytest.mark.asyncio
async def test_history_cache_is_bounded_lru():
    from app.gateway.routers import video_gen as router_mod

    router_mod._COST_HISTORY_CACHE.clear()
    svc = MagicMock()
    svc.domain = "video_gen"
    svc.repo.list_by_user = AsyncMock(return_value=[])
    for i in range(130):
        await router_mod._cost_history_per_shot(svc, f"user-{i}", "vg_cost")
    assert len(router_mod._COST_HISTORY_CACHE) <= 128
    router_mod._COST_HISTORY_CACHE.clear()


# ── endpoint：200 / 404 / 409 ─────────────────────────────────────────────


def _task(status: str = "draft", task_id: str = "vg_cost", user_id: str = "u1") -> types.SimpleNamespace:
    return types.SimpleNamespace(
        id=task_id,
        user_id=user_id,
        domain="video_gen",
        task_type="vimax",
        status=status,
        progress=40,
        retry_count=0,
        result="{}",
        payload=json.dumps({"mode": "fixed", "script": "x"}, ensure_ascii=False),
    )


def _service(tmp_path, task) -> MagicMock:
    svc = MagicMock()
    svc._storage_dir = str(tmp_path)
    svc.repo = MagicMock()
    svc.repo.get = AsyncMock(return_value=task)
    svc._config = types.SimpleNamespace(ai_image_provider="seedream", ai_video_provider="seedance")
    return svc


def _request(svc) -> types.SimpleNamespace:
    return types.SimpleNamespace(app=types.SimpleNamespace(state=types.SimpleNamespace(video_gen_service=svc)))


def _seed_storyboard(tmp_path, frames: int = 8, total_duration: float = 64.0) -> None:
    from deerflow.runtime.user_context import get_effective_user_id

    task_dir = tmp_path / get_effective_user_id() / "tasks" / "vg_cost"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {"video_type": "vimax", "width": 1080, "height": 1920},
                "frames": [{"index": i, "narration": "旁白" * 10, "duration": 8.0} for i in range(frames)],
                "total_duration": total_duration,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_endpoint_returns_estimate_for_draft(tmp_path):
    from app.gateway.routers.video_gen import get_video_cost_estimate

    _seed_storyboard(tmp_path, frames=8, total_duration=64.0)
    svc = _service(tmp_path, _task("draft"))
    with (
        patch("app.gateway.routers.video_gen._cost_resolve_prices", return_value=PRICES),
        patch("app.gateway.routers.video_gen._cost_history_per_shot", new=AsyncMock(return_value=(None, 0))),
        patch("app.gateway.routers.video_gen._cost_spent_cny", new=AsyncMock(return_value=3.54)),
    ):
        est = await get_video_cost_estimate(_request(svc), "vg_cost")

    assert isinstance(est, VideoCostEstimate)
    assert est.n_shots == 8
    assert est.video_seconds == 64
    assert est.spent_cny == pytest.approx(3.54)
    assert est.point_cny is not None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "frames,total,target,expected_shots,expected_seconds",
    [
        (35, 238.06, None, 30, 240),
        (8, 34.54, None, 5, 40),
        (8, 64.0, 60, 8, 64),
    ],
)
async def test_endpoint_uses_resolved_budget_for_shots_and_duration(tmp_path, frames, total, target, expected_shots, expected_seconds):
    from app.gateway.routers.video_gen import get_video_cost_estimate

    _seed_storyboard(tmp_path, frames=frames, total_duration=total)
    task = _task("draft")
    payload = {"mode": "fixed", "script": "x"}
    if target is not None:
        payload["target_duration"] = target
    task.payload = json.dumps(payload)
    svc = _service(tmp_path, task)
    with (
        patch("app.gateway.routers.video_gen._cost_resolve_prices", return_value=PRICES),
        patch("app.gateway.routers.video_gen._cost_history_per_shot", new=AsyncMock(return_value=(None, 0))),
        patch("app.gateway.routers.video_gen._cost_spent_cny", new=AsyncMock(return_value=None)),
    ):
        est = await get_video_cost_estimate(_request(svc), "vg_cost")

    assert est.n_shots == expected_shots
    assert est.video_seconds == expected_seconds
    assert est.n_characters == 2, "草稿阶段无 characters.json 默认按 2 个角色三视图计"
    assert "transition" in est.unpriced, "转场未计必须标 partial"


@pytest.mark.asyncio
async def test_endpoint_404_when_storyboard_missing(tmp_path):
    from app.gateway.routers.video_gen import get_video_cost_estimate

    svc = _service(tmp_path, _task("draft"))
    with pytest.raises(HTTPException) as ei:
        await get_video_cost_estimate(_request(svc), "vg_cost")
    assert ei.value.status_code == 404


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["running", "composing", "pending"])
async def test_endpoint_409_while_not_estimable(tmp_path, status):
    from app.gateway.routers.video_gen import get_video_cost_estimate

    _seed_storyboard(tmp_path)
    svc = _service(tmp_path, _task(status))
    with pytest.raises(HTTPException) as ei:
        await get_video_cost_estimate(_request(svc), "vg_cost")
    assert ei.value.status_code == 409


@pytest.mark.asyncio
async def test_endpoint_404_for_other_users_task(tmp_path):
    from app.gateway.routers.video_gen import get_video_cost_estimate

    _seed_storyboard(tmp_path)
    svc = _service(tmp_path, None)
    with pytest.raises(HTTPException) as ei:
        await get_video_cost_estimate(_request(svc), "vg_cost")
    assert ei.value.status_code == 404
