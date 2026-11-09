"""生图/生视频兜底单价常量（CNY per call）--预算闸/成本指标的最后兜底。

**定位声明（C7，2026-08-27 成本可信化批次 v4.2 A13 联动）**：这两个常量是
``media_pricing`` 表读不到（缓存空 / DB 故障）或单位不可预扣（per_kilo_token
无法在调用前折算金额，provider_bridge docstring 同口径）时，预算闸
（visual_factory / provider_bridge）与成本指标（record_image_cost）的
**最后兜底**，与展示价目（console / media_pricing_resolver）解耦是**有意行为**，
不与价目表手工同步。

- A13 用户拍板（2026-08-27）：金额闸默认关闭（ai_visual_budget_cny 代码默认
  None，次数闸 600/240 为唯一硬顶），常量仅余「运营显式启用金额闸时的预扣」
  与「成本指标兜底」两个观测用途；
- 展示侧估算一律走 ``_shared/media_pricing_resolver``（模型级->provider 级），
  与本常量无关。此前 visual_factory.py / provider_bridge.py 各写一份 0.3 靠
  注释提醒手工同步的旧注释已废--这里不是价目表。
"""

import logging

logger = logging.getLogger(__name__)

IMAGE_UNIT_COST_CNY = 0.3
VIDEO_UNIT_COST_CNY = 1.5


def record_image_cost(caller: str, n: int = 1, *, provider: str | None = None) -> None:
    """记一次生图成本（仅可观测，不拦截）。

    caller ∈ {"video_gen", "writer", "gallery"}--throttle 的
    video_gen_provider_calls_total 无此区分，三方混计无法归因。

    C3收口：读 media_pricing 缓存的 per_image 单价；缓存为空时回退常量。
    provider 参数精确取价（与 visual_factory 对齐），未传时回退常量。
    """
    from app.gateway._shared.task_framework.metrics import metrics

    unit_cost = IMAGE_UNIT_COST_CNY
    if provider:
        try:
            from app.gateway.routers.console import _load_media_pricing_sync

            pricing = _load_media_pricing_sync()
            entry = pricing.get((provider, "image"))
            if entry and entry.get("unit") == "per_image":
                unit_cost = entry["unit_price"]
        except Exception:
            # B5（2026-10-07 反馈核实批）：静默吞错会让成本指标无声低估——记
            # warning 提示排查。触发面（2026-10-08 复核勘正）：
            # _load_media_pricing_sync 是纯同步缓存读（缓存空返回 {}、DB 失败
            # 由 resolver 内部 logger.debug 吞掉），本告警仅在读取函数真抛异常
            # （如 console 模块 import 失败）时触发；缓存空走 entry=None 静默
            # 兜底是 A13 拍板的有意行为（见模块 docstring 的定位声明）。
            logger.warning(
                "media_pricing 读取失败（provider=%s），生图成本按兜底常量 %.2f CNY/次计——成本指标将低估",
                provider,
                IMAGE_UNIT_COST_CNY,
                exc_info=True,
            )

    metrics.inc(
        "image_generation_cost_cny_total",
        unit_cost * n,
        labels={"caller": caller},
        help_text="Cumulative AI image generation cost in CNY by caller",
    )
