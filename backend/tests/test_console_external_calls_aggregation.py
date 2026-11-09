"""P3-③ Behavioral test: cross-aggregation consistency for /external-calls.

Verifies that mixed success/error rows produce consistent call counts across
all three aggregations (by_provider, by_date, by_type) — the P1 regression
where ``continue`` dropped failed rows from date/type aggregation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.routers.console import ConsoleScope


class _MockSession:
    def __init__(self, rows):
        self._rows = rows

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, stmt):
        result = MagicMock()
        scalars = MagicMock()
        scalars.all.return_value = self._rows
        result.scalars.return_value = scalars
        return result


class _MockSF:
    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


def _make_row(provider, method, status, metadata=None):
    """Create a mock ExternalApiCallRow."""
    row = MagicMock()
    row.created_at = datetime.now(UTC)
    row.user_id = "u1"
    row.provider = provider
    row.method = method
    row.status = status
    row.metadata_json = metadata
    return row


@pytest.mark.asyncio
async def test_mixed_success_error_rows_consistent_across_aggregations():
    """Σby_provider.calls == Σby_date.calls == total_calls, even with failed rows."""
    from app.gateway.routers import console as console_mod

    rows = [
        _make_row("gpt-image-2", "generate_image", "ok", {"amount": 0.04}),
        _make_row("gpt-image-2", "generate_image", "error", None),  # failed, no amount
        _make_row("seedance-ark", "generate_video", "success", None),
        _make_row("seedance-ark", "generate_video", "error", None),  # failed
        _make_row("doubao_tts", "synthesize_sse", "ok", None),
    ]

    session = _MockSession(rows)
    mock_sf = _MockSF(session)

    async def fake_pricing():
        return {}

    scope_mock = MagicMock()
    scope_mock.apply = lambda stmt, col: stmt
    scope_mock.response = lambda: ConsoleScope(is_admin=True, all_users=True)

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_resolve_scope", new_callable=AsyncMock, return_value=scope_mock),
        patch.object(console_mod, "_load_media_pricing", new_callable=AsyncMock, return_value={}),
    ):
        request = MagicMock()
        result = await console_mod.console_external_calls(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    # Total calls = 5
    assert result.total_calls == 5

    # Σ by_provider calls must equal total
    provider_sum = sum(p["calls"] for p in result.by_provider)
    assert provider_sum == 5, f"by_provider sum {provider_sum} != 5"

    # Σ by_date calls must equal total
    date_sum = sum(d["calls"] for d in result.by_date)
    assert date_sum == 5, f"by_date sum {date_sum} != 5"

    # Σ by_type calls (excluding free) must count non-free rows
    # voice_clone_upload is free, but all 5 rows here are non-free
    type_sum = sum(t["calls"] for t in result.by_type)
    assert type_sum == 5, f"by_type sum {type_sum} != 5"


@pytest.mark.asyncio
async def test_failed_rows_not_estimated_but_counted():
    """Failed rows have cost=None (not estimated), but their calls are counted."""
    from app.gateway.routers import console as console_mod

    rows = [
        _make_row("gpt-image-2", "generate_image", "ok", {"amount": 0.04}),
        _make_row("gpt-image-2", "generate_image", "error", None),  # failed
    ]

    session = _MockSession(rows)
    mock_sf = _MockSF(session)

    scope_mock = MagicMock()
    scope_mock.apply = lambda stmt, col: stmt
    scope_mock.response = lambda: ConsoleScope(is_admin=True, all_users=True)

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_resolve_scope", new_callable=AsyncMock, return_value=scope_mock),
        patch.object(console_mod, "_load_media_pricing", new_callable=AsyncMock, return_value={}),
    ):
        request = MagicMock()
        result = await console_mod.console_external_calls(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    # Find gpt-image-2 in by_provider
    gpt2 = next(p for p in result.by_provider if p["provider"] == "gpt-image-2")
    assert gpt2["calls"] == 2  # both success and error counted
    assert gpt2["cost"] is not None  # has at least the real-bill amount
    assert gpt2["cost"] == pytest.approx(0.04)  # only the success row's amount


@pytest.mark.asyncio
async def test_double_type_provider_yields_one_row_per_type():
    """B1（2026-08-27 评审修复）：同一 provider 同时落 image+video 行时（如 qihu
    的 Ref.source 同名——qihu_image.py / qihu_video.py 均 source="qihu"），
    by_provider 必须按 (provider, media_type) 出两行、各自带正确类型与金额。
    修前折叠为"首个非免费 method"的单值 → 前端按类型过滤时另一边整行消失、
    合计漏算。"""
    from app.gateway.routers import console as console_mod

    rows = [
        _make_row("qihu", "generate_video", "ok", {"amount": 1.0}),
        _make_row("qihu", "generate_video", "ok", {"amount": 2.0}),
        _make_row("qihu", "generate_image", "ok", {"amount": 0.5}),
    ]

    session = _MockSession(rows)
    mock_sf = _MockSF(session)

    scope_mock = MagicMock()
    scope_mock.apply = lambda stmt, col: stmt
    scope_mock.response = lambda: ConsoleScope(is_admin=True, all_users=True)

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_resolve_scope", new_callable=AsyncMock, return_value=scope_mock),
        patch.object(console_mod, "_load_media_pricing", new_callable=AsyncMock, return_value={}),
    ):
        request = MagicMock()
        result = await console_mod.console_external_calls(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    qihu_rows = [p for p in result.by_provider if p["provider"] == "qihu"]
    assert len(qihu_rows) == 2, f"双类型 provider 须出两行，实际: {qihu_rows}"
    video_row = next(p for p in qihu_rows if p["media_type"] == "video")
    image_row = next(p for p in qihu_rows if p["media_type"] == "image")
    assert video_row["calls"] == 2
    assert video_row["cost"] == pytest.approx(3.0)
    assert image_row["calls"] == 1
    assert image_row["cost"] == pytest.approx(0.5)


@pytest.mark.asyncio
async def test_voice_clone_excluded_from_type_but_counted_everywhere():
    """voice_clone_upload (free) is counted in by_provider and by_date,
    but excluded from by_type cost aggregation."""
    from app.gateway.routers import console as console_mod

    rows = [
        _make_row("volcengine", "voice_clone_upload", "ok", None),  # free
        _make_row("gpt-image-2", "generate_image", "ok", {"amount": 0.04}),
    ]

    session = _MockSession(rows)
    mock_sf = _MockSF(session)

    scope_mock = MagicMock()
    scope_mock.apply = lambda stmt, col: stmt
    scope_mock.response = lambda: ConsoleScope(is_admin=True, all_users=True)

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_resolve_scope", new_callable=AsyncMock, return_value=scope_mock),
        patch.object(console_mod, "_load_media_pricing", new_callable=AsyncMock, return_value={}),
    ):
        request = MagicMock()
        result = await console_mod.console_external_calls(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    # Total includes voice_clone
    assert result.total_calls == 2

    # by_provider includes volcengine
    volc = next(p for p in result.by_provider if p["provider"] == "volcengine")
    assert volc["calls"] == 1

    # by_type: voice_clone_upload maps to "tts" but is free → should NOT appear in type cost
    # (free methods are excluded from type aggregation entirely)
    tts_type = [t for t in result.by_type if t["media_type"] == "tts"]
    assert len(tts_type) == 0, "voice_clone_upload should be excluded from by_type"
