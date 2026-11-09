"""Scope gate on memory writes: durable preferences in, task artifacts out.

Memory is injected into every future system prompt, so it must hold things
that stay true across sessions. Without a gate it also absorbed one-off task
output — file paths under ``/mnt/user-data/outputs/`` that vanish with the
thread, "task complete" acknowledgements, byte sizes of a PPT generated once.

Two compounding harms, both visible in the production ``memory.json``:

1. The extractor assigns these artifacts confidence 0.90-0.99 — *higher* than
   genuine preferences — so when ``max_facts`` trimming sorts by confidence,
   the artifacts survive and evict the real preferences.
2. In a new session the agent asserts a file exists at a path that was
   destroyed with the old thread.

The strings below are taken verbatim from the production file so the gate is
tested against what the extractor actually produces, not invented examples.
"""

import pytest

from deerflow.agents.memory.scope import is_durable_fact

# Verbatim from backend/.deer-flow/users/default/memory.json.
TASK_ARTIFACTS = [
    "播客生成成功，输出文件包括：ai-org-podcast.mp3（约6-7分钟双主持对话音频）和ai-org-podcast-transcript.md（完整对话转录稿）",
    "python-pptx技能成功重新生成了10页PPT，文件路径：/mnt/user-data/outputs/ai-org-presentation-pptx.pptx（43KB）",
    "图像生成文件路径：/mnt/user-data/outputs/ironman-greatwall-peaches-comic.jpg",
    "Anthropic研究知识卡片文件路径：/mnt/user-data/outputs/ai-coding-skills-knowledge-card.html，采用深紫-深蓝渐变科技风格",
    "带旁白版本的光合作用科普PPT输出路径为/mnt/user-data/outputs/光合作用科普PPT_带旁白.pptx，文件大小约5.3MB",
    "升级后的带字幕光合作用科普讲解视频文件大小约4.5MB，输出路径仍为/mnt/user-data/outputs/光合作用科普讲解视频.mp4",
    "用户确认了任务完成状态，明确表示“这个任务完成了”，指从范凌AI时代组织变革访谈文章到知识卡片、播客、PPT三种形式内容转化的完整工作流程已成功结束",
    "用户已完成3页光合作用科普PPT的全流程生成，最终输出文件路径为/mnt/user-data/outputs/光合作用科普PPT.pptx",
    "已为光合作用科普讲解视频添加52条中文字幕，按语句断句、按字数比例分配时间轴",
]

# Also verbatim from production — these must survive.
DURABLE_FACTS = [
    "用户使用中文（普通话）进行交流",
    "用户在内容生成任务中偏好高效交互模式，要求AI自主确定合理默认方案，尽量减少追问，支持一次性批准完整提案后执行",
    "用户确认深紫-深蓝渐变科技风格和毛玻璃卡片布局为首选知识卡片设计风格",
    "用户对图像生成有多种风格需求，包括美式漫画、日漫、港漫等不同风格变体",
    "auto-video-flow技能使用Remotion框架，依赖Node.js v22+、Python 3（含edge-tts）、FFmpeg等工具",
    "当出现跨会话上下文丢失、无法找到之前方案或项目文件的情况时，需要主动向用户确认待推进的项目方向或请求提供源材料后再执行",
]


class TestRejectsTaskArtifacts:
    @pytest.mark.parametrize("content", TASK_ARTIFACTS)
    def test_production_artifacts_are_rejected(self, content):
        assert is_durable_fact(content, "knowledge") is False

    def test_output_path_is_rejected_regardless_of_category(self):
        content = "结果保存在 /mnt/user-data/outputs/report.pdf"
        for category in ("knowledge", "context", "behavior", "goal", "preference"):
            assert is_durable_fact(content, category) is False

    def test_thread_scoped_workspace_paths_are_rejected(self):
        assert is_durable_fact("文件写到 /mnt/user-data/workspace/draft.md", "knowledge") is False
        assert is_durable_fact("上传的文件在 /mnt/user-data/uploads/a.docx", "context") is False

    def test_completion_acknowledgements_are_rejected(self):
        assert is_durable_fact("用户确认任务完成状态", "context") is False
        assert is_durable_fact("PPT制作完成，共10页", "knowledge") is False


class TestKeepsDurableFacts:
    @pytest.mark.parametrize("content", DURABLE_FACTS)
    def test_production_durable_facts_are_kept(self, content):
        assert is_durable_fact(content, "preference") is True

    def test_corrections_are_always_kept(self):
        """Corrections encode a lesson; they outrank every other signal."""
        assert is_durable_fact("不要把产物路径当成永久事实", "correction") is True

    def test_a_correction_mentioning_a_path_is_still_kept(self):
        """The lesson matters even when the example that taught it had a path."""
        content = "上次误以为 /mnt/user-data/outputs/x.pptx 仍然存在，跨会话后该路径已销毁，需先确认"
        assert is_durable_fact(content, "correction") is True

    def test_tool_capability_knowledge_is_kept(self):
        assert is_durable_fact("auto-video-flow 依赖 FFmpeg 与 Node.js v22+", "knowledge") is True

    def test_stable_preference_with_a_number_is_kept(self):
        """Numbers alone are not artifacts — only ephemeral references are."""
        assert is_durable_fact("用户偏好每页 PPT 不超过 5 个要点", "preference") is True


class TestEdgeCases:
    def test_empty_content_is_rejected(self):
        assert is_durable_fact("", "preference") is False
        assert is_durable_fact("   ", "preference") is False

    def test_non_string_content_is_rejected(self):
        assert is_durable_fact(None, "preference") is False
        assert is_durable_fact(123, "preference") is False

    def test_missing_category_defaults_to_gated_not_bypassed(self):
        """An absent category must not become a hole in the gate."""
        assert is_durable_fact("输出文件路径：/mnt/user-data/outputs/a.mp4", None) is False


class TestGateIsAppliedOnWrite:
    """The gate must run inside ``_apply_updates``, not just exist as a helper.

    A module nobody calls is the failure mode this guards against.
    """

    def _apply(self, facts: list[dict]) -> list[dict]:
        from deerflow.agents.memory.updater import MemoryUpdater

        current = {"facts": [], "user": {}, "history": {}}
        updated = MemoryUpdater._apply_updates(
            MemoryUpdater.__new__(MemoryUpdater),
            current,
            {"newFacts": facts},
            thread_id="thread-1",
        )
        return updated["facts"]

    def test_artifact_facts_are_not_written(self):
        kept = self._apply(
            [
                {
                    "content": "图像生成文件路径：/mnt/user-data/outputs/x.jpg",
                    "category": "knowledge",
                    "confidence": 0.9,
                },
                {
                    "content": "用户偏好深紫-深蓝渐变科技风格",
                    "category": "preference",
                    "confidence": 0.85,
                },
            ]
        )

        contents = [f["content"] for f in kept]
        assert contents == ["用户偏好深紫-深蓝渐变科技风格"]

    def test_high_confidence_does_not_bypass_the_gate(self):
        """The core harm: artifacts scored higher than real preferences."""
        kept = self._apply(
            [
                {
                    "content": "带旁白版本的PPT输出路径为/mnt/user-data/outputs/a.pptx，文件大小约5.3MB",
                    "category": "knowledge",
                    "confidence": 0.99,
                },
            ]
        )

        assert kept == []

    def test_corrections_still_get_written(self):
        kept = self._apply(
            [
                {
                    "content": "跨会话后须先确认项目方向再执行",
                    "category": "correction",
                    "confidence": 0.95,
                },
            ]
        )

        assert len(kept) == 1
        assert kept[0]["category"] == "correction"
