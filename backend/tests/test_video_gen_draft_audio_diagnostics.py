"""Plan A Task 3（2026-10-18）：草稿音频失败按同轮次阶段留痕。

事故背景（vg_da6419f0c0e6 谱系）：草稿「准备音频」失败时 payload 只有通用
``audio_error = "音频生成失败，请重试"``——TTS/拼接/字幕/合并四个阶段不可区分，
sidecar 的 voice_fallback 只证明「进入降级」不能证明「降级成功」，排障只能翻
服务端日志。修复口径：

- ``_synthesize_audio(..., stage_hook)``：hook 在 tts→concat→subtitle 阶段边界
  被调（``Callable[[str], None]``，缺省 None 不改变其他调用方）；
- ``_generate_draft_audio`` 传 hook 并在 merge 前本地切到 ``merge``；异常出口
  写 ``audio_error_stage``（tts|concat|subtitle|merge）+ ``audio_error`` 含中文
  阶段；成功出口同时清除旧 ``audio_error``/``audio_error_stage``；
- 原始异常仍走 ``logger.exception``（服务端日志保留完整 traceback——payload 只
  放阶段化文案，不外泄 traceback）。

受控边界沿用 test_video_gen_draft_audio_drift.py 的 harness 形态：
TTS 边界 mock + service.run_ffmpeg/​_write_storyboard_srt 按用例注入。
"""

import asyncio
import json
import logging
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway._shared.task_framework.schemas import DRAFT
from app.gateway.routers.video_gen import get_storyboard
from app.gateway.video_gen.service import VideoGenService

VOICE = "zh-CN-YunjianNeural"


def _task_dir(tmp_path: Path, task_id: str) -> Path:
    from deerflow.runtime.user_context import get_effective_user_id

    d = tmp_path / get_effective_user_id() / "tasks" / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _make_svc(tmp_path: Path, payload: dict | None = None):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry

    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-script-model")
    svc = VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )
    task = MagicMock()
    task.payload = json.dumps(payload or {}, ensure_ascii=False)
    task.status = DRAFT
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    return svc


class _FakeRequest:
    def __init__(self, svc):
        self.app = types.SimpleNamespace(state=types.SimpleNamespace(video_gen_service=svc))


def _write_sb(task_dir: Path, narrations: list[str]) -> None:
    frames = []
    for i, n in enumerate(narrations):
        frames.append({"index": i, "narration": n, "duration": 2.0})
    sb = {"title": "t", "config": {"video_type": "ai_image", "width": 1080, "height": 1920, "voice_id": VOICE, "tts_speed": 1.2}, "frames": frames}
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")


def _fake_tts(text, **kwargs):
    from app.gateway.video_gen.orchestration.tts import TTSResult

    out = Path(kwargs["output_path"])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(b"fake-mp3")
    return TTSResult(audio_path=str(out), srt="", duration=2.0, actual_voice_id=None)


def _last_payload(svc) -> dict:
    assert svc.repo.update.call_args_list, "repo.update 必须被调用"
    last = svc.repo.update.call_args_list[-1]
    return json.loads(last.kwargs["payload"])


async def _run_draft(svc, task_id: str, task_dir: Path) -> None:
    await asyncio.wait_for(svc._generate_draft_audio(task_id, task_dir), timeout=30)


TASK_ID = "vg_diag_test"
STAGE_LABELS = {"tts": "配音合成", "concat": "音频拼接", "subtitle": "字幕生成", "merge": "音频合并"}


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["tts", "concat", "subtitle", "merge"])
async def test_stage_failure_labels_payload_and_clears_ready(tmp_path, caplog, stage):
    """四类阶段异常：payload 含正确 audio_error_stage、中文阶段文案、无 audio_ready；
    原始异常仍由 logger.exception 留痕。"""
    task_dir = _task_dir(tmp_path, TASK_ID)
    _write_sb(task_dir, ["第一句旁白", "第二句旁白"])
    svc = _make_svc(tmp_path, payload={"audio_ready": True})  # 上代成功标记残留

    tts_raise = stage == "tts"
    with (
        patch(
            "app.gateway.video_gen.orchestration.tts.synthesize",
            side_effect=(RuntimeError("tts boom") if tts_raise else _fake_tts),
        ),
        patch(
            "app.gateway.video_gen.service.run_ffmpeg",
            new=AsyncMock(side_effect=(RuntimeError("concat boom") if stage == "concat" else None)),
        ),
        patch(
            "app.gateway.video_gen.service._write_storyboard_srt",
            side_effect=(RuntimeError("srt boom") if stage == "subtitle" else None),
        ),
        patch("app.gateway.video_gen.service._get_audio_duration", new=lambda p: 2.0),
        patch.object(
            svc,
            "_merge_audio_into_storyboard",
            new=AsyncMock(side_effect=(RuntimeError("merge boom") if stage == "merge" else None)),
        ),
    ):
        with caplog.at_level(logging.ERROR):
            await _run_draft(svc, TASK_ID, task_dir)

    pl = _last_payload(svc)
    assert pl.get("audio_error_stage") == stage, f"{stage} 阶段异常须写 audio_error_stage，实际 payload={pl}"
    assert STAGE_LABELS[stage] in pl.get("audio_error", ""), f"audio_error 须含中文阶段「{STAGE_LABELS[stage]}」，实际 {pl.get('audio_error')!r}"
    assert "audio_ready" not in pl, "失败轮不得残留 audio_ready"
    assert any("Draft audio generation failed" in r.message for r in caplog.records), "原始异常须由 logger.exception 记录"


@pytest.mark.asyncio
async def test_success_clears_stale_error_and_stage(tmp_path):
    """新一代音频成功：旧 audio_error 与 audio_error_stage 必须一起清除。"""
    task_dir = _task_dir(tmp_path, TASK_ID)
    _write_sb(task_dir, ["第一句旁白", "第二句旁白"])
    svc = _make_svc(tmp_path, payload={"audio_error": "音频生成失败（配音合成），请重试", "audio_error_stage": "tts"})

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_tts),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._get_audio_duration", new=lambda p: 2.0),
    ):
        await _run_draft(svc, TASK_ID, task_dir)

    pl = _last_payload(svc)
    assert pl.get("audio_ready") is True
    assert "audio_error" not in pl, "成功轮须清除旧 audio_error"
    assert "audio_error_stage" not in pl, "成功轮须同时清除旧 audio_error_stage"


@pytest.mark.asyncio
async def test_router_storyboard_merges_audio_error_stage(tmp_path):
    """GET storyboard 响应透传 audio_error_stage（前端据此前端展示真实阶段）。"""
    task_dir = _task_dir(tmp_path, TASK_ID)
    _write_sb(task_dir, ["第一句旁白"])
    svc = _make_svc(tmp_path, payload={"audio_error": "音频生成失败（音频拼接），请重试", "audio_error_stage": "concat"})

    resp = await get_storyboard(_FakeRequest(svc), TASK_ID)
    body = json.loads(resp.body) if isinstance(resp, object) and hasattr(resp, "body") else resp
    assert body.get("audio_error_stage") == "concat", f"router 须透传 audio_error_stage，实际 {body}"
    assert "音频拼接" in body.get("audio_error", "")


@pytest.mark.asyncio
async def test_entry_phase_failure_still_writes_error_payload(tmp_path):
    """评审修复回归：入口阶段（stage 绑定前）异常不得让 except 处理器自身崩溃。

    storyboard.json 损坏 → 修前 except 里 stage["current"] 抛 UnboundLocalError
    穿出、audio_error 永不写（前端无限轮询）；修后必须正常写 tts 阶段错误。
    """
    task_dir = _task_dir(tmp_path, TASK_ID)
    (task_dir / "storyboard.json").write_text("{ not valid json", encoding="utf-8")
    svc = _make_svc(tmp_path, payload={"audio_ready": True})

    await asyncio.wait_for(svc._generate_draft_audio(TASK_ID, task_dir), timeout=10)  # 不得 raise

    pl = _last_payload(svc)
    assert "配音合成" in pl.get("audio_error", ""), f"入口异常须写 tts 阶段文案，实际 {pl!r}"
    assert pl.get("audio_error_stage") == "tts"
    assert "audio_ready" not in pl
