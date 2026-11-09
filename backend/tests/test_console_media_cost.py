"""B1 补充：console by_model 聚合读取 media provider amount。"""

import pytest


class _MockSession:
    """模拟 async_sessionmaker 返回的 session 上下文。"""

    def __init__(self, run_rows, media_rows, pricing_rows=None):
        self._run_rows = run_rows
        self._media_rows = media_rows
        self._pricing_rows = pricing_rows or []
        self._call_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def execute(self, stmt):
        from unittest.mock import MagicMock

        self._call_count += 1
        result = MagicMock()
        scalars = MagicMock()
        # Call 1 = RunRow query, call 2 = media query (pricing is mocked out)
        if self._call_count == 1:
            scalars.all.return_value = self._run_rows
        else:
            scalars.all.return_value = self._media_rows
        result.scalars.return_value = scalars
        return result

    async def commit(self):
        pass


class _MockSF:
    """模拟 async_sessionmaker()。"""

    def __init__(self, session):
        self._session = session

    def __call__(self):
        return self._session


@pytest.mark.asyncio
async def test_console_usage_includes_media_cost_by_provider(monkeypatch):
    """console_usage 返回 media_cost_by_provider，从 external_api_calls.metadata_json.amount 聚合。"""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.routers import console as console_mod

    now = datetime.now(UTC)

    # Fake ExternalApiCallRow with amount
    fake_media = MagicMock()
    fake_media.created_at = now
    fake_media.user_id = "u1"
    fake_media.provider = "ai_image"
    fake_media.method = "generate_image"
    fake_media.metadata_json = {"amount": 0.020396}

    fake_media2 = MagicMock()
    fake_media2.created_at = now
    fake_media2.user_id = "u1"
    fake_media2.provider = "ai_video"
    fake_media2.method = "generate_video"
    fake_media2.metadata_json = {"amount": 0.15}

    session = _MockSession(run_rows=[], media_rows=[fake_media, fake_media2])
    mock_sf = _MockSF(session)

    async def fake_pricing(sf):
        return {}

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_build_pricing_map", fake_pricing),
        patch.object(console_mod, "_is_admin", return_value=True),
        patch.object(console_mod, "_auth_user", return_value=None),
        patch.object(console_mod, "get_current_user", new_callable=AsyncMock, return_value="admin"),
    ):
        request = MagicMock()
        result = await console_mod.console_usage(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    assert result.media_cost_by_provider["ai_image"] == pytest.approx(0.020396)
    assert result.media_cost_by_provider["ai_video"] == pytest.approx(0.15)


@pytest.mark.asyncio
async def test_console_media_cost_skips_no_amount():
    """无 amount 的 media 行不进入聚合。"""
    from datetime import UTC, datetime
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.routers import console as console_mod

    now = datetime.now(UTC)

    fake_media = MagicMock()
    fake_media.created_at = now
    fake_media.user_id = "u1"
    fake_media.provider = "ai_image"
    fake_media.method = "generate_image"
    fake_media.metadata_json = None  # 无 amount

    session = _MockSession(run_rows=[], media_rows=[fake_media])
    mock_sf = _MockSF(session)

    async def fake_pricing(sf):
        return {}

    with (
        patch.object(console_mod, "get_session_factory", return_value=mock_sf),
        patch.object(console_mod, "_build_pricing_map", fake_pricing),
        patch.object(console_mod, "_is_admin", return_value=True),
        patch.object(console_mod, "_auth_user", return_value=None),
        patch.object(console_mod, "get_current_user", new_callable=AsyncMock, return_value="admin"),
    ):
        request = MagicMock()
        result = await console_mod.console_usage(
            request=request,
            days=1,
            tz_offset_minutes=0,
            user_id=None,
        )

    assert result.media_cost_by_provider == {}
