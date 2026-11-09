"""Task 23：降级 sidecar 必须经详情接口回传给用户。

C11/C13/C6 三处降级（BGM 混音失败静默丢背景音、字幕烧录失败静默出无字幕片、
转场降级为硬切）此前都只写 worker 日志——用户拿到的成片"看着像成了"，
少了自己勾选的东西却零提示。

Task 23 让三处统一写 ``degradations.json``；本测试锁「详情接口读得到并转成
中文告警」这一条不变量。沿用 Task 10 ``placeholder_warning`` 的通道形状：
非终态不读、文件缺失不报、空列表不报。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.gateway.routers.video_gen import _attach_degradation_warning
from app.gateway.video_gen.degradations import record_degradation


def _write(task_dir: Path, records: list[dict]) -> None:
    """用**真实 writer** 落盘，而不是手写 JSON。

    原实现手写 {"records": [...]}，与 writer 实际的 {"items": [...]} 不符——
    于是「reader 读错键」这个真 bug 被测试固化成了「正确行为」，两侧各自绿、
    合起来整条通道一条都报不出来。手写 fixture 复刻协议 = 把协议漂移测掉了。
    """
    for rec in records:
        record_degradation(
            task_dir,
            kind=rec["kind"],
            detail=rec.get("detail", ""),
            frame_index=rec.get("frame_index"),
        )


def test_bgm_failure_surfaces_chinese_warning(tmp_path: Path) -> None:
    _write(tmp_path, [{"kind": "bgm_failed", "detail": "ffmpeg amix exit 1"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result.get("degradation_warning")
    assert warning, "BGM 混音失败必须有告警"
    assert "背景音乐" in warning, f"文案应点明背景音乐，实际：{warning}"


def test_subtitle_failure_surfaces_warning(tmp_path: Path) -> None:
    _write(tmp_path, [{"kind": "subtitle_burn_failed", "detail": "libass"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    assert "字幕" in (result.get("degradation_warning") or "")


def test_transition_downgrade_surfaces_warning(tmp_path: Path) -> None:
    _write(tmp_path, [{"kind": "transition_downgraded", "detail": "xfade unavailable"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    assert "转场" in (result.get("degradation_warning") or "")


def test_multiple_degradations_all_reported(tmp_path: Path) -> None:
    """多处降级同时发生 → 一条告警里都要出现，不能只报第一条。"""
    _write(
        tmp_path,
        [
            {"kind": "bgm_failed", "detail": "x"},
            {"kind": "subtitle_burn_failed", "detail": "y"},
        ],
    )
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "背景音乐" in warning and "字幕" in warning, f"两处降级都要报，实际：{warning}"


@pytest.mark.parametrize("status", ["queued", "processing", "composing"])
def test_non_terminal_status_does_not_read(tmp_path: Path, status: str) -> None:
    """非终态不读——详情页 2 秒轮询，中途读到的是半成品状态。"""
    _write(tmp_path, [{"kind": "bgm_failed", "detail": "x"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, status)

    assert "degradation_warning" not in result


def test_missing_sidecar_is_silent(tmp_path: Path) -> None:
    result: dict = {}
    _attach_degradation_warning(result, tmp_path, "ready")
    assert "degradation_warning" not in result


def test_empty_records_is_silent(tmp_path: Path) -> None:
    """无降级 → 不能凭空造告警。"""
    _write(tmp_path, [])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    assert "degradation_warning" not in result


def test_unknown_kind_still_reported(tmp_path: Path) -> None:
    """未知 kind 不能吞掉——宁可报个泛化文案，也别静默。"""
    _write(tmp_path, [{"kind": "some_future_kind", "detail": "z"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    assert result.get("degradation_warning"), "未知 kind 也要有兜底告警"


# ---------------------------------------------------------------------------
# T2（2026-09-01 降级提示分级）：指引尾巴按 kind 分桶。
#
# 修前 :124 无条件拼「如需完整效果请点重试合成」——recompose 只重新拼接盘面
# 素材不重新生成帧（service.recompose_task：no re-fetch/re-TTS），定格类问题
# （生成阶段产物）重试合成拿同样素材再拼一遍，指引是错的。
# ---------------------------------------------------------------------------


def test_pure_compose_bucket_tail_points_to_recompose(tmp_path: Path) -> None:
    """纯合成桶（bgm_failed）→ 尾巴指向「重试合成」。"""
    _write(tmp_path, [{"kind": "bgm_failed", "detail": "x"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "重试合成」修复" in warning, f"合成桶须指路重试合成，实际：{warning}"
    assert "重新生成」" not in warning, f"纯合成桶不应带帧级指引，实际：{warning}"


def test_pure_frame_regen_bucket_tail_points_to_regenerate(tmp_path: Path) -> None:
    """纯帧级桶（visual_shortfall）→ 尾巴指向对帧「重新生成」。"""
    _write(tmp_path, [{"kind": "visual_shortfall", "detail": "5.0s vs 6.3s", "frame_index": 2}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "重新生成」后重试合成才生效" in warning, f"帧级桶须指路重新生成，实际：{warning}"
    assert "以上合成问题可点" not in warning, f"纯帧级桶不应带合成指引，实际：{warning}"


def test_mixed_buckets_get_both_tails_in_order(tmp_path: Path) -> None:
    """混合桶 → 两条尾巴都拼（合成在前、帧级在后）。"""
    _write(
        tmp_path,
        [
            {"kind": "bgm_failed", "detail": "x"},
            {"kind": "visual_shortfall", "detail": "y", "frame_index": 0},
        ],
    )
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "以上合成问题可点「重试合成」修复" in warning
    assert "画面类问题需对相应帧点「重新生成」后重试合成才生效" in warning
    assert warning.index("重试合成」修复") < warning.index("画面类问题"), "合成尾巴在前（与桶声明顺序一致）"


def test_informative_only_bucket_has_no_tail(tmp_path: Path) -> None:
    """纯告知桶（两桶之外，如 video_extended）→ 有告警无指引尾巴。"""
    _write(tmp_path, [{"kind": "video_extended", "detail": "x"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert warning and "续写" in warning, f"告知内容仍在，实际：{warning}"
    assert not warning.endswith("「重试合成」修复") and "重新生成」后重试合成才生效" not in warning, f"纯告知不加指引尾巴，实际：{warning}"
    assert "可点" not in warning and "需对相应帧点" not in warning, f"纯告知桶零指引，实际：{warning}"


def test_difference_semantics_covers_unlisted_kinds_no_english_leak(tmp_path: Path) -> None:
    """差集语义：labels 补全后的 6 个 kind（如 graphic_spec_rejected）落纯告知桶，
    断言中文文案且无英文 kind 泄漏——修前走兜底文案会把英文 kind 直接给终端用户。"""
    _write(tmp_path, [{"kind": "graphic_spec_rejected", "detail": "x", "frame_index": 3}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "图文卡" in warning, f"补全后的中文 label 应生效，实际：{warning}"
    assert "graphic_spec_rejected" not in warning, f"英文 kind 不得泄漏给终端用户，实际：{warning}"
    assert "可点" not in warning and "需对相应帧点" not in warning, f"纯告知桶零指引尾巴，实际：{warning}"
    assert "帧 4：" in warning, "帧号前缀逻辑不动"


def test_graphic_field_omitted_labeled_no_english_leak_no_tail(tmp_path: Path) -> None:
    """2026-09-02 标题截断修复：graphic_spec_field_omitted（字段级省略，卡片照出）
    ——须有中文 label（不泄漏英文 kind）、落纯告知桶（重试/重生成都不对症）。"""
    _write(tmp_path, [{"kind": "graphic_spec_field_omitted", "detail": "标题超长（17>14），已省略", "frame_index": 5}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "未显示" in warning, f"中文 label 须生效，实际：{warning}"
    assert "graphic_spec_field_omitted" not in warning, f"英文 kind 不得泄漏给终端用户，实际：{warning}"
    assert "可点" not in warning and "需对相应帧点" not in warning, f"纯告知桶零指引尾巴，实际：{warning}"
    assert "帧 6：" in warning, "帧号前缀逻辑不动"


def test_future_kind_falls_into_informative_bucket_by_difference(tmp_path: Path) -> None:
    """差集兜底未来新增 kind：既不在两桶也不在 labels → 兜底文案 + 无指引尾巴
    （不因新增 kind 误指路「重试合成」）。"""
    _write(tmp_path, [{"kind": "brand_new_kind_2027", "detail": "x"}])
    result: dict = {}

    _attach_degradation_warning(result, tmp_path, "ready")

    warning = result["degradation_warning"]
    assert "出现降级处理" in warning
    assert "可点" not in warning and "需对相应帧点" not in warning, f"未知 kind 不指路，实际：{warning}"
