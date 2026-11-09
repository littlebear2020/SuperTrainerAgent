"""ViMax 只读费用/成片时长预估（2026-10-20 Task 6，已拍板）。

口径：
- 端点先解析预算：有预算时 ``n_shots = min(len(frames), budget)``（frames 为空用
  budget），无预算才 ``max(len(frames), ceil(total_duration / 8))``；``video_seconds
  = n_shots * 8``（seedance 默认 8s/镜）；
- ``images = n_shots + 3 * n_characters``（首帧 + 角色三视图）；转场调用无可靠
  秒数/价目，恒进 ``unpriced`` 并标 ``partial``（history all-in 除外）；
- 视频价目 ``per_second`` 直算；``per_kilo_token`` 优先近 10 个成功 vimax 任务
  每镜中位数，无历史则 ``point_cny=None`` 并标 ``partial``；
- 只给区间（正常 ±25%；partial/样本不足 ±40%），禁止伪造精确金额；
- 纯函数不查 DB、不调 provider；扣费/预算闸完全不消费本模块。
"""

from __future__ import annotations

from collections.abc import Mapping

from .schemas import VideoCostEstimate

_SECONDS_PER_SHOT = 8


def _unit_price(entry: Mapping | None) -> tuple[str | None, float | None]:
    if not isinstance(entry, Mapping):
        return None, None
    unit = entry.get("unit")
    raw = entry.get("unit_price")
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return (str(unit) if unit is not None else None), None
    return (str(unit) if unit is not None else None), float(raw)


def estimate_vimax_cost(
    *,
    n_shots: int,
    video_seconds: float,
    n_characters: int,
    tts_chars: int,
    prices: Mapping[str, dict | None],
    history_per_shot: float | None = None,
    history_sample_count: int | None = None,
) -> VideoCostEstimate:
    """纯函数：价目由调用方注入（本函数不查 DB / 不触发 provider）。"""
    n_shots = max(0, int(n_shots))
    video_seconds = max(0.0, float(video_seconds))
    n_characters = max(0, int(n_characters))
    tts_chars = max(0, int(tts_chars))

    breakdown: dict[str, float | None] = {"image": None, "video": None, "tts": None}
    unpriced: list[str] = []
    partial = False
    history_all_in = False

    # ── 图片：首帧 + 角色三视图 ────────────────────────────────────────────
    images = n_shots + 3 * n_characters
    image_unit, image_price = _unit_price(prices.get("image"))
    if image_unit == "per_image" and image_price is not None:
        breakdown["image"] = round(images * image_price, 4)
    elif image_unit == "per_kilo_token":
        # token 计费无法从镜数推导精确费用——不伪造金额。
        unpriced.append("image")
        partial = True
    else:
        unpriced.append("image")
        partial = True

    # ── 视频 ──────────────────────────────────────────────────────────────
    video_unit, video_price = _unit_price(prices.get("video"))
    basis_video: str | None = None
    if video_unit == "per_second" and video_price is not None:
        breakdown["video"] = round(video_seconds * video_price, 4)
        basis_video = "price_table"
    elif video_unit == "per_kilo_token":
        if history_per_shot is not None and history_per_shot > 0:
            breakdown["video"] = round(n_shots * float(history_per_shot), 4)
            basis_video = "history"
            # 历史口径 = 整任务总成本 / 镜数（含图片/首帧/三视图/TTS），不能再与
            # 显式 image/tts 分项相加，否则重复计费、区间系统性偏高。
            history_all_in = True
        else:
            unpriced.append("video")
            partial = True
    else:
        unpriced.append("video")
        partial = True

    # ── TTS：无价目视为免费/未计价（Edge 默认），不计入 unpriced 噪音 ────
    tts_unit, tts_price = _unit_price(prices.get("tts"))
    if tts_unit == "per_kilo_char" and tts_price is not None:
        breakdown["tts"] = round(tts_chars / 1000.0 * tts_price, 4)
    elif tts_unit == "per_char" and tts_price is not None:
        breakdown["tts"] = round(tts_chars * tts_price, 4)
    elif tts_unit is not None and tts_unit != "free":
        unpriced.append("tts")
        partial = True
    else:
        breakdown["tts"] = 0.0

    if history_all_in:
        # 分项不可枚举为 0（会被误读为图片/配音免费）；history 已是整任务口径。
        breakdown["image"] = None
        breakdown["tts"] = None
        unpriced = [u for u in unpriced if u not in ("image", "tts")]
        partial = False

    if not history_all_in:
        # 转场视频调用当前无可靠每镜秒数/价目，不能装进精确分项——明确标未计。
        unpriced.append("transition")
        partial = True

    if breakdown["video"] is None:
        # 视频是主成本项——缺它时不给任何「金额」，只说明哪项无法计价。
        point = None
        low = None
        high = None
    else:
        point = round(sum(v for v in breakdown.values() if v is not None), 2)
        low_confidence_history = basis_video == "history" and (history_sample_count or 0) < 3
        spread = 0.4 if partial or low_confidence_history else 0.25
        low = round(point * (1 - spread), 2)
        high = round(point * (1 + spread), 2)

    if partial:
        basis: str = "partial"
    elif basis_video == "history":
        basis = "history"
    else:
        basis = "price_table"

    return VideoCostEstimate(
        currency="CNY",
        low_cny=low,
        high_cny=high,
        point_cny=point,
        basis=basis,
        n_shots=n_shots,
        video_seconds=video_seconds,
        n_characters=n_characters,
        breakdown=breakdown,
        unpriced=unpriced,
        spent_cny=None,
    )


__all__ = ["estimate_vimax_cost", "VideoCostEstimate", "_SECONDS_PER_SHOT"]
