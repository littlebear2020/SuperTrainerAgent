"""T6（2026-10-16 收口批）：idea2video vendored 卫生（P37）。

- 三处角色肖像 ``.save()``（PIL 同步编码/写盘）经 ``asyncio.to_thread`` 线程池
  （P34 已修 script2video 侧，idea2video 的 portrait 链是 Lane 1 plan T7 枚举
  漏列——findings-backlog 2026-10-15 条目）。
- ``story.txt`` 直写改原子落盘（``_atomic_json_dump`` 同款文本版）：写盘中断
  不留半截文件毒化 resume（resume 见存在即跳过 develop_story）。
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from vimax.interfaces import CharacterInScene
from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline


def _character(name: str = "船夫") -> CharacterInScene:
    return CharacterInScene(idx=0, identifier_in_scene=name, is_visible=True, static_features=f"{name} static", dynamic_features=f"{name} dynamic")


def _make_pipe(tmp_path, *, portraits=None, screenwriter=None) -> Idea2VideoPipeline:
    """绕过 __init__（会构造真实 Screenwriter/LLM）——直接装配被测属性。"""
    pipe = Idea2VideoPipeline.__new__(Idea2VideoPipeline)
    pipe.chat_model = object()
    pipe.image_generator = SimpleNamespace(_aspect_ratio=None)
    pipe.video_generator = SimpleNamespace()
    pipe.working_dir = str(tmp_path)
    pipe.character_portraits_generator = portraits
    pipe.screenwriter = screenwriter
    pipe.character_extractor = SimpleNamespace()
    return pipe


class _BytesPortrait:
    def __init__(self, payload: bytes):
        self.payload = payload

    def save(self, path: str) -> None:
        with open(path, "wb") as handle:
            handle.write(self.payload)


@pytest.mark.asyncio
async def test_portrait_saves_go_through_to_thread(tmp_path, monkeypatch):
    """front/side/back 三处 PIL save 须经 asyncio.to_thread（off-loop）。"""
    recorded: list[str] = []
    real_to_thread = asyncio.to_thread

    async def recording_to_thread(fn, *args, **kwargs):
        recorded.append(getattr(fn, "__name__", str(fn)))
        return await real_to_thread(fn, *args, **kwargs)

    monkeypatch.setattr("vimax.pipelines.idea2video_pipeline.asyncio.to_thread", recording_to_thread)

    class _Portraits:
        async def generate_front_portrait(self, character, style):
            return _BytesPortrait(b"front")

        async def generate_side_portrait(self, character, front_path, style):
            return _BytesPortrait(b"side")

        async def generate_back_portrait(self, character, front_path, style):
            return _BytesPortrait(b"back")

    pipe = _make_pipe(tmp_path, portraits=_Portraits())
    result = await pipe.generate_portraits_for_single_character(_character(), "realistic")

    assert result["船夫"]["front"]["path"].endswith("front.png")
    save_calls = [name for name in recorded if name == "save"]
    assert len(save_calls) == 3, f"三处肖像 save 须走 to_thread，实际经线程池的调用：{recorded}"
    assert (tmp_path / "character_portraits").exists()


@pytest.mark.asyncio
async def test_story_txt_write_is_atomic(tmp_path, monkeypatch):
    """story.txt 原子落盘：成功无 .tmp 残留；写盘中断不留半截文件。"""

    class _Screenwriter:
        async def develop_story(self, idea, user_requirement):
            return "很久很久以前的故事。"

    pipe = _make_pipe(tmp_path, screenwriter=_Screenwriter())
    story = await pipe.develop_story(idea="一个想法", user_requirement="30 秒")
    assert story == "很久很久以前的故事。"
    assert (tmp_path / "story.txt").read_text(encoding="utf-8") == story
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp" in p.name]
    assert not leftovers, f"成功路径不得残留临时文件：{leftovers}"

    # 失败注入：os.replace 抛错（模拟写盘中断/磁盘错）→ story.txt 不落半截
    import vimax.pipelines.idea2video_pipeline as i2v

    real_replace = __import__("os").replace

    def exploding_replace(src, dst, *args, **kwargs):
        if str(dst).endswith("story.txt"):
            raise OSError(28, "No space left on device")
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(i2v.os, "replace", exploding_replace)

    class _Screenwriter2:
        async def develop_story(self, idea, user_requirement):
            return "第二章"

    pipe2 = _make_pipe(tmp_path / "task2", screenwriter=_Screenwriter2())
    with pytest.raises(OSError):
        await pipe2.develop_story(idea="另一个", user_requirement="x")
    task2 = tmp_path / "task2"
    assert not (task2 / "story.txt").exists(), "写盘失败不得留半截 story.txt（resume 会误判已完成）"
    leftovers2 = [p.name for p in task2.iterdir() if ".tmp" in p.name]
    assert not leftovers2, f"失败路径须清理临时文件：{leftovers2}"
