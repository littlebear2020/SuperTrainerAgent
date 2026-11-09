"""时长栅格对齐与缺口计算（2026-08-03 Task 2）。

背景：ai_video 的 provider 时长是**请求值不是保证值**，且 Ark seedance 只接受
[4,15] 的整数秒（``providers/seedance_ark.py``）。修前 ``ai_video.py`` 直接把
TTS 实测浮点时长（如 8.9s）下发，Ark 端 ``int(round(8.9))`` → 9s，尚可；但 2.5s 会被
钳到 4s、20s 被钳到 15s，而 ``VideoRef.duration`` 仍回显未钳的原值——clamp 与 echo
不一致，**不需要模型不听话就已经漂移**。

本模块把栅格对齐前移到调用方，使「请求了几秒」与「可能拿到几秒」在 gateway 侧一致。

方向选择：**向上取整**。请求长于旁白 → worker ``-t`` 截断（stream copy，无损）；
请求短于旁白 → worker ``tpad`` 定格（观感突兀）。两种误差不对称，故偏向前者。
"""

from __future__ import annotations

import math

_DEFAULT_TOLERANCE_SEC = 0.05


def align_to_provider_grid(
    desired: float,
    *,
    cap: float,
    floor: float = 0.0,
    integer_seconds: bool = True,
    safety_factor: float = 1.0,
) -> float:
    """把期望时长对齐到 provider 真实可达的栅格。

    Args:
        desired: 期望时长（通常是 TTS 实测旁白时长）。
        cap: provider 上限（``max_video_duration``；非 Ark provider 无此属性 → 调用方传 8.0）。
        floor: provider 下限（Ark = 4.0；无下限的 provider 传 0.0）。
        integer_seconds: provider 是否只接受整数秒。
        safety_factor: E9 超发裁剪保险系数（2026-08-25 补齐，默认 1.0 = 不放大）。
            期望时长先乘系数再走 ceil/floor/cap——模型欠交付（产物 < 旁白）时
            worker 只能 tpad 定格，×1.25 让请求恒 ≥ 旁白，合成端永远走
            ``normalize_clip_resolution(max_duration=帧时长)`` 的无损裁剪。
            封顶仍由 min(cap) 保证：旁白 > 单次上限的帧走续写而非超发。

    Returns:
        对齐后的请求时长。保证 ``floor <= 返回值 <= cap``（floor > cap 时 cap 优先）。
    """
    value = max(desired, 0.0) * safety_factor
    if integer_seconds:
        value = float(math.ceil(value))
    if floor > 0:
        value = max(value, floor)
    return min(value, cap)


def duration_shortfall(target: float, actual: float | None, *, tolerance: float = _DEFAULT_TOLERANCE_SEC) -> float:
    """需要定格补偿的秒数；无缺口或无法判定时返回 0.0。

    ``actual`` 为 None/<=0 表示未探测或探测失败——此时返回 0.0 而**不是**保守告警。
    理由：worker 侧对探不到时长的片子已按「未知」保守定格（N9），gateway 再报一次
    只会产生用户不可行动的噪音。
    """
    if actual is None or actual <= 0:
        return 0.0
    gap = target - actual
    return gap if gap > tolerance else 0.0
