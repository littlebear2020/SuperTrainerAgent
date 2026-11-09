"""Outline agent — generate structured training outlines.

Graph: research → draft → review → (refine | END)

Input state fields:
  topic, audience, duration_min, style (optional)

Output: ``final`` field contains the structured outline text.
"""

from __future__ import annotations

import logging
from typing import NotRequired

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

from deerflow.models import create_chat_model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------


class OutlineState(TypedDict, total=False):
    topic: str
    audience: str
    duration_min: int
    style: NotRequired[str]

    # Internal nodes
    research_notes: NotRequired[str]
    draft: NotRequired[str]
    review_feedback: NotRequired[str]
    final: NotRequired[str]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = "你是一位专业的培训课程设计师。你的输出应使用清晰的 Markdown 格式，包含层级标题。"

_RESEARCH_PROMPT = """\
请为以下培训主题提供教学设计的参考信息：

主题：{topic}
目标受众：{audience}
预计时长：{duration_min} 分钟

请列出：
1. 该主题的核心知识模块
2. 适合该受众的教学策略
3. 建议的时间分配

仅输出参考信息，不要生成完整大纲。"""

_DRAFT_PROMPT = """\
根据以下参考信息，生成一份结构化的培训大纲。

主题：{topic}
目标受众：{audience}
预计时长：{duration_min} 分钟
{style_line}

要求：
- 使用 Markdown 层级标题（## 章节, ### 小节）
- 每个小节包含要点（bullet points）
- 标注每个章节的建议时长
- 包含互动环节或练习建议

参考信息：
{research_notes}

请直接输出培训大纲："""

_REVIEW_PROMPT = """\
请审查以下培训大纲，评估其完整性和教学效果：

---
{draft}
---

评估维度（每项 1-5 分）：
1. 内容完整性
2. 逻辑结构
3. 时长分配合理性
4. 互动性设计
5. 受众匹配度

请先给出总分（/25），然后列出需要改进的具体问题。
如果总分 >= 20，请输出 "APPROVED"。
如果总分 < 20，请输出 "NEEDS_REVISION" 并说明需要修改的地方。"""

_REFINE_PROMPT = """\
根据以下审查反馈，修改培训大纲：

原始大纲：
---
{draft}
---

审查反馈：
---
{review_feedback}
---

主题：{topic}
目标受众：{audience}
预计时长：{duration_min} 分钟

请输出修改后的完整培训大纲："""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _research(state: OutlineState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _RESEARCH_PROMPT.format(
        topic=state["topic"],
        audience=state["audience"],
        duration_min=state.get("duration_min", 60),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"research_notes": result.content}


def _draft(state: OutlineState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    style_line = f"风格要求：{state['style']}" if state.get("style") else ""
    prompt = _DRAFT_PROMPT.format(
        topic=state["topic"],
        audience=state["audience"],
        duration_min=state.get("duration_min", 60),
        style_line=style_line,
        research_notes=state.get("research_notes", ""),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"draft": result.content}


def _review(state: OutlineState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REVIEW_PROMPT.format(draft=state.get("draft", ""))
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"review_feedback": result.content}


def _refine(state: OutlineState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REFINE_PROMPT.format(
        draft=state.get("draft", ""),
        review_feedback=state.get("review_feedback", ""),
        topic=state["topic"],
        audience=state["audience"],
        duration_min=state.get("duration_min", 60),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"final": result.content}


def _route_review(state: OutlineState) -> str:
    feedback = state.get("review_feedback", "")
    if "APPROVED" in feedback:
        return "approved"
    return "needs_fix"


def _set_final_from_draft(state: OutlineState) -> dict:
    return {"final": state.get("draft", "")}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def make_outline_agent(config: RunnableConfig) -> StateGraph:
    """Build and compile the outline generation graph."""
    g = StateGraph(OutlineState)
    g.add_node("research", _research)
    g.add_node("draft", _draft)
    g.add_node("review", _review)
    g.add_node("refine", _refine)
    g.add_node("set_final", _set_final_from_draft)

    g.set_entry_point("research")
    g.add_edge("research", "draft")
    g.add_edge("draft", "review")
    g.add_conditional_edges(
        "review",
        _route_review,
        {"approved": "set_final", "needs_fix": "refine"},
    )
    g.add_edge("set_final", END)
    g.add_edge("refine", END)
    return g.compile()
