"""Task 3（2026-10-20，已拍板）：分镜超时收敛为 2 次 × 420 秒，且不再任务级重放。

- ``VIMAX_STORYBOARD_TIMEOUT_S`` 默认 420，非法回 420，clamp 60..900；
- ``LocalStoryboardDeadlineError.pipeline_retryable`` 恒 False（单批/多批一致）；
- artist ``design_storyboard`` 恰好 2 次 attempt；
- 只有 script2video 的 ``design_storyboard`` 使用新 timeout，decompose 与
  idea2video 的 300s 不变。
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import Runnable

from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine
from app.gateway.video_gen.engines.vimax.storyboard_timeout import resolve_storyboard_timeout_s

# ── helper ─────────────────────────────────────────────────────────────────


def test_timeout_default_is_420():
    assert resolve_storyboard_timeout_s({}) == 420


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("600", 600),
        ("0", 420),
        ("abc", 420),
        ("nan", 420),
        ("", 420),
        ("30", 60),
        ("5000", 900),
        ("-5", 420),
    ],
)
def test_timeout_env_parse_and_clamp(raw, expected):
    assert resolve_storyboard_timeout_s({"VIMAX_STORYBOARD_TIMEOUT_S": raw}) == expected


def test_timeout_none_env_uses_os_environ(monkeypatch):
    monkeypatch.setenv("VIMAX_STORYBOARD_TIMEOUT_S", "600")
    assert resolve_storyboard_timeout_s() == 600


# ── deadline 语义：task_retryable 三态 ─────────────────────────────────────


@pytest.mark.parametrize(
    "batches_reusable,task_retryable,expected",
    [
        (False, None, True),  # 旧行为：单批可重放
        (True, None, False),  # 旧行为：多批不重放
        (False, False, False),  # script2video：显式不重放
        (True, False, False),
        (False, True, True),  # idea2video：显式重放
        (True, True, True),
    ],
)
def test_local_deadline_task_retryable_tristate(batches_reusable, task_retryable, expected):
    from vimax.utils.retry import LocalStoryboardDeadlineError

    exc = LocalStoryboardDeadlineError(420, batches_reusable=batches_reusable, task_retryable=task_retryable)
    assert exc.pipeline_retryable is expected


# ── artist 恰好 2 次 ───────────────────────────────────────────────────────


class _FailingChatModel(Runnable):
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, input, config=None, **kwargs):
        raise NotImplementedError

    async def ainvoke(self, messages, config=None, **kwargs):
        self.calls += 1
        raise TimeoutError("provider timed out")


@pytest.mark.asyncio
async def test_storyboard_artist_stops_after_two_attempts(monkeypatch):
    import tenacity
    from vimax.agents import storyboard_artist

    monkeypatch.setattr(storyboard_artist, "_STORYBOARD_RETRY_WAIT", tenacity.wait_none())
    model = _FailingChatModel()
    artist = storyboard_artist.StoryboardArtist(chat_model=model, max_attempts=2)

    with pytest.raises(asyncio.TimeoutError):
        await artist.design_storyboard(script="x", characters=[], retry_timeout=0.05)

    assert model.calls == 2


@pytest.mark.asyncio
async def test_storyboard_artist_default_retries_three_times_for_idea2video(monkeypatch):
    import tenacity
    from vimax.agents import storyboard_artist

    monkeypatch.setattr(storyboard_artist, "_STORYBOARD_RETRY_WAIT", tenacity.wait_none())
    model = _FailingChatModel()
    artist = storyboard_artist.StoryboardArtist(chat_model=model)

    with pytest.raises(asyncio.TimeoutError):
        await artist.design_storyboard(script="x", characters=[], retry_timeout=0.05)

    assert artist.max_attempts == 3
    assert model.calls == 3


# ── engine 只对 script2video 设置 storyboard_timeout ───────────────────────


class _RecordingPipeline:
    def __init__(self) -> None:
        self.llm_retry_timeout = 300
        self.storyboard_timeout = 300
        self.storyboard_max_attempts = 3
        self.storyboard_task_retryable = True
        self.storyboard_artist = type("Artist", (), {"max_attempts": 3})()
        self.calls: list[dict] = []

    def configure_storyboard_retry(self, *, max_attempts, timeout, task_retryable):
        self.storyboard_max_attempts = max_attempts
        self.storyboard_timeout = timeout
        self.storyboard_task_retryable = task_retryable
        self.storyboard_artist.max_attempts = max_attempts

    async def __call__(self, **kwargs):
        self.calls.append(kwargs)


def _ctx(tmp_path, request: dict) -> EngineContext:
    return EngineContext(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request=request)


@pytest.mark.asyncio
async def test_engine_sets_storyboard_timeout_only(tmp_path, monkeypatch):
    monkeypatch.setenv("VIMAX_STORYBOARD_TIMEOUT_S", "420")
    pipe = _RecordingPipeline()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipe)

    await engine.render(_ctx(tmp_path, {"script": "剧本"}))

    assert pipe.storyboard_timeout == 420
    assert pipe.storyboard_max_attempts == 2
    assert pipe.storyboard_task_retryable is False
    assert pipe.storyboard_artist.max_attempts == 2
    assert pipe.llm_retry_timeout == 300


@pytest.mark.asyncio
async def test_engine_does_not_touch_storyboard_timeout_for_idea2video(tmp_path, monkeypatch):
    monkeypatch.setenv("VIMAX_STORYBOARD_TIMEOUT_S", "600")
    pipe = _RecordingPipeline()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipe)

    await engine.render(_ctx(tmp_path, {"idea": "一个想法", "pipeline": "idea2video"}))

    assert pipe.storyboard_timeout == 300
    assert pipe.storyboard_max_attempts == 3
    assert pipe.storyboard_task_retryable is True
    assert pipe.storyboard_artist.max_attempts == 3
    assert pipe.llm_retry_timeout == 300


# ── pipeline：design_storyboard 用 storyboard_timeout，decompose 保持 300 ──


@pytest.mark.asyncio
async def test_pipeline_wires_storyboard_timeout_into_artist(tmp_path):
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    pipe = Script2VideoPipeline(chat_model=object(), image_generator=AsyncMock(), video_generator=AsyncMock(), working_dir=str(tmp_path))
    pipe.storyboard_timeout = 420
    seen: list[int] = []

    async def fake_design_storyboard(**kwargs):
        seen.append(kwargs["retry_timeout"])
        from vimax.interfaces import ShotBriefDescription

        return [ShotBriefDescription(idx=0, is_last=True, cam_idx=0, visual_desc="v", audio_desc="a")]

    pipe.storyboard_artist.design_storyboard = fake_design_storyboard
    await pipe.design_storyboard(script="剧本一段。", characters=[], user_requirement="")

    assert seen == [420]


@pytest.mark.asyncio
async def test_idea2video_child_pipeline_keeps_three_attempts_and_task_retry(tmp_path, monkeypatch):
    """真正驱动 Idea2VideoPipeline 构造子 Script2VideoPipeline：子 artist 必须
    保持 3 attempts × 300s + 任务级重试（共享 artist 的 script2video 2 次策略
    不得扩散到 idea2video）。"""
    import os

    import vimax.pipelines.idea2video_pipeline as idea2video_module
    from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline
    from vimax.utils.retry import LocalStoryboardDeadlineError

    class _SpyScript2Video(Script2VideoPipeline):
        instances: list = []

        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.__class__.instances.append(self)

        async def __call__(self, **kwargs):
            return os.path.join(self.working_dir, "scene_final.mp4")

    monkeypatch.setattr(idea2video_module, "Script2VideoPipeline", _SpyScript2Video)
    monkeypatch.setattr(idea2video_module, "concatenate_video_files", lambda *_a, **_kw: None)

    pipe = Idea2VideoPipeline(chat_model=object(), image_generator=AsyncMock(), video_generator=AsyncMock(), working_dir=str(tmp_path / "idea"))
    pipe.develop_story = AsyncMock(return_value="story")
    pipe.extract_characters = AsyncMock(return_value=[])
    pipe.generate_character_portraits = AsyncMock(return_value={})
    pipe.write_script_based_on_story = AsyncMock(return_value=["scene script"])

    await pipe(idea="一个想法", user_requirement="", style="realistic", quiet=True)

    assert len(_SpyScript2Video.instances) == 1
    child = _SpyScript2Video.instances[0]
    assert child.storyboard_artist.max_attempts == 3
    assert child.storyboard_max_attempts == 3
    assert child.storyboard_timeout == 300
    assert child.storyboard_task_retryable is True
    exc = LocalStoryboardDeadlineError(300, batches_reusable=False, task_retryable=child.storyboard_task_retryable)
    assert exc.pipeline_retryable is True
