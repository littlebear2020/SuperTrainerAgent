"""B5（2026-10-07 反馈核实批）：媒体定价读取失败不得静默吞错。

``record_image_cost`` 在 media_pricing 读取失败时 ``except Exception: pass``
静默回退常量——成本指标无声低估且无任何告警。改为 logger.warning（含失败
原因与低估提示）；指标兜底行为本身不变。
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from app.gateway.video_gen.pricing import IMAGE_UNIT_COST_CNY, record_image_cost


def test_record_image_cost_warns_when_pricing_load_fails(caplog) -> None:
    with patch(
        "app.gateway.routers.console._load_media_pricing_sync",
        side_effect=RuntimeError("db down"),
    ):
        with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.pricing"):
            record_image_cost("video_gen", 2, provider="doubao")

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings, "定价读取失败必须留下 warning 日志"
    assert any("成本指标将低估" in r.getMessage() for r in warnings)


def test_record_image_cost_falls_back_to_constant_on_failure() -> None:
    """兜底行为不变：读取失败仍按常量计价、指标照记。"""
    fake_metrics = MagicMock()
    with (
        patch("app.gateway.routers.console._load_media_pricing_sync", side_effect=RuntimeError("db down")),
        patch("app.gateway._shared.task_framework.metrics.metrics", fake_metrics),
    ):
        record_image_cost("video_gen", 2, provider="doubao")

    fake_metrics.inc.assert_called_once()
    call = fake_metrics.inc.call_args
    # inc(name, value, labels=..., help_text=...)：金额是第二个位置参数
    amount = call.args[1] if len(call.args) > 1 else call.kwargs.get("value")
    assert amount == IMAGE_UNIT_COST_CNY * 2
    assert call.kwargs.get("labels") == {"caller": "video_gen"}
