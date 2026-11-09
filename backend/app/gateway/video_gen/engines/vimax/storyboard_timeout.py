"""ViMax 分镜单次尝试超时（2026-10-20 已拍板：2 次 × 420 秒）。

环境变量 ``VIMAX_STORYBOARD_TIMEOUT_S``：默认 420；非数值/≤0 回落 420；
正数 clamp 到 [60, 900]。不新增 config schema / config_version——部署端可只
改环境变量。
"""

from __future__ import annotations

import math
import os
from collections.abc import Mapping

DEFAULT_STORYBOARD_TIMEOUT_S = 420
MIN_STORYBOARD_TIMEOUT_S = 60
MAX_STORYBOARD_TIMEOUT_S = 900


def resolve_storyboard_timeout_s(env: Mapping[str, str] | None = None) -> int:
    """返回合法分镜 deadline 秒数（默认 420，非法回落 420，clamp 60..900）。"""
    source = os.environ if env is None else env
    raw = source.get("VIMAX_STORYBOARD_TIMEOUT_S")
    if raw is None or str(raw).strip() == "":
        return DEFAULT_STORYBOARD_TIMEOUT_S
    try:
        value = float(str(raw).strip())
    except ValueError:
        return DEFAULT_STORYBOARD_TIMEOUT_S
    if not math.isfinite(value) or value <= 0:
        return DEFAULT_STORYBOARD_TIMEOUT_S
    return int(min(MAX_STORYBOARD_TIMEOUT_S, max(MIN_STORYBOARD_TIMEOUT_S, value)))
