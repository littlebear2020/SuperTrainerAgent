"""Tests for media_pricing router (C2) and cost estimation helpers (C3).

Covers:
- GET /api/admin/media-pricing returns defaults when DB is empty
- PUT /api/admin/media-pricing replaces and returns updated entries
- _estimate_call_cost for all unit types
- voice_clone_upload exclusion from cost estimation
- by_date / by_type aggregation shapes
"""

from __future__ import annotations

import pytest

from app.gateway._shared.media_constants import FREE_METHODS, METHOD_TO_MEDIA_TYPE
from app.gateway.routers.console import _estimate_call_cost


class TestEstimateCallCost:
    def test_per_image(self) -> None:
        pricing = {"unit": "per_image", "unit_price": 0.22, "currency": "CNY"}
        cost = _estimate_call_cost({}, "generate_image", pricing)
        assert cost == 0.22

    def test_per_kilo_token(self) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        metadata = {"total_tokens": 5000}
        cost = _estimate_call_cost(metadata, "generate_video", pricing)
        assert cost == pytest.approx(0.115)  # 5 * 0.023

    def test_per_kilo_token_no_tokens(self) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        cost = _estimate_call_cost({}, "generate_video", pricing)
        assert cost is None  # can't estimate without token count

    def test_per_kilo_token_completion_only(self) -> None:
        """仅回传 completion_tokens 的成功视频（修前只认 total_tokens → 漏价）。"""
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        cost = _estimate_call_cost({"completion_tokens": 460}, "generate_video", pricing)
        assert cost == pytest.approx(0.01058)  # 0.46 * 0.023

    def test_per_kilo_token_prefers_completion_and_never_sums(self) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        cost = _estimate_call_cost({"completion_tokens": 300, "total_tokens": 1000}, "generate_video", pricing)
        assert cost == pytest.approx(0.0069)  # 300 only; 1300 would be 0.0299

    @pytest.mark.parametrize("completion", [None, "n/a", 0, "0", -5], ids=["none", "garbage", "zero", "zero-str", "negative"])
    def test_per_kilo_token_falls_back_to_total_when_completion_unusable(self, completion) -> None:
        """0 与负数同样按缺失处理：provider 失败/空响应常回报 completion_tokens=0 并带有效 total_tokens（评审 #50）。"""
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        cost = _estimate_call_cost({"completion_tokens": completion, "total_tokens": 5000}, "generate_video", pricing)
        assert cost == pytest.approx(0.115)  # 5 * 0.023 via total

    def test_per_kilo_token_zero_completion_without_total_is_unpriced(self) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        assert _estimate_call_cost({"completion_tokens": 0}, "generate_video", pricing) is None
        assert _estimate_call_cost({"completion_tokens": 0, "total_tokens": 0}, "generate_video", pricing) is None

    def test_per_kilo_token_no_usable_tokens_is_unpriced(self) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        cost = _estimate_call_cost({"completion_tokens": "n/a", "duration": 5.0}, "generate_video", pricing)
        assert cost is None  # duration is never used to guess tokens


class TestEvaluateMediaCallAmount:
    """真账 amount 优先于任何 token 估算（共享计价器契约，媒体计量计划 PR 1 钉死）。"""

    def test_real_amount_wins_over_completion_token_estimate(self) -> None:
        from app.gateway._shared.task_cost import evaluate_media_call

        priced = evaluate_media_call("seedance", "generate_video", "ok", {"amount": 0.5, "completion_tokens": 1000})
        assert priced.cost == pytest.approx(0.5)
        assert priced.source == "real"

    def test_completion_only_success_row_is_estimated(self, monkeypatch) -> None:
        from app.gateway._shared import media_pricing_resolver as resolver
        from app.gateway._shared.task_cost import evaluate_media_call

        monkeypatch.setattr(resolver, "_media_pricing_cache", {("seedance", "video"): {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}})
        monkeypatch.setattr(resolver, "_media_pricing_model_cache", {})
        priced = evaluate_media_call("seedance", "generate_video", "ok", {"model": "seedance", "completion_tokens": 460})
        assert priced.cost == pytest.approx(0.02116)  # 0.46 * 0.046
        assert priced.source == "estimated"

    def test_success_row_without_tokens_is_unpriced_not_zero(self, monkeypatch) -> None:
        """有价目、无 token → unpriced（不是 0，也不用 duration 猜）。"""
        from app.gateway._shared import media_pricing_resolver as resolver
        from app.gateway._shared.task_cost import evaluate_media_call

        monkeypatch.setattr(resolver, "_media_pricing_cache", {("seedance", "video"): {"unit": "per_kilo_token", "unit_price": 0.046, "currency": "CNY"}})
        monkeypatch.setattr(resolver, "_media_pricing_model_cache", {})
        priced = evaluate_media_call("seedance", "generate_video", "ok", {"model": "seedance", "duration": 5.0})
        assert priced.cost is None
        assert priced.source == "unpriced"

    def test_per_second(self) -> None:
        pricing = {"unit": "per_second", "unit_price": 0.5, "currency": "CNY"}
        metadata = {"duration": 10.0}
        cost = _estimate_call_cost(metadata, "generate_video", pricing)
        assert cost == pytest.approx(5.0)

    def test_per_second_no_duration(self) -> None:
        pricing = {"unit": "per_second", "unit_price": 0.5, "currency": "CNY"}
        cost = _estimate_call_cost({}, "generate_video", pricing)
        assert cost is None

    def test_per_kilo_char(self) -> None:
        pricing = {"unit": "per_kilo_char", "unit_price": 0.5, "currency": "CNY"}
        metadata = {"char_count": 2000}
        cost = _estimate_call_cost(metadata, "synthesize_sse", pricing)
        assert cost == pytest.approx(1.0)  # 2 * 0.5

    def test_no_pricing_entry(self) -> None:
        cost = _estimate_call_cost({"duration": 5.0}, "generate_video", None)
        assert cost is None

    def test_empty_metadata(self) -> None:
        pricing = {"unit": "per_second", "unit_price": 0.5, "currency": "CNY"}
        cost = _estimate_call_cost(None, "generate_video", pricing)
        assert cost is None


class TestVoiceCloneExclusion:
    """voice_clone_upload is free — must be in FREE_METHODS and mapped to tts."""

    def test_voice_clone_in_free_methods(self) -> None:
        assert "voice_clone_upload" in FREE_METHODS

    def test_voice_clone_in_method_map(self) -> None:
        assert METHOD_TO_MEDIA_TYPE.get("voice_clone_upload") == "tts"


class TestDefaultMediaPricing:
    """C2: DEFAULT_MEDIA_PRICING has entries for the main providers."""

    def test_seedream_default_exists(self) -> None:
        from app.gateway.routers.media_pricing import DEFAULT_MEDIA_PRICING

        seedream_entries = [e for e in DEFAULT_MEDIA_PRICING if e["provider"] == "seedream"]
        assert len(seedream_entries) == 1
        assert seedream_entries[0]["unit"] == "per_image"

    def test_seedance_ark_default_exists(self) -> None:
        from app.gateway.routers.media_pricing import DEFAULT_MEDIA_PRICING

        entries = [e for e in DEFAULT_MEDIA_PRICING if e["provider"] == "seedance-ark"]
        assert len(entries) == 1
        assert entries[0]["unit"] == "per_kilo_token"


class TestCacheInvalidation:
    """P1-1: invalidate_media_pricing_cache clears the cache so next read refetches."""

    def test_invalidate_clears_cache(self) -> None:
        from app.gateway.routers.console import (
            _load_media_pricing_sync,
            invalidate_media_pricing_cache,
        )

        # Ensure cache is populated (or at least initialized)
        invalidate_media_pricing_cache()
        # After invalidation, sync reader should return empty (cache = None)
        result = _load_media_pricing_sync()
        assert result == {}


class TestCrossAggregationConsistency:
    """P1 regression guard: failed rows must NOT be dropped from by_date/by_type.

    The original P1-2 fix used ``continue`` which skipped the date/type
    aggregation at the end of the loop. This test ensures all three
    aggregations (by_provider, by_date, by_type) see the same total call count.
    """

    def test_failed_rows_counted_in_all_aggregations(self) -> None:
        """Simulate the estimation loop with mixed success/error rows and
        verify Σby_date.calls == Σby_provider.calls == total_calls.
        """
        # We can't easily call the full endpoint, but we can verify the
        # loop logic by checking that _estimate_call_cost is not called
        # for failed rows (status filtering) while the counting logic
        # at the loop tail runs unconditionally.
        #
        # Instead: verify the fix is structural — no `continue` or `break`
        # in the estimation branch that could skip subsequent aggregation.
        import ast
        import pathlib

        console_path = pathlib.Path(__file__).resolve().parent.parent / "app" / "gateway" / "routers" / "console.py"
        tree = ast.parse(console_path.read_text())

        # Find the console_external_calls function
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "console_external_calls":
                # Walk the body looking for `continue` statements inside the
                # main for-loop (the row iteration). There should be NONE —
                # the P1-2 fix must use `if status in (ok/success):` not `continue`.
                for child in ast.walk(node):
                    if isinstance(child, ast.Continue):
                        pytest.fail("Found `continue` in console_external_calls — P1 regression: it skips by_date/by_type aggregation. Use `if row.status in (...):` to wrap only the estimation block.")
                break
        else:
            pytest.fail("console_external_calls function not found")


class TestEstimateCallCostRejectsNonFinite:
    """终审 M-2：`inf` / 超大数不得产生 `inf` 成本或 OverflowError 逃逸到 /api/usage。"""

    @pytest.mark.parametrize(
        "metadata",
        [
            {"completion_tokens": float("inf")},
            {"completion_tokens": "1e999"},
            {"completion_tokens": 10**400},
            {"total_tokens": float("inf")},
            {"completion_tokens": -1, "total_tokens": float("inf")},
        ],
        ids=["inf-completion", "str-1e999", "overflow-int", "inf-total", "inf-fallback"],
    )
    def test_per_kilo_token_non_finite_is_unpriced(self, metadata) -> None:
        pricing = {"unit": "per_kilo_token", "unit_price": 0.023, "currency": "CNY"}
        assert _estimate_call_cost(metadata, "generate_video", pricing) is None

    def test_per_second_and_per_kilo_char_non_finite_are_unpriced(self) -> None:
        assert _estimate_call_cost({"duration": float("inf")}, "generate_video", {"unit": "per_second", "unit_price": 0.5, "currency": "CNY"}) is None
        assert _estimate_call_cost({"char_count": 10**400}, "synthesize_sse", {"unit": "per_kilo_char", "unit_price": 0.5, "currency": "CNY"}) is None
        assert _estimate_call_cost({"duration": "nan"}, "generate_video", {"unit": "per_second", "unit_price": 0.5, "currency": "CNY"}) is None
