import json

import pytest

from app.gateway.video_gen.course_pipeline import CoursePipelineService
from app.gateway.video_gen.storyboard import StoryboardConfig


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        class _Resp:
            content = self._content

        return _Resp()


@pytest.mark.asyncio
async def test_generate_outline_parses_json():
    llm_response = json.dumps(
        {
            "title": "新经理培训",
            "chapters": [
                {"title": "角色认知", "key_points": ["从执行到管理", "心态转变"], "estimated_minutes": 5},
                {"title": "团队建设", "key_points": ["信任建立", "目标对齐"], "estimated_minutes": 3},
            ],
            "total_estimated_minutes": 8,
        }
    )
    llm = _FakeLLM(llm_response)
    svc = CoursePipelineService()
    outline = await svc.generate_outline(llm, topic="新经理培训", target_duration_min=10)
    assert outline.title == "新经理培训"
    assert len(outline.chapters) == 2
    assert outline.chapters[0].title == "角色认知"
    assert outline.chapters[0].key_points == ["从执行到管理", "心态转变"]


@pytest.mark.asyncio
async def test_generate_outline_sends_topic_and_duration():
    class _CapturingLLM:
        def __init__(self):
            self.user_msg = ""

        async def ainvoke(self, messages):
            self.user_msg = messages[1]["content"]

            class _Resp:
                content = '{"title":"t","chapters":[],"total_estimated_minutes":0}'

            return _Resp()

    llm = _CapturingLLM()
    svc = CoursePipelineService()
    await svc.generate_outline(llm, topic="沟通技巧", target_duration_min=15)
    assert "沟通技巧" in llm.user_msg
    assert "15" in llm.user_msg


@pytest.mark.asyncio
async def test_chapter_script_to_storyboard():
    """script_to_storyboard should split paragraphs into scene groups and sentences into frames."""
    svc = CoursePipelineService()
    script_text = "领导力是影响他人的能力。它包含三个维度。\n\n首先是什么是愿景。愿景是未来的画面。"
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    sb = svc.script_to_storyboard(script_text, config=cfg, title="领导力")

    # 2 paragraphs → 2 scene groups
    assert len(sb.scene_groups) == 2
    # Paragraph 1 has 2 sentences → 2 frames
    assert len(sb.scene_groups[0].frame_indices) == 2
    # All frames have correct indices
    assert sb.scene_groups[0].frame_indices == [0, 1]
    assert sb.scene_groups[1].frame_indices[0] == 2


@pytest.mark.asyncio
async def test_script_to_storyboard_with_planner():
    """Full pipeline: script_to_storyboard then plan_course."""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod

    svc = CoursePipelineService()
    script_text = "第一句测试。第二句测试。"
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)

    sb = svc.script_to_storyboard(script_text, config=cfg, title="t")
    assert len(sb.frames) == 2

    # Now call plan_course
    llm = _FakeLLM('{"frames": [{"image_prompt": "illustration of concept one"}, {"image_prompt": "illustration of concept two"}]}')
    await planner_mod.plan_course(llm, storyboard=sb, style_context={}, chunk_size=8)  # E8: bulk 输出契约
    # 3k: image_prompt 追加质量词后缀，用 startswith 容忍
    assert sb.frames[0].image_prompt.startswith("illustration of concept one")


@pytest.mark.asyncio
async def test_full_course_pipeline_outline_to_storyboard():
    """End-to-end: outline → chapter script → storyboard with scene groups."""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod
    from app.gateway.video_gen.orchestration.script import generate_chapter_narrations

    # Step 1: outline
    outline_json = json.dumps(
        {
            "title": "高效沟通",
            "chapters": [
                {"title": "倾听技巧", "key_points": ["主动倾听", "反馈确认"], "estimated_minutes": 3},
            ],
            "total_estimated_minutes": 3,
        }
    )
    llm = _FakeLLM(outline_json)
    svc = CoursePipelineService()
    outline = await svc.generate_outline(llm, topic="高效沟通", target_duration_min=3)
    assert outline.chapters[0].title == "倾听技巧"

    # Step 2: chapter script
    script_llm = _FakeLLM("主动倾听是沟通的基础。它要求我们全神贯注。\n\n反馈确认也很重要。复述对方的话可以避免误解。")
    script = await generate_chapter_narrations(
        script_llm,
        chapter_title=outline.chapters[0].title,
        key_points=outline.chapters[0].key_points,
        target_chars=100,
    )
    assert "\n\n" in script  # has paragraphs

    # Step 3: storyboard
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    sb = svc.script_to_storyboard(script, config=cfg, title=outline.title)
    assert len(sb.scene_groups) == 2  # two paragraphs
    assert len(sb.frames) >= 2  # at least one sentence per paragraph

    # Step 4: plan_course
    plan_llm = _FakeLLM('{"frames": [{"image_prompt": "a person listening attentively"}, {"image_prompt": "two people in a meeting confirming understanding"}]}')
    await planner_mod.plan_course(plan_llm, storyboard=sb, style_context={"style_prefix": "flat illustration"})
    assert sb.frames[0].image_prompt is not None


def test_build_style_context_from_course_project():
    """build_style_context should extract style_prefix from CourseProject."""
    from app.gateway.video_gen.course_models import CourseProject

    proj = CourseProject(
        id="cp_1",
        user_id="u1",
        title="课程",
        visual_style_preset="flat illustration",
        color_palette={"primary": "#FF6B35", "secondary": "#004E89"},
        character_reference="a female teacher in her 30s",
    )
    ctx = CoursePipelineService.build_style_context(proj)
    assert "style_prefix" in ctx
    # 2026-08-01（Task 2 Step 4b）：断言由「英文字面量原样出现」改为「已翻译成中文」。
    # 旧断言 `"flat illustration" in style_prefix` 锁的正是要修的 bug ——
    # 英文 value 原样拼进生图 prompt 会把整条画面词带成英文（反馈 5）。
    # 本用例的**意图**（项目级风格确实注入了 style_prefix）不变，只是期望值变了。
    assert "扁平插画" in ctx["style_prefix"], ctx["style_prefix"]
    assert "flat illustration" not in ctx["style_prefix"], "英文字面量不得进 prompt"
    # 角色描述原样保留（用户自己写的文本，不翻译）
    assert "female teacher" in ctx["style_prefix"]
    # color_palette 是 ffmpeg eq 滤镜的数值参数，不再拼进 prompt（重复消费 + 噪声）
    assert "primary" not in ctx["style_prefix"]
    assert "#FF6B35" not in ctx["style_prefix"]


def test_build_style_context_empty_project():
    ctx = CoursePipelineService.build_style_context(None)
    assert ctx == {}
