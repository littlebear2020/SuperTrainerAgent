"""Worker health probe and pipeline version decision."""

from __future__ import annotations

import logging
import os
import time

import httpx

from .storyboard import Storyboard

logger = logging.getLogger(__name__)

_probe_cache: tuple[list[str], float] | None = None
_CACHE_TTL = 60.0


# NEW-3 (Task 10): 默认探测地址指向 **worker 服务名**，不是 localhost。
#
# 原默认值 http://localhost:8040 在生产是必败的：gateway 容器监听 8001，而
# production-worker 是**独立容器**（docker-compose-deploy.yaml, replicas: 3）监听
# 8040 —— gateway 自己的 localhost:8040 无人监听。2026-10-05 更新：三份 compose 与
# scripts/serve.sh 已显式设置 WORKER_HEALTH_URL（审计整改 B1/C7），本函数的 env
# 覆盖仅作裸跑/特殊拓扑兜底——历史"全仓零处设置致探测恒败"病灶已闭合。
#
# Compose 网络下 Docker DNS 会把 production-worker 解析到某个 replica（3 个能力
# 相同，探任一即可）。本机裸跑（非 compose）时该名字解析不了，探测仍回落 v1 —— 与
# 修前行为一致，不会更差。
_DEFAULT_WORKER_HEALTH_URL = "http://production-worker:8040"
# T13（2026-09-08 remotion-course）：remotion 池探测地址（独立容器，独立 health）。
# 显式 env 优先；缺省按 compose 服务名解析（同 ffmpeg 池的 DNS 推理）。
_DEFAULT_REMOTION_WORKER_HEALTH_URL = "http://remotion-worker:8040"

# remotion 池探测缓存（与 _probe_cache 同款 TTL；docker stop 后 gateway 缓存可能
# 仍认为可用——测试用例须先重启 gateway 清缓存或等 TTL 过期，测试计划 v2 L0 防 flaky）
_remotion_probe_cache: tuple[list[str], float] | None = None


def _default_worker_url() -> str:
    """解析探测地址：显式环境变量优先，否则用 worker 服务名（非 localhost）。"""
    return os.environ.get("WORKER_HEALTH_URL") or _DEFAULT_WORKER_HEALTH_URL


def _default_remotion_worker_url() -> str:
    return os.environ.get("REMOTION_WORKER_HEALTH_URL") or _DEFAULT_REMOTION_WORKER_HEALTH_URL


async def probe_remotion_worker_capabilities(worker_url: str | None = None) -> list[str]:
    """T13 双池化：探测 remotion-worker 池能力（独立缓存，与 ffmpeg 池互不污染）。

    返回 capabilities；探测失败（容器停/网络不通）返回 []——调用方据此做 D16
    提交前快速失败（明确中文报错），不收任务悬死。
    """
    global _remotion_probe_cache
    if _remotion_probe_cache and (time.monotonic() - _remotion_probe_cache[1]) < _CACHE_TTL:
        return _remotion_probe_cache[0]
    url = worker_url or _default_remotion_worker_url()
    caps: list[str] = []
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            if resp.status_code == 200:
                body = resp.json()
                if body.get("worker_pool") == "remotion" or "remotion_course" in body.get("capabilities", []):
                    caps = body.get("capabilities", [])
                else:
                    logger.warning("remotion worker probe: pool mismatch (worker_pool=%s)", body.get("worker_pool"))
    except Exception:
        logger.warning("Remotion worker probe failed at %s — pool considered down", url)
    _remotion_probe_cache = (caps, time.monotonic())
    return caps


async def ensure_remotion_pool_available() -> None:
    """D16：remotion 池下线时 gateway 提交前快速失败（明确报错文案，不收任务悬死）。

    探测失败/池不匹配 → raise ValueError（中文），create 端点转 503 语义由调用方
    （routers/video_gen.py create 分支）处理。
    """
    caps = await probe_remotion_worker_capabilities()
    if "remotion_course" not in caps:
        raise ValueError("课程视频渲染服务（remotion-worker）当前不可用，请稍后重试或联系管理员")


async def probe_worker_capabilities(worker_url: str | None = None) -> list[str]:
    """GET worker /health, return capabilities. Caches 60s. Fallback to ['v1']."""
    global _probe_cache
    if _probe_cache and (time.monotonic() - _probe_cache[1]) < _CACHE_TTL:
        return _probe_cache[0]

    url = worker_url or _default_worker_url()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/health")
            if resp.status_code == 200:
                caps = resp.json().get("capabilities", ["v1"])
                _probe_cache = (caps, time.monotonic())
                return caps
            logger.warning("Worker probe at %s returned HTTP %s, assuming v1 only", url, resp.status_code)
    except Exception:
        logger.warning("Worker probe failed at %s, assuming v1 only", url)
    caps = ["v1"]
    _probe_cache = (caps, time.monotonic())
    return caps


def should_use_mixed_pipeline(storyboard: Storyboard, caps: list[str]) -> bool:
    return "v2_mixed" in caps and len({getattr(f, "visual_source", None) or storyboard.config.video_type for f in storyboard.frames}) > 1


def should_use_segmented_pipeline(storyboard: Storyboard, caps: list[str]) -> bool:
    """v3 分段管线分派。T6（2026-08-18 两反馈批）：planner 生产分组器产出的 groups
    （origin="planner"）**不**转活 v3——两步纪律的 worker 侧对应物：

    - v3 长期生产不可达（compose 探测修前恒 v1），转活自带隐患，须独立验收
      （同 _plan_course_batched 的第二步纪律）；
    - T-S2 上线后要跑 A/B 对照再定默认去留（拍板③折中），worker 管线若同时
      翻转，A/B 归因就被混杂。

    course 系 groups（origin=None，course_pipeline 段落分组）行为不变。"""
    if "v3_segmented" not in caps or len(storyboard.scene_groups) <= 1:
        return False
    return not all(getattr(g, "origin", None) == "planner" for g in storyboard.scene_groups)


def decide_pipeline_version(storyboard: Storyboard, caps: list[str]) -> str:
    if should_use_segmented_pipeline(storyboard, caps):
        return "v3_segmented"
    if should_use_mixed_pipeline(storyboard, caps):
        return "v2_mixed"
    return "v1"
