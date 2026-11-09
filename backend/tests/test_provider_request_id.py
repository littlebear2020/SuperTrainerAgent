"""A2: provider request_id 全链路捕获——错误 msg 必须带上 request_id。"""

import httpx
import pytest
import respx

from app.gateway.video_gen.providers.base import _append_request_id, _extract_request_id


class _FakeResp:
    """Minimal fake httpx.Response for unit testing _extract_request_id."""

    def __init__(self, headers=None, json_body=None):
        self.headers = headers or {}
        self._json = json_body

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


def test_extract_from_header():
    resp = _FakeResp(headers={"x-request-id": "req-abc123"})
    assert _extract_request_id(resp) == "req-abc123"


def test_extract_from_error_body():
    resp = _FakeResp(json_body={"error": {"request_id": "req-xyz"}})
    assert _extract_request_id(resp) == "req-xyz"


def test_extract_from_nested_message_json():
    import json

    inner = json.dumps({"request_id": "req-nested", "status": "failed"})
    resp = _FakeResp(json_body={"error": {"code": "10940", "message": inner}})
    assert _extract_request_id(resp) == "req-nested"


def test_extract_returns_none_when_absent():
    resp = _FakeResp(headers={}, json_body={"error": {"code": "500"}})
    assert _extract_request_id(resp) is None


def test_append_request_id_adds_suffix():
    resp = _FakeResp(headers={"x-request-id": "r-1"})
    msg = _append_request_id(resp, "submit failed (400)")
    assert "[request_id=r-1]" in msg


def test_append_request_id_no_op_when_absent():
    resp = _FakeResp(headers={}, json_body=None)
    msg = _append_request_id(resp, "submit failed (400)")
    assert "request_id" not in msg


@respx.mock
@pytest.mark.asyncio
async def test_qihu_video_submit_error_carries_request_id():
    """360 video submit 4xx 错误 msg 必须包含 request_id（来自 x-request-id header）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    respx.post("https://api.360.cn/v1/videos/async_generations").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"code": "forbidden"}},
            headers={"x-request-id": "vid-req-001"},
        ),
    )
    provider = QihuVideoProvider(api_key="test-key")
    with pytest.raises(RuntimeError, match="vid-req-001"):
        await provider.generate_video("test", duration=8.0)


@respx.mock
@pytest.mark.asyncio
async def test_qihu_image_error_carries_request_id():
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    respx.post("https://api.360.cn/v1/images/generations").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "bad_request"}},
            headers={"x-request-id": "img-req-002"},
        ),
    )
    provider = QihuImageProvider(api_key="test-key")
    with pytest.raises(RuntimeError, match="img-req-002"):
        await provider.generate_image("a cat")


@respx.mock
@pytest.mark.asyncio
async def test_seedance_ark_submit_error_carries_request_id():
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    respx.post("https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks").mock(
        return_value=httpx.Response(
            403,
            json={"error": {"code": "forbidden"}},
            headers={"x-request-id": "ark-req-003"},
        ),
    )
    provider = SeedanceArkVideoProvider(api_key="test-key")
    with pytest.raises(RuntimeError, match="ark-req-003"):
        await provider.generate_video("test", duration=5.0)


@respx.mock
@pytest.mark.asyncio
async def test_seedream_error_carries_request_id():
    from app.gateway.video_gen.providers.seedream import SeedreamImageProvider

    respx.post("https://ark.cn-beijing.volces.com/api/v3/images/generations").mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": "bad_request"}},
            headers={"x-request-id": "sd-req-004"},
        ),
    )
    provider = SeedreamImageProvider(api_key="test-key")
    with pytest.raises(RuntimeError, match="sd-req-004"):
        await provider.generate_image("a cat")
