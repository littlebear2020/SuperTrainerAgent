"""视觉源与预算工厂（2026-08-03 Task 16 从 service.py 抽出）。

这四个函数原为 ``VideoGenService`` 方法，但只用到 ``self._providers`` /
``self._config`` 两项实例状态——改成显式形参即脱离实例。service 侧保留薄委托方法，
把「实例状态注入」这一件事收在一处，调用点零改动。

纯搬迁，零行为变化——docstring 原样保留。守护测试见
tests/test_visual_factory_extraction.py。
"""

from __future__ import annotations

from typing import Any

from .pricing import IMAGE_UNIT_COST_CNY, VIDEO_UNIT_COST_CNY
from .storyboard import Storyboard


def _resolve_unit_cost(provider: str, media_type: str, fallback: float) -> float:
    """Read media unit cost for a specific provider from the pricing cache.

    C3收口：visual_factory 的预算闸也读 media_pricing 缓存，与 ViMax bridge
    的 ``_resolve_image/video_unit_cost`` 保持一致。缓存为空（启动前）或 DB
    故障时回退硬编码常量，不阻断生成。
    """
    try:
        from app.gateway.routers.console import _load_media_pricing_sync

        pricing = _load_media_pricing_sync()
        entry = pricing.get((provider, media_type))
        if entry and entry.get("unit") in ("per_image", "per_second"):
            return entry["unit_price"]
    except Exception:
        pass
    return fallback


def has_mixed_sources(storyboard: Storyboard) -> bool:
    """Check if storyboard frames use more than one visual source type."""
    default_type = storyboard.config.video_type
    sources = {getattr(f, "visual_source", None) or default_type for f in storyboard.frames}
    return len(sources) > 1


def make_visual_budget(kind: str, *, config):  # noqa: ANN001,ANN201
    """构造单任务成本闸（Task 13 / E1+E6）。kind = "image" | "video"。

    与 ViMax 的三重闸对称：ai_image/ai_video 修前**零拦截**，schemas 允许
    n_scenes ≤ 200 × video_count ≤ 5 = 单次请求最多 1000 次付费生成。
    单价语义（C7 定位声明 + A13，2026-08-27）：查价走 media_pricing 缓存的
    provider 级行（per_image/per_second 可预扣才认），读不到或单位不可预扣时
    回落 pricing.py 兜底常量（与展示价目解耦是有意行为，见该文件 docstring）；
    金额闸默认关闭（ai_visual_budget_cny 默认 None），次数闸为唯一硬顶。
    """
    from .visual_sources.budget import VisualBudget

    cfg = config
    if kind == "video":
        return VisualBudget(
            max_calls=getattr(cfg, "ai_video_max_gen", None),
            max_cost_cny=getattr(cfg, "ai_visual_budget_cny", None),
            unit_cost_cny=_resolve_unit_cost(getattr(cfg, "ai_video_provider", ""), "video", VIDEO_UNIT_COST_CNY),
        )
    return VisualBudget(
        max_calls=getattr(cfg, "ai_image_max_gen", None),
        max_cost_cny=getattr(cfg, "ai_visual_budget_cny", None),
        unit_cost_cny=_resolve_unit_cost(getattr(cfg, "ai_image_provider", ""), "image", IMAGE_UNIT_COST_CNY),
    )


def make_visual_source_mixed(
    storyboard: Storyboard,
    concurrency: int | None = None,
    *,
    providers,  # noqa: ANN001
    config,  # noqa: ANN001
    image_provider: str | None = None,
    video_provider: str | None = None,
):  # noqa: ANN201
    """Create a MixedVisualSource that routes each frame to its appropriate source.

    Remotion frames are skipped (template-driven, handled by Worker fallback).

    Task 6（2026-08-01）：同 :func:`make_visual_source` 接受任务级 provider 覆盖。
    混合任务（逐帧 visual_source 不同）若不接线，用户选的模型会被静默忽略 ——
    与「engine 死旋钮」是同一类缺陷，故一并处理。
    """
    from .visual_sources.ai_image import AIImageVisualSource
    from .visual_sources.ai_video import AIVideoVisualSource
    from .visual_sources.mixed import MixedVisualSource

    eff = concurrency if concurrency is not None else config.frame_concurrency
    default_type = storyboard.config.video_type
    needed = {getattr(f, "visual_source", None) or default_type for f in storyboard.frames}
    # graphic-native-text：graphic 帧归并 ai_image 源渲染——全 graphic 任务也须注册 ai_image
    if "graphic" in needed:
        needed.add("ai_image")

    sources: dict[str, Any] = {}
    if "ai_image" in needed:
        sources["ai_image"] = AIImageVisualSource(providers.make_ai_image(image_provider), concurrency=eff, global_limit=config.ai_api_global_concurrency, budget=make_visual_budget("image", config=config))
    if "ai_video" in needed:
        sources["ai_video"] = AIVideoVisualSource(
            providers.make_ai_video(video_provider),
            concurrency=eff,
            global_limit=config.ai_api_global_concurrency,
            budget=make_visual_budget("video", config=config),
            extend_enabled=config.video_extend_enabled,
            extend_min_gap=config.video_extend_min_gap,
            extend_max_segments=config.video_extend_max_segments,
            provider_name=video_provider or config.ai_video_provider,
            duration_safety_factor=config.duration_safety_factor,
        )
    # graphic-native-text（2026-08-17）：graphic 帧无独立渲染源——MixedVisualSource 分派时
    # 归并到 ai_image 源（文字由生图画进画面）；"graphic" 不再注册（旧注册的 Pillow 渲染器已删）。
    # remotion: NOT added — template-driven, handled by Worker drawtext fallback

    return MixedVisualSource(sources)


def make_visual_source(
    task_type: str,
    engine: str = "auto",
    concurrency: int | None = None,
    *,
    providers,  # noqa: ANN001
    config,  # noqa: ANN001
    image_provider: str | None = None,
    video_provider: str | None = None,
):  # noqa: ANN201
    """Create visual source. concurrency overrides config default.

    Task 6（2026-08-01）：新增 ``image_provider`` / ``video_provider`` 任务级覆盖。
    ``None`` → 用启动时按 config 构建的默认实例（含既有降级链），与改动前完全一致；
    具名 → 经 registry 按名构建，**不挂降级**（用户决策 4）。

    注：``engine`` 参数至今仍未被使用（历史死旋钮，保留仅为不破调用签名）。
    本次不复用它，见 schemas.CreateVideoRequest.image_provider 的说明。
    """
    if task_type == "ai_image":
        from .visual_sources.ai_image import AIImageVisualSource

        eff = concurrency if concurrency is not None else config.frame_concurrency
        provider = providers.make_ai_image(image_provider)
        return AIImageVisualSource(provider, concurrency=eff, global_limit=config.ai_api_global_concurrency, budget=make_visual_budget("image", config=config))
    if task_type == "ai_video":
        from .visual_sources.ai_video import AIVideoVisualSource

        eff = concurrency if concurrency is not None else config.frame_concurrency
        provider = providers.make_ai_video(video_provider)
        return AIVideoVisualSource(
            provider,
            concurrency=eff,
            global_limit=config.ai_api_global_concurrency,
            budget=make_visual_budget("video", config=config),
            extend_enabled=config.video_extend_enabled,
            extend_min_gap=config.video_extend_min_gap,
            extend_max_segments=config.video_extend_max_segments,
            provider_name=video_provider or config.ai_video_provider,
            duration_safety_factor=config.duration_safety_factor,
        )
    if task_type == "remotion_course":
        # remotion_course（2026-09-10 独立管线计划 P5b）：槽位过滤编排源——内部持有
        # ai_image/ai_video 两个既有源（provider 调用层/审核重试/预算计费复用），
        # 按 §3.4 条件过滤目标帧（图槽+显式 ai_image；仅用户显式 VideoClipPage+ai_video）。
        # 覆盖面：主管线 + _regenerate_frame（后者对 remotion_course 不做帧级 override，
        # 恒以本类型直调——2026-09-11 二轮审查 M1，勿改回帧源翻转）。
        from .visual_sources.ai_image import AIImageVisualSource
        from .visual_sources.ai_video import AIVideoVisualSource
        from .visual_sources.remotion_course_visuals import RemotionCourseVisualSource

        eff = concurrency if concurrency is not None else config.frame_concurrency
        return RemotionCourseVisualSource(
            image_source=AIImageVisualSource(providers.make_ai_image(image_provider), concurrency=eff, global_limit=config.ai_api_global_concurrency, budget=make_visual_budget("image", config=config)),
            video_source=AIVideoVisualSource(
                providers.make_ai_video(video_provider),
                concurrency=eff,
                global_limit=config.ai_api_global_concurrency,
                budget=make_visual_budget("video", config=config),
                extend_enabled=config.video_extend_enabled,
                extend_min_gap=config.video_extend_min_gap,
                extend_max_segments=config.video_extend_max_segments,
                provider_name=video_provider or config.ai_video_provider,
                duration_safety_factor=config.duration_safety_factor,
            ),
        )
    if task_type == "vimax":
        # vimax only exists in the multi_engine engine layer (VimaxEngine).
        # Reaching here means config.yaml video_gen.engine_arch is "legacy"
        # (the default), which is a misconfiguration — not an unimplemented
        # feature. Surface it explicitly so the fix (set engine_arch:
        # multi_engine) is obvious. Root cause of vg_7477487923ef.
        raise ValueError("vimax requires engine_arch='multi_engine' in config.yaml video_gen section (got the legacy path — this is a misconfiguration, not an unimplemented feature). Set engine_arch: multi_engine and restart.")
    raise ValueError(f"visual source for {task_type} not implemented in this phase")
