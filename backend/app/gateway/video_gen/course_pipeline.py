"""CoursePipelineService — orchestration for course video content generation.

Provides outline → chapter script → storyboard pipeline for long-form course videos.
This is a pure orchestration helper (not a BaseTaskService subclass) — it is called
by VideoGenService during draft generation or by dedicated course API endpoints.
"""

from __future__ import annotations

import json
import logging

from .course_models import CourseOutline, CourseProject, OutlineChapter
from .orchestration import _invoke_llm_bounded
from .storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

logger = logging.getLogger(__name__)


class CoursePipelineService:
    """Orchestrates the outline → script → storyboard pipeline for course videos."""

    async def generate_outline(
        self,
        llm,
        *,
        topic: str,
        target_duration_min: int = 10,
    ) -> CourseOutline:
        """Generate a course outline via LLM.

        Args:
            llm: A LangChain chat model (must have async ainvoke).
            topic: Course topic (e.g. "新经理培训").
            target_duration_min: Target total duration in minutes.

        Returns:
            CourseOutline with chapters and estimated durations.
        """
        n_chapters = max(3, min(10, target_duration_min // 3))

        system = (
            "你是培训课程设计专家。根据主题生成结构化的课程大纲。"
            f"生成 {n_chapters} 章。每章包含: 标题、3-5个知识要点、预估讲解时长（分钟）。"
            f"总时长约 {target_duration_min} 分钟。"
            '输出 STRICT JSON: {"title": "课程标题", "chapters": [{"title": "...", "key_points": ["..."], "estimated_minutes": N}], "total_estimated_minutes": N}'
        )
        user = f"主题：{topic}\n目标时长：{target_duration_min} 分钟"
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        resp = await _invoke_llm_bounded(llm, messages)  # E5: 有界超时
        content = resp.content if hasattr(resp, "content") else str(resp)
        # E6: LLM 输出格式漂移时抛领域错误（不裸 JSONDecodeError），让调用方能区分"模型格式问题"
        try:
            data = _extract_json(content)
        except (json.JSONDecodeError, ValueError) as e:
            logger.warning("generate_outline: LLM 返回非 JSON（%s）；原文片段：%r", e, content[:200])
            raise RuntimeError(f"课程大纲生成失败：LLM 返回内容不是合法 JSON（{e}）") from e
        return CourseOutline.from_dict(data)

    async def generate_chapter_script(
        self,
        llm,
        *,
        chapter: OutlineChapter,
    ) -> str:
        """Generate narration text for a single chapter.

        Delegates to script.generate_chapter_narrations.
        Returns text with paragraphs separated by blank lines.
        """
        from .orchestration.script import generate_chapter_narrations

        target_chars = chapter.estimated_minutes * 200  # ~200 words/min
        return await generate_chapter_narrations(
            llm,
            chapter_title=chapter.title,
            key_points=chapter.key_points,
            target_chars=target_chars,
        )

    def script_to_storyboard(
        self,
        script: str,
        *,
        config: StoryboardConfig,
        title: str,
    ) -> Storyboard:
        """Convert a multi-paragraph script into a Storyboard with SceneGroups.

        Each paragraph (separated by blank lines) becomes a SceneGroup.
        Each sentence within a paragraph becomes a Frame.

        Does NOT call the planner — caller should invoke plan_course() separately
        if visual prompts are needed.
        """
        from .orchestration.script import split_fixed_script

        paragraphs = [p.strip() for p in script.split("\n\n") if p.strip()]
        frames: list[StoryboardFrame] = []
        groups: list[SceneGroup] = []

        for para_idx, para in enumerate(paragraphs):
            sentences = split_fixed_script(para)
            group_frame_indices = []
            for sent in sentences:
                frame = StoryboardFrame(index=len(frames), narration=sent)
                frames.append(frame)
                group_frame_indices.append(frame.index)
            groups.append(
                SceneGroup(
                    index=para_idx,
                    title=f"段落 {para_idx + 1}",
                    frame_indices=group_frame_indices,
                )
            )

        return Storyboard(
            title=title,
            config=config,
            frames=frames,
            scene_groups=groups,
        )

    @staticmethod
    def build_style_context(project: CourseProject) -> dict:
        """Build style_context dict for plan_course() from a CourseProject."""
        return {"style_prefix": project.style_prefix} if project else {}


def _extract_json(text: str) -> dict:
    """Extract JSON from text, handling markdown fences."""
    s = text.strip()
    if "```" in s:
        parts = s.split("```")
        for part in parts:
            part = part.strip()
            if part.startswith("json"):
                part = part[4:].strip()
            if part.startswith("{"):
                try:
                    return json.loads(part)
                except json.JSONDecodeError:
                    continue
    return json.loads(s)
