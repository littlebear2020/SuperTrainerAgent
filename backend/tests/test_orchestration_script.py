import pytest

from app.gateway.video_gen.orchestration import script as script_mod


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        class _Resp:
            content = self._content

        return _Resp()


@pytest.mark.asyncio
async def test_generate_narrations_from_topicparses_lines():
    llm = _FakeLLM("1. 第一句旁白\n2. 第二句旁白\n3. 第三句旁白")
    narrations = await script_mod.generate_narrations(llm, topic="金钱的作用", n_scenes=3)
    assert narrations == ["第一句旁白", "第二句旁白", "第三句旁白"]


@pytest.mark.asyncio
async def test_generate_narrations_strips_bullets_and_blanks():
    llm = _FakeLLM("- A\n\n• B\n  \n1) C")
    narrations = await script_mod.generate_narrations(llm, topic="x", n_scenes=3)
    assert narrations == ["A", "B", "C"]


def test_split_fixed_script_by_sentence():
    text = "第一句。第二句！第三句？"
    out = script_mod.split_fixed_script(text)
    assert out == ["第一句。", "第二句！", "第三句？"]


def test_split_fixed_script_by_paragraph():
    text = "段落一\n\n段落二\n\n段落三"
    out = script_mod.split_fixed_script(text, split_mode="paragraph")
    assert out == ["段落一", "段落二", "段落三"]


@pytest.mark.asyncio
async def test_generate_chapter_narrations_returns_paragraphs():
    """generate_chapter_narrations should return text with paragraphs separated by blank lines."""
    llm = _FakeLLM("大家好，今天我们来聊聊领导力。\n\n首先，什么是领导力？\n领导力是影响他人的能力。\n\n最后，总结一下。")
    result = await script_mod.generate_chapter_narrations(
        llm,
        chapter_title="领导力入门",
        key_points=["领导力的定义", "领导力的三个维度"],
        target_chars=100,
    )
    assert "领导力" in result
    assert "\n\n" in result  # paragraphs separated by blank lines


@pytest.mark.asyncio
async def test_generate_chapter_narrations_includes_key_points():
    """The LLM prompt should include the chapter title and key points."""

    class _CapturingLLM:
        def __init__(self):
            self.received_messages = None

        async def ainvoke(self, messages):
            self.received_messages = messages

            class _Resp:
                content = "段落一\n\n段落二"

            return _Resp()

    llm = _CapturingLLM()
    await script_mod.generate_chapter_narrations(
        llm,
        chapter_title="高效沟通",
        key_points=["倾听技巧", "非暴力沟通"],
        target_chars=200,
    )
    user_msg = llm.received_messages[1]["content"]
    assert "高效沟通" in user_msg
    assert "倾听技巧" in user_msg


# ---------------------------------------------------------------------------
# Stage 1.3 regression: doubao reasoning_content must not leak into narrations.
# doubao returns content (clean answer) + reasoning_content (thinking, surfaced
# by langchain in additional_kwargs, SEPARATE from .content). generate_narrations
# uses `resp.content` only — the thinking text must never enter parsed narrations.
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 1.3 验收闸门.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_generate_narrations_ignores_doubao_reasoning_content():
    """doubao-style response with reasoning_content in additional_kwargs — narrations
    come from .content only; thinking text must not leak."""
    thinking = "让我想想旁白结构，先拆成三段……这部分是思考链不应进入正文。"

    class _DoubaoLLM:
        async def ainvoke(self, messages):
            class _Resp:
                content = "1. 第一句旁白\n2. 第二句旁白"
                additional_kwargs = {"reasoning_content": thinking}

            return _Resp()

    narrations = await script_mod.generate_narrations(_DoubaoLLM(), topic="x", n_scenes=2)
    assert narrations == ["第一句旁白", "第二句旁白"]
    assert all(thinking not in n for n in narrations), "reasoning_content leaked into narrations"


def test_split_fixed_script_caps_explosion_without_target():
    """S2: 无 target 的长脚本（50+ 句）须有帧数上限，防爆爆炸。"""
    from app.gateway.video_gen.orchestration.script import split_fixed_script

    text = "。".join(f"第{i}句话的正文内容" for i in range(50)) + "。"
    parts = split_fixed_script(text)  # 无 target_count
    assert len(parts) <= 30, f"长脚本无 target 须 ≤30 帧（防爆），got {len(parts)}"


def test_split_fixed_script_short_script_not_capped():
    """S2 反向：短脚本（10 句）无 target 仍 1:1（不被误并）。"""
    from app.gateway.video_gen.orchestration.script import split_fixed_script

    text = "。".join(f"第{i}句话" for i in range(10)) + "。"
    parts = split_fixed_script(text)
    assert len(parts) == 10, f"短脚本无 target 须保持 1:1（10 帧），got {len(parts)}"


def test_strip_visual_markers_removes_markdown_title():
    """S3: markdown 标题 # 被剥（不当台词）。"""
    from app.gateway.video_gen.orchestration.script import _strip_visual_markers

    text = "# 公众号文章标题\n正文第一句。"
    cleaned = _strip_visual_markers(text)
    assert "公众号文章标题" not in cleaned, "markdown 标题应被剥"


def test_strip_visual_markers_removes_wechat_metadata():
    """S3: 公众号元信息行被剥。"""
    from app.gateway.video_gen.orchestration.script import _strip_visual_markers

    text = "作者：张三\n阅读 10086\n正文。"
    cleaned = _strip_visual_markers(text)
    assert "作者：张三" not in cleaned
    assert "正文" in cleaned


def test_strip_visual_markers_removes_bare_page_numbers():
    """S3: 裸页码被剥。"""
    from app.gateway.video_gen.orchestration.script import _strip_visual_markers

    text = "第3页\n正文。"
    cleaned = _strip_visual_markers(text)
    assert "第3页" not in cleaned


def test_strip_visual_markers_preserves_legit_hash_content():
    """S3 反向：含 # 的正常内容不被误伤。"""
    from app.gateway.video_gen.orchestration.script import _strip_visual_markers

    text = "C# 是一门语言。编号#1很重要。"
    cleaned = _strip_visual_markers(text)
    assert "C#" in cleaned and "#1" in cleaned, "合法内容含 # 不应被剥"
