"""E5+E6: LLM 调用有界超时 + outline JSON 兜底。"""

import asyncio
import json

import pytest


class _FakeResp:
    def __init__(self, content):
        self.content = content


class _HangingLLM:
    """ainvoke 挂起 30s——模拟 LLM hang。

    必须用有界 sleep 而非永不 set 的 Event：绿路径下生产 wait_for(0.05) 会
    取消协程，30s 从不真正等满；而超时保护回归（wait_for 被删/坏）时，
    sleep 版最多 30s 后以 DID-NOT-RAISE 干净变红，Event 版会永久挂起——
    仓库未配 pytest-timeout，挂起只能烧满 CI job 级 15min 超时。"""

    async def ainvoke(self, messages):
        await asyncio.sleep(30)
        return _FakeResp("never")  # pragma: no cover - unreachable


class _OkLLM:
    """ainvoke 立即返回。"""

    def __init__(self, content="ok"):
        self._content = content

    async def ainvoke(self, messages):
        return _FakeResp(self._content)


@pytest.mark.asyncio
async def test_invoke_llm_bounded_raises_on_timeout():
    """E5: LLM hang 时 _invoke_llm_bounded 在有界时间内抛可诊断错误（不无限挂）。"""
    from app.gateway.video_gen.orchestration import _invoke_llm_bounded

    with pytest.raises(RuntimeError, match="timed out|timeout|超时"):
        await _invoke_llm_bounded(_HangingLLM(), [], timeout=0.05)


@pytest.mark.asyncio
async def test_invoke_llm_bounded_returns_response_on_success():
    """E5: 正常返回时 _invoke_llm_bounded 透传 LLM 响应。"""
    from app.gateway.video_gen.orchestration import _invoke_llm_bounded

    resp = await _invoke_llm_bounded(_OkLLM("hello"), [], timeout=5)
    assert resp.content == "hello"


@pytest.mark.asyncio
async def test_generate_outline_domain_error_on_bad_json():
    """E6: generate_outline 收到非 JSON → 抛领域错误（不裸 JSONDecodeError 冒泡）。"""
    from app.gateway.video_gen.course_pipeline import CoursePipelineService

    class _BadJSONLLM:
        async def ainvoke(self, messages):
            return _FakeResp("这不是 JSON，完全是自然语言")

    svc = CoursePipelineService()
    with pytest.raises(Exception) as exc:
        await svc.generate_outline(_BadJSONLLM(), topic="新经理培训", target_duration_min=9)
    # 关键：不得是裸 json.JSONDecodeError（让下游无法区分"LLM 格式漂移"与"系统错误"）
    assert not isinstance(exc.value, json.JSONDecodeError), "generate_outline 不得裸抛 JSONDecodeError"
