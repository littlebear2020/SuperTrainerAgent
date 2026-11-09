"""Volcengine SSE code 归一化契约（Task 12 评审跟进）：缺失/空白/字符串 code 与数字等价，不得误抛丢音频。"""

import asyncio

import pytest

from app.gateway.voice_twin.volcengine_client import VolcengineTTSClient


class TestVolcengineSseCodeNormalization:
    """352/152/V1(None) 接受 ("", "0", None)；真实错误码仍 raise。"""

    def setup_method(self):
        self.client = VolcengineTTSClient(appid="a", token="t")
        self.chunks: list[bytes] = []

    def test_352_tolerates_missing_and_string_code(self):
        """352 缺 code / code="0"（字符串）不得误抛错丢弃已收音频。"""
        self.client._handle_sse_event("352", {"code": "0", "data": "QUFB"}, self.chunks)
        self.client._handle_sse_event("352", {"data": "QkJC"}, self.chunks)  # 缺 code
        self.client._handle_sse_event(None, {"data": "Q0ND"}, self.chunks)  # V1 无 event_type
        assert len(self.chunks) == 3

    def test_152_tolerates_missing_code(self):
        """152 finish 缺 code / code="20000000" 放行；其他码 raise。"""
        self.client._handle_sse_event("152", {}, self.chunks)  # 缺 code
        self.client._handle_sse_event("152", {"code": "20000000"}, self.chunks)
        with pytest.raises(RuntimeError, match="55000000"):
            self.client._handle_sse_event("152", {"code": 55000000, "message": "mismatch"}, self.chunks)

    def test_352_error_code_still_raises(self):
        """真实错误码（含字符串形式）仍 raise——归一化不放宽错误路径。"""
        with pytest.raises(RuntimeError, match="45000001"):
            self.client._handle_sse_event("352", {"code": "45000001", "message": "quota"}, self.chunks)


# ---------------------------------------------------------------------------
# C1 (2026-09-02): SSE 整调用超时——卡住无数据的流被掐断,正常消费不受影响
# ---------------------------------------------------------------------------


class _FakeSseResponse:
    """模拟 SSE response:aiter_lines 逐行产出给定行;hang=True 时产完后无限挂起。"""

    def __init__(self, lines: list[str], hang: bool = False):
        self._lines = lines
        self._hang = hang

    async def aiter_lines(self):
        for line in self._lines:
            yield line
        if self._hang:
            await asyncio.sleep(999)  # 僵死:连接挂着但再无数据


@pytest.mark.asyncio
async def test_consume_sse_stream_normal_consumption():
    """正常 SSE 流(含音频事件 + finish)被完整消费,不触发超时。"""
    client = VolcengineTTSClient(appid="a", token="t")
    chunks: list[bytes] = []
    resp = _FakeSseResponse(
        [
            "event: 352",
            'data: {"code":"0","data":"QUFB"}',
            "",
            "event: 152",
            'data: {"code":"20000000"}',
            "",
        ]
    )
    await client._consume_sse_stream(resp, chunks)
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_sse_idle_stream_is_cut_by_wait_for():
    """C1: 卡住无数据的 SSE 流被 wait_for 掐断(超时 → TimeoutError → 可重试/降级链)。"""
    client = VolcengineTTSClient(appid="a", token="t")
    chunks: list[bytes] = []
    resp = _FakeSseResponse([], hang=True)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(client._consume_sse_stream(resp, chunks), timeout=0.2)


def test_sse_timeout_env_default_and_override(monkeypatch):
    """C1: 超时值默认 180s,env VOLCENGINE_SSE_TIMEOUT 可调。"""
    from app.gateway.voice_twin._retry import volcengine_sse_timeout

    monkeypatch.delenv("VOLCENGINE_SSE_TIMEOUT", raising=False)
    assert volcengine_sse_timeout() == 180.0
    monkeypatch.setenv("VOLCENGINE_SSE_TIMEOUT", "240")
    assert volcengine_sse_timeout() == 240.0
