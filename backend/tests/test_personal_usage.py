from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.gateway._shared.personal_usage.types import (
    UsageModel,
    canonical_agent_capability,
    make_usage_window,
    merge_cost_source,
    normalize_usage_status,
)


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("course-video", "course-video"),
        ("creative-studio", "creative-studio"),
        ("ppt-master", "ppt-master"),
        ("podcast-studio", "podcast-studio"),
        ("ai_image", "ai_image"),
        ("ai_video", "ai_video"),
        ("course-ppt", "ppt-master"),
        ("Course_PPT", "ppt-master"),
        ("card-poster", "creative-studio"),
        ("case-article", "creative-studio"),
        ("design-specialist", "creative-studio"),
        ("knowledge-extraction", "creative-studio"),
        ("video-clip", "creative-studio"),
    ],
)
def test_canonical_agent_capability_uses_six_capability_whitelist(candidate, expected):
    assert canonical_agent_capability(candidate) == expected


def test_canonical_agent_capability_uses_first_supported_candidate():
    assert canonical_agent_capability(None, "lead_agent", "course-ppt", "ai_image") == "ppt-master"
    assert canonical_agent_capability("researcher", "not-a-capability") is None
    assert canonical_agent_capability(None, "") is None


@pytest.mark.parametrize("raw_status", ["success", "ready", " SUCCESS "])
def test_normalize_usage_status_completed(raw_status):
    assert normalize_usage_status(raw_status) == "completed"


@pytest.mark.parametrize(
    "raw_status",
    [
        "pending",
        "running",
        "draft",
        "extracting",
        "converting",
        "synthesizing",
        "scripting",
        "generating",
        "rendering",
        "story_ready",
        "composing",
    ],
)
def test_normalize_usage_status_running(raw_status):
    assert normalize_usage_status(raw_status) == "running"


@pytest.mark.parametrize(
    "raw_status",
    [None, "failed", "error", "timeout", "cancelled", "canceled", "interrupted", "idle", "unknown"],
)
def test_normalize_usage_status_conservatively_marks_incomplete(raw_status):
    assert normalize_usage_status(raw_status) == "incomplete"


@pytest.mark.parametrize(
    ("days", "start_date", "start_utc"),
    [
        (7, "2026-11-16", datetime(2026, 11, 15, 16, 0, tzinfo=UTC)),
        (14, "2026-11-09", datetime(2026, 11, 8, 16, 0, tzinfo=UTC)),
        (30, "2026-10-24", datetime(2026, 10, 23, 16, 0, tzinfo=UTC)),
    ],
)
def test_make_usage_window_uses_local_calendar_days(days, start_date, start_utc):
    window = make_usage_window(
        days,
        480,
        now=datetime(2026, 11, 21, 16, 30, tzinfo=UTC),
    )

    assert window.days == days
    assert window.start_date == start_date
    assert window.end_date == "2026-11-22"
    assert window.start_utc == start_utc
    assert window.end_utc == datetime(2026, 11, 22, 16, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("current", "incoming", "expected"),
    [
        (None, None, None),
        (None, "real", "real"),
        (None, "estimated", "estimated"),
        ("real", "real", "real"),
        ("estimated", "estimated", "estimated"),
        ("real", "estimated", "mixed"),
        ("estimated", "real", "mixed"),
        ("mixed", "real", "mixed"),
        ("estimated", "mixed", "mixed"),
    ],
)
def test_merge_cost_source(current, incoming, expected):
    assert merge_cost_source(current, incoming) == expected


def test_usage_model_preserves_unknown_cache_and_derived_total():
    model = UsageModel(
        name="priced-model",
        input_tokens=60,
        output_tokens=40,
        cache_read_tokens=None,
        total_tokens=100,
        cost=None,
        cost_source=None,
    )

    assert model.cache_read_tokens is None
    assert model.total_tokens == model.input_tokens + model.output_tokens
