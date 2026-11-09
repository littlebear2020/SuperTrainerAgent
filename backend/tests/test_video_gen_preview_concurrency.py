"""E2→T2: preview 生图并发闸（2026-08-31 批量预览并发化）。

E2 原始问题：preview 无并发闸，54 并发第 54 个等 ~54s 才启动 → >nginx 60s → 504。
T2（2026-08-31）：E2 的 Semaphore(3) 已被实测推翻（360 服务端 12 路并发 12/12 成功、
零排队、33s 全部完成）——闸改 config ``preview_all_concurrency``（默认 12）驱动的
懒构造单例（与 get_ai_api_semaphore 同模式）。
"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


async def _make_preview_svc(tmp_path, config=None):
    from app.gateway.video_gen.service import VideoGenService

    user_id = "u1"
    task_id = "t1"
    task_dir = (tmp_path / user_id / "tasks" / task_id).resolve()
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "image_prompt": "p", "narration": "n", "duration": 5.0}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    in_flight = {"n": 0, "max": 0}

    # data: URI——_generate_frame_preview 成功生图后会调 _localize_image 本地化；
    # data 分支本地解码落盘零网络（http://x 会走 httpx + _LOCALIZE_RETRY_DELAYS
    # [0,1,2]s 退避，16 路并发全失败也把整批拖满 ~3s）。并发观测点在
    # generate_image 内的 in_flight 计数，不受本地化形态影响。
    _TINY_PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="

    class FakeProvider:
        async def generate_image(self, prompt, **k):
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
            await asyncio.sleep(0.05)
            in_flight["n"] -= 1
            return MagicMock(url=_TINY_PNG)

    providers = MagicMock()
    providers.ai_image = FakeProvider()
    # E7：预览经 make_ai_image(payload.image_provider)；None → 默认实例。
    providers.make_ai_image = lambda name: providers.ai_image

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    svc.repo = MagicMock()
    svc.repo.get = AsyncMock(return_value=MagicMock(payload="{}", status="draft", task_type="ai_image"))
    svc._providers = providers
    svc._config = config if config is not None else MagicMock()  # MagicMock 桩 config → 自动属性非 int → 回落默认
    svc._update_storyboard = AsyncMock()
    return svc, task_id, user_id, in_flight


@pytest.fixture(autouse=True)
def _fresh_preview_sem():
    """隔离懒单例：每用例按本用例 config 重建信号量，用后复位。"""
    from app.gateway.video_gen import service as svc_mod

    svc_mod._reset_preview_sem_for_test()
    yield
    svc_mod._reset_preview_sem_for_test()


def _stub_irrelevant_helpers(monkeypatch, svc_mod):
    # 隔离并发闸测试：桩掉 character/i2i helpers（不关心它们，只验 Semaphore）
    monkeypatch.setattr(svc_mod, "_inject_project_character_context", lambda *a, **k: None)
    monkeypatch.setattr(svc_mod, "_warn_if_image_provider_not_i2i", lambda *a, **k: None)
    monkeypatch.setattr(svc_mod, "_character_ref_paths_from_payload", lambda p: None)


def test_config_preview_all_concurrency_default_12():
    """T2 计划§四.8：config 默认值 12 pin（拍板值，360 服务端 12 路实测零排队）。"""
    from app.gateway.video_gen.config import VideoGenConfig

    assert VideoGenConfig().preview_all_concurrency == 12


@pytest.mark.asyncio
async def test_preview_sem_default_caps_at_12(tmp_path, monkeypatch):
    """默认（无合法 config 值）：16 个并发 _generate_frame_preview 峰值 ≤12（闸=12）。"""
    from app.gateway.video_gen import service as svc_mod

    _stub_irrelevant_helpers(monkeypatch, svc_mod)
    svc, task_id, user_id, in_flight = await _make_preview_svc(tmp_path)

    await asyncio.gather(*[svc._generate_frame_preview(task_id, 0, user_id) for _ in range(16)])
    assert in_flight["max"] <= 12, f"默认闸 12 须限并发≤12，实测 {in_flight['max']}"
    assert in_flight["max"] == 12, f"16 路并发应打满 12 闸（懒单例定容正确），实测 {in_flight['max']}"
    assert in_flight["max"] >= 1, "须确实跑了 generate_image"


@pytest.mark.asyncio
async def test_preview_sem_respects_configured_cap(tmp_path, monkeypatch):
    """config preview_all_concurrency=3 → 闸 3（配置驱动生效；回退位）。"""
    from app.gateway.video_gen import service as svc_mod

    _stub_irrelevant_helpers(monkeypatch, svc_mod)
    svc, task_id, user_id, in_flight = await _make_preview_svc(tmp_path, config=SimpleNamespace(preview_all_concurrency=3))

    await asyncio.gather(*[svc._generate_frame_preview(task_id, 0, user_id) for _ in range(10)])
    assert in_flight["max"] <= 3, f"配置闸 3 须限并发≤3，实测 {in_flight['max']}"
