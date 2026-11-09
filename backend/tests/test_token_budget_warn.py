"""Token budget warning tests (OBS-03 D3, 2026-09-23 批 2).

阈值判定口径 = thread 累计 tokens（与 UI 徽章一致）。只告警不阻断。
"""

from __future__ import annotations

from deerflow.runtime.runs.budget_warn import (
    resolve_warn_threshold,
    should_warn_token_budget,
    thread_total_tokens,
)

_THRESHOLDS = {
    "default": 12_000_000,
    "ppt-master": 12_000_000,
    "course-video": 12_000_000,
}


class TestThresholdJudgment:
    """计划验收：8M 不告警 / 12M 越线告警。"""

    def test_8m_does_not_warn(self):
        assert should_warn_token_budget(8_000_000, "ppt-master", _THRESHOLDS) is False

    def test_12m_and_above_warns(self):
        assert should_warn_token_budget(12_000_001, "ppt-master", _THRESHOLDS) is True
        assert should_warn_token_budget(15_000_000, "ppt-master", _THRESHOLDS) is True

    def test_task_type_exact_match_beats_default(self):
        thresholds = {"default": 1_000_000, "ppt-master": 12_000_000}
        assert should_warn_token_budget(2_000_000, "ppt-master", thresholds) is False
        assert should_warn_token_budget(2_000_000, "course-video", thresholds) is True

    def test_unknown_task_falls_back_to_default(self):
        assert resolve_warn_threshold("some-other-agent", _THRESHOLDS) == 12_000_000
        assert resolve_warn_threshold(None, _THRESHOLDS) == 12_000_000

    def test_no_default_and_no_match_means_no_warning(self):
        thresholds = {"ppt-master": 5_000_000}
        assert resolve_warn_threshold("course-video", thresholds) is None
        assert should_warn_token_budget(99_000_000, "course-video", thresholds) is False

    def test_non_positive_or_invalid_values_disable(self):
        thresholds = {"default": 0, "ppt-master": -5, "course-video": "12M"}
        assert resolve_warn_threshold("ppt-master", thresholds) is None
        assert resolve_warn_threshold("default", thresholds) is None
        assert resolve_warn_threshold("course-video", {"course-video": True}) is None

    def test_none_inputs_never_warn(self):
        assert should_warn_token_budget(None, "ppt-master", _THRESHOLDS) is False
        assert should_warn_token_budget(8_000_000, "ppt-master", None) is False
        assert should_warn_token_budget(0, "ppt-master", _THRESHOLDS) is False


class TestThreadTotal:
    def test_sums_from_aggregate_shape(self):
        aggregate = {
            "total_tokens": 12_345_678,
            "total_input_tokens": 10_000_000,
            "by_model": {"glm": {"tokens": 12_345_678, "runs": 30}},
        }
        assert thread_total_tokens(aggregate) == 12_345_678

    def test_tolerates_missing_or_malformed(self):
        assert thread_total_tokens(None) == 0
        assert thread_total_tokens({}) == 0
        assert thread_total_tokens({"total_tokens": None}) == 0
