"""F1 核实优化：effective_stall_timeout 须真正接入（非死代码）。

E5a 是 75% 卡死的实际修复（每帧 progress）；E5b（effective_stall_timeout）是安全网——
按任务帧数缩放 stall_timeout，让长渲染在 E5a 万一失败时也有足够窗口不被误杀。
本文件测 hook 本身；reaper 用 per-task 缩放的集成测在 test_task_framework.py
（test_reaper_uses_per_task_scaled_timeout）。
"""

import json
from types import SimpleNamespace


def test_stall_timeout_for_task_scales_by_frame_count():
    """VideoGenService.stall_timeout_for_task 须按 payload 帧数缩放（调用 effective_stall_timeout）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._config = VideoGenConfig()
    svc.stall_timeout = 1800
    # target=432s / spf=8 → _resolve_n_scenes=54 → effective_stall_timeout=1800+60*54=5040
    t54 = SimpleNamespace(payload=json.dumps({"target_duration": 432, "seconds_per_frame": 8}))
    assert svc.stall_timeout_for_task(t54) == 1800 + 60 * 54
    # 无帧信号（仅 task_type/topic）→ 回落默认
    t_plain = SimpleNamespace(payload=json.dumps({"task_type": "ai_image", "topic": "x"}))
    assert svc.stall_timeout_for_task(t_plain) == 1800
    # 无 payload → 默认
    assert svc.stall_timeout_for_task(SimpleNamespace(payload=None)) == 1800


def test_base_stall_timeout_for_task_defaults_to_global():
    """BaseTaskService 默认 hook = self.stall_timeout（非 video-gen 域不缩放，不破坏既有行为）。"""
    from app.gateway._shared.task_framework.service import BaseTaskService

    # BaseTaskService is abstract; exercise the default hook via __dict__ bound method
    class _Bare(BaseTaskService):
        domain = "bare"
        id_prefix = "br"

        async def _run_pipeline(self, *a, **k):
            return None

    b = _Bare.__new__(_Bare)
    b.stall_timeout = 1800
    assert b.stall_timeout_for_task(SimpleNamespace(payload="{}")) == 1800


# --- Task 17 (E17): effective_stall_timeout 须尊重 stall_timeout_seconds ---


def test_effective_stall_timeout_uses_configured_base():
    """基数须取 stall_timeout_seconds，而非硬编码 1800。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig(stall_timeout_seconds=600)
    # 10 帧：600 + 60*10 = 1200（旧实现硬编码基数 1800 → 2400）
    assert cfg.effective_stall_timeout(10) == 600 + 60 * 10


def test_effective_stall_timeout_zero_disables_reaper():
    """字段注释写明「set 0 to disable」——设 0 必须真的禁用（返回 0），不能仍返回 60*N。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig(stall_timeout_seconds=0)
    assert cfg.effective_stall_timeout(54) == 0
    assert cfg.effective_stall_timeout(0) == 0


def test_effective_stall_timeout_default_preserves_existing_behavior():
    """默认 1800 时行为与旧实现一致，不破坏既有部署预期。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig()
    assert cfg.effective_stall_timeout(54) == 1800 + 60 * 54


def test_stall_timeout_for_task_survives_task_without_user_id():
    """回归：task 缺 user_id/id 时不得吞掉 payload 缩放分支。

    磁盘优先分支访问 task.user_id/task.id，对 reaper 传入的精简 task 对象会抛
    AttributeError；旧实现被外层 except Exception 一并吞掉，连 payload 回落也丢了，
    于是 54 帧任务拿到默认 1800s 被误杀。
    """
    import json as _json
    from types import SimpleNamespace as _NS

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._config = VideoGenConfig()
    svc._storage_dir = "/nonexistent-for-test"
    svc.stall_timeout = 1800
    t = _NS(payload=_json.dumps({"target_duration": 432, "seconds_per_frame": 8}))
    assert svc.stall_timeout_for_task(t) == 1800 + 60 * 54


def test_video_gen_service_enables_fixed_progress_stall_window():
    """T6->W5（2026-08-24 活性重构）：固定冻结窗口改为配置驱动。

    旧硬编码 PROGRESS_STALL_TIMEOUT=20*60 已退役；新默认 = 配置
    ``video_gen.stall_progress_freeze_seconds``（1800=P5 无覆盖兜底窗口；
    回滚到旧行为 = 配置 1200）。谓词 C 同时降级为无覆盖阶段兜底。
    """
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    assert VideoGenConfig().stall_progress_freeze_seconds == 1800
    assert VideoGenService.progress_stall_timeout is None  # 类默认关闭，实例从 config 注入
