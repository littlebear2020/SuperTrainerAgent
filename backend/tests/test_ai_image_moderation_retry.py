# backend/tests/test_ai_image_moderation_retry.py
"""ai_image 生图：审核拒绝→词表中性化→旁白直述，两层重试各一次（对齐 ai_video，P1 2026-08-19）。

fixture 形态沿用 tests/test_content_review_retry.py：MagicMock provider 按 prompt
队列返回/抛错，Storyboard 手搓单帧。
"""

import logging
import traceback
from unittest.mock import MagicMock

import pytest


def _sb(prompt: str, narration: str = "n"):  # noqa: ANN202
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_image", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration=narration, duration=5.0, image_prompt=prompt)])


def _src(fake_gen):  # noqa: ANN001,ANN202
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    provider = MagicMock(generate_image=fake_gen)
    return AIImageVisualSource(provider, concurrency=1)


@pytest.fixture()
def _no_localize(monkeypatch):
    from app.gateway.video_gen.visual_sources import ai_image as mod

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_image", fake_localize)


@pytest.mark.asyncio
async def test_false_positive_retries_with_narration_prompt(tmp_path, _no_localize) -> None:
    """审核误判且无可中性化 → 旁白直述 prompt 重试一次并成功。"""
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        if len(seen) == 1:
            raise RuntimeError('360 image API 400: {"error":{"code":12020,"message":"命中敏感[色情:女裸露:女胸裸露]"}}')
        return MagicMock(url="http://img/1")

    sb = _sb("人形光影拖入界面", narration="程序员把光影拖入界面")
    await _src(fake_gen).render(sb, task_dir=tmp_path)
    assert len(seen) == 2, f"应旁白直述重试一次，实际调用 {len(seen)} 次"
    assert seen[1] == "程序员把光影拖入界面", f"重试应使用旁白原文，实际 {seen[1]}"
    assert sb.frames[0].visual_url == "http://img/1", "重试成功后必须写回结果"


@pytest.mark.asyncio
async def test_neutralizable_word_retried_neutralized(tmp_path, _no_localize) -> None:
    """词表命中 → 中性化 prompt 重试一次。"""
    from app.gateway.video_gen.providers.content_review import neutralize_prompt

    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        if len(seen) == 1:
            raise RuntimeError("360 image API 400: 内容审核不通过")
        return MagicMock(url="http://img/2")

    sb = _sb("血迹斑斑的战场", narration="战场实况")
    await _src(fake_gen).render(sb, task_dir=tmp_path)
    assert len(seen) == 2
    assert seen[1] == neutralize_prompt("血迹斑斑的战场"), f"重试应使用中性化提示词，实际 {seen[1]}"


@pytest.mark.asyncio
async def test_moderation_reject_without_narration_surfaces(tmp_path, _no_localize) -> None:
    """审核拒绝且旁白为空 → 上抛（覆盖率闸响亮失败），不重试。"""
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        raise RuntimeError("360 image API 400: 内容审核不通过")

    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src(fake_gen).render(_sb("人形光影拖入界面", narration=""), task_dir=tmp_path)
    assert len(seen) == 1, f"旁白为空不得重试，实际调用 {len(seen)} 次"


@pytest.mark.asyncio
async def test_moderation_retry_exhaustion_chains_last_error(tmp_path, _no_localize, caplog) -> None:
    """三层调用均被审核拒绝而耗尽 → 上抛的 RuntimeError 必须链接最后一次审核异常。

    PEP 3110：``except ... as _err`` 块结束即删变量，不显式保存则「最终死于
    哪层拒绝」在日志 traceback 里丢失（外部审查 2026-08-19 问题2，低危可观测性）。
    """
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        raise RuntimeError("360 image API 400: 内容审核不通过")

    with caplog.at_level(logging.WARNING):
        with pytest.raises(RuntimeError, match="coverage too low"):
            await _src(fake_gen).render(_sb("血迹斑斑的战场", narration="战场实况"), task_dir=tmp_path)
    assert len(seen) == 3, f"原始+中性化+旁白共 3 次后耗尽，实际 {len(seen)} 次"
    rec = next(r for r in caplog.records if "AI image frame" in r.getMessage() and r.exc_info)
    chain = "".join(traceback.format_exception(*rec.exc_info))
    assert "moderation retry exhausted" in chain
    assert "内容审核不通过" in chain, "异常链须含最后一次审核拒绝原文（raise ... from）"
