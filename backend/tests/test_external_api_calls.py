"""F1: external_api_calls table + collection point tests."""

from __future__ import annotations

import pytest

from app.gateway._shared.llm_usage_sink import record_external_api_call


@pytest.fixture(autouse=True)
def _reset_llm_accounting_ctx():
    """防止 set_llm_accounting_context 跨文件泄漏（同 test_llm_usage_sink.py 口径）。"""
    from app.gateway._shared.llm_usage_sink import _accounting_ctx

    token = _accounting_ctx.set(None)
    try:
        yield
    finally:
        _accounting_ctx.reset(token)


@pytest.fixture()
async def db(tmp_path):
    # Ensure the model is registered in Base.metadata before create_all
    import deerflow.persistence.external_api_calls.model  # noqa: F401
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'f1.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await close_engine()


async def _count_calls():
    from sqlalchemy import func, select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    sf = get_session_factory()
    async with sf() as session:
        return await session.scalar(select(func.count()).select_from(ExternalApiCallRow)) or 0


async def _get_calls():
    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    sf = get_session_factory()
    async with sf() as session:
        result = await session.execute(select(ExternalApiCallRow))
        return result.scalars().all()


class TestExternalApiCall:
    @pytest.mark.anyio
    async def test_writes_row(self, db):
        await record_external_api_call(
            provider="seedream",
            method="generate_image",
            status="success",
            elapsed_ms=1500,
            user_id="u1",
        )

        rows = await _get_calls()
        assert len(rows) == 1
        assert rows[0].provider == "seedream"
        assert rows[0].method == "generate_image"
        assert rows[0].status == "success"
        assert rows[0].elapsed_ms == 1500
        assert rows[0].user_id == "u1"

    @pytest.mark.anyio
    async def test_user_id_fallback_to_context(self, db):
        from app.gateway._shared.llm_usage_sink import set_llm_accounting_context

        set_llm_accounting_context("t", "ctx-user", "test")
        await record_external_api_call(
            provider="edge_tts",
            method="synthesize",
            status="success",
        )

        rows = await _get_calls()
        assert len(rows) == 1
        assert rows[0].user_id == "ctx-user"

    @pytest.mark.anyio
    async def test_does_not_crash_without_db(self):
        await record_external_api_call(
            provider="test",
            method="test",
            status="success",
        )

    @pytest.mark.anyio
    async def test_multiple_providers(self, db):
        await record_external_api_call(provider="seedream", method="generate_image", status="success", user_id="u")
        await record_external_api_call(provider="seedance_ark", method="generate_video", status="success", user_id="u")
        await record_external_api_call(provider="doubao_tts", method="synthesize", status="error", user_id="u")

        assert await _count_calls() == 3


class _FakeStreamResponse:
    """Minimal stand-in for the httpx streaming response used by TTS clients."""

    def __init__(self, lines: list[str], *, is_error: bool = False, status_code: int = 200, body: bytes = b""):
        self._lines = lines
        self.is_error = is_error
        self.status_code = status_code
        self._body = body

    async def aread(self) -> bytes:
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, resp: _FakeStreamResponse):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *args):
        return False


class _FakeAsyncClient:
    def __init__(self, resp: _FakeStreamResponse):
        self._resp = resp

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, *args, **kwargs):
        return _FakeStreamCM(self._resp)


class TestVolcengineSynthesizeTracking:
    """F1 follow-up: VolcengineTTSClient.synthesize_sse (the pay-per-call
    synthesis hot path) must record success AND failure rows — only the
    training upload was tracked in the first cut."""

    @pytest.mark.anyio
    async def test_success_records_row(self, db, monkeypatch):
        from app.gateway.voice_twin import volcengine_client
        from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient

        resp = _FakeStreamResponse(['data: {"code": 0, "data": "QUFB"}', ""])
        monkeypatch.setattr(volcengine_client.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(resp))

        client = VolcengineTTSClient(appid="a", token="t")  # no access_key → V1 payload
        audio = await client.synthesize_sse("speaker", "你好")
        assert audio == b"AAA"

        rows = await _get_calls()
        assert len(rows) == 1
        assert rows[0].provider == "volcengine"
        assert rows[0].method == "synthesize_sse"
        assert rows[0].status == "success"

    @pytest.mark.anyio
    async def test_http_error_records_error_row(self, db, monkeypatch):
        from app.gateway.voice_twin import volcengine_client
        from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient

        resp = _FakeStreamResponse([], is_error=True, status_code=400, body=b"bad request")
        monkeypatch.setattr(volcengine_client.httpx, "AsyncClient", lambda **kw: _FakeAsyncClient(resp))

        client = VolcengineTTSClient(appid="a", token="t")
        with pytest.raises(RuntimeError, match="400"):
            await client.synthesize_sse("speaker", "你好")

        rows = await _get_calls()
        assert len(rows) == 1
        assert rows[0].provider == "volcengine"
        assert rows[0].method == "synthesize_sse"
        assert rows[0].status == "error"
