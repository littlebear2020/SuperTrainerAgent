"""审查偏离项 3（2026-08-19，两反馈批）：H3/happyhorse 多图截断打标。

T3 声明 supports_image_urls 后，H3（QihuVideoProvider 按 _is_h3_model 门控）与
happyhorse 只消费首图（单首帧口径），其余参考图（角色三视图 front/side/back）
修前是**静默丢弃**——对照 registry 降级分支（不支持多图的 provider 只传首图时）
有 ``degraded_without_refs`` 打标 + warning 日志 + sidecar 字段
（visual_coverage.json 的 character_ref_degraded），同一性质的降级两种标准。

本文件锁三件事：
1. 截断发生 → ``provider.multi_image_truncated`` 置 True；
2. 单图 / seedance（r2v 消费全部 refs）不置标；
3. 打标必须穿透生产包裹链（Throttle→Fallback，BUG-B 同款教训：属性无透传
   property 时 getattr 恒取兜底值），且 ``reset_multi_image_truncated()``
   能经包裹链清零（provider 单例跨任务，sidecar 防上一任务污染）。

sidecar 字段（character_ref_truncated）的写出由 test_ai_video_coverage_status.py
覆盖；这里只锁 provider 侧的打标与透传。
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider
from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


class _CapturingClient:
    """记录 POST body 的假 httpx client（submit 后立刻让 poll 拿到 url）。"""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.is_closed = False

    async def post(self, url: str, **kw: Any) -> MagicMock:
        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"data": {"id": "task_1"}})

    async def get(self, url: str, **kw: Any) -> MagicMock:
        return MagicMock(
            status_code=200,
            json=lambda: {"data": {"status": "succeeded", "url": "http://v/1.mp4"}},
        )


_THREE_REFS = ["http://img/front.png", "http://img/side.png", "http://img/back.png"]


def _h3() -> QihuVideoProvider:
    p = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)
    p._client = _CapturingClient()  # type: ignore[assignment]
    return p


def _happyhorse() -> HappyhorseVideoProvider:
    p = HappyhorseVideoProvider(api_key="k", base_url="http://b", poll_interval=0)
    p._client = _CapturingClient()  # type: ignore[assignment]
    return p


def _seedance360() -> SeedanceVideoProvider:
    p = SeedanceVideoProvider(api_key="k", base_url="http://b", poll_interval=0)
    p._client = _CapturingClient()  # type: ignore[assignment]
    return p


def test_h3_multi_image_truncation_flagged() -> None:
    """H3 拿 3 张参考图 → 只取首图且 multi_image_truncated=True（修前静默丢 2 张）。"""
    p = _h3()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert p.multi_image_truncated is True


def test_h3_submit_video_truncation_flagged() -> None:
    """submit 分离路径（Throttle 重试复用）同样截断同样置标——只标 generate 会漏。"""
    p = _h3()
    asyncio.run(p.submit_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert p.multi_image_truncated is True


def test_h3_single_image_not_flagged() -> None:
    """单图本就是 H3 首帧口径，不构成截断，不置标。"""
    p = _h3()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=["http://img/one.png"]))
    assert p.multi_image_truncated is False


def test_happyhorse_multi_image_truncation_flagged() -> None:
    """happyhorse generate/submit 两路径取 [0]，同样置标（同 registry 标准）。"""
    p = _happyhorse()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert p.multi_image_truncated is True

    p2 = _happyhorse()
    asyncio.run(p2.submit_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert p2.multi_image_truncated is True


def test_happyhorse_single_image_not_flagged() -> None:
    p = _happyhorse()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=["http://img/one.png"]))
    assert p.multi_image_truncated is False


def test_seedance_consumes_all_refs_not_flagged() -> None:
    """seedance r2v 消费全部 refs（无截断），不得置标——打标只属于真截断的 provider。"""
    p = _seedance360()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert p.multi_image_truncated is False


def test_flag_survives_production_wrapper_chain() -> None:
    """截断打标必须穿透 Fallback(Throttle(...)) 生产包裹链（BUG-B 同款教训）。"""
    inner = _h3()
    chain = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(inner, qps=0.5, max_retries=5),
        ThrottledAIVideoProvider(_h3(), qps=0.5, max_retries=5),
    )
    assert chain.multi_image_truncated is False, "未截断时应为 False"
    asyncio.run(inner.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert inner.multi_image_truncated is True
    assert chain.multi_image_truncated is True, "修前 Throttle/Fallback 无透传 → 恒 False"


def test_reset_forwards_through_wrapper_chain() -> None:
    """reset_multi_image_truncated 经包裹链清零（provider 单例跨任务防污染）。"""
    inner = _h3()
    chain = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(inner, qps=0.5, max_retries=5),
        ThrottledAIVideoProvider(_h3(), qps=0.5, max_retries=5),
    )
    asyncio.run(inner.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS))
    assert chain.multi_image_truncated is True
    chain.reset_multi_image_truncated()
    assert chain.multi_image_truncated is False
    assert inner.multi_image_truncated is False


# ── 2026-09-04 H3 r2v：role="reference" 多图全量下发，不构成截断 ──


def test_h3_r2v_multi_image_not_flagged(monkeypatch) -> None:
    """r2v 真实消费全部参考图（无截断），不得置标——打标只属于真截断。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    p = _h3()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS, role="reference"))
    assert p.multi_image_truncated is False


def test_h3_r2v_submit_multi_image_not_flagged(monkeypatch) -> None:
    """submit 分离路径同口径（Throttle 重试复用，两条通道必须同行为）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    p = _h3()
    asyncio.run(p.submit_video("一只猫", duration=5.0, image_urls=_THREE_REFS, role="reference"))
    assert p.multi_image_truncated is False


def test_h3_r2v_flag_off_multi_image_still_flagged(monkeypatch) -> None:
    """开关关闭回退旧行为：role="reference" 多图仍取首图 + 置标。"""
    monkeypatch.setenv("VIDEO_GEN_H3_R2V", "0")
    p = _h3()
    asyncio.run(p.generate_video("一只猫", duration=5.0, image_urls=_THREE_REFS, role="reference"))
    assert p.multi_image_truncated is True
