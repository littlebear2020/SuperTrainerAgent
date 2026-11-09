"""Script agent — generate narrated scripts from outlines or PPT content.

Graph: research → draft → review → (refine | END)

Input state fields:
  content, tone (optional), pace (optional), language (optional)

Output: ``final`` field contains the narrated script text.
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


class ScriptState(TypedDict, total=False):
    content: str  # Outline or PPT content
    tone: NotRequired[str]  # conversational / formal / energetic
    pace: NotRequired[str]  # slow / normal / fast
    language: NotRequired[str]  # zh-CN / en-US

    # Internal nodes
    research_notes: NotRequired[str]
    draft: NotRequired[str]
    review_feedback: NotRequired[str]
    final: NotRequired[str]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = "你是一位专业的培训口播稿撰写人。你的稿件应该适合口语表达，自然流畅，包含适当的停顿和强调标记。"

_RESEARCH_PROMPT = """\
请分析以下培训内容，为撰写口播稿做准备：

---
{content}
---

请分析：
1. 每个章节/要点需要多少口播时间（按中等语速 200 字/分钟估算）
2. 哪些内容需要举例说明
3. 哪些地方适合设置互动提问或停顿
4. 建议的开场和结尾方式

仅输出分析结果。"""

_DRAFT_PROMPT = """\
根据以下培训内容和口播分析，撰写一份口播稿。

原始内容：
---
{content}
---

口播分析：
{research_notes}

要求：
- 语气：{tone}
- 语速：{pace}
- 语言：{language}
- 使用 Markdown 格式
- 用 「...」 标注需要强调的关键词
- 用 「[停顿 2 秒]」 等标记停顿
- 用 「[过渡]」 标注章节过渡
- 包含开场白和结束语

请直接输出口播稿："""

_REVIEW_PROMPT = """\
请审查以下培训口播稿，评估其口语化程度和教学效果：

---
{draft}
---

评估维度（每项 1-5 分）：
1. 口语化程度（是否适合朗读）
2. 内容完整性
3. 节奏感（停顿和过渡是否自然）
4. 时长合理性
5. 教学效果（能否帮助听众理解和记忆）

请先给出总分（/25），然后列出需要改进的具体问题。
如果总分 >= 20，请输出 "APPROVED"。
如果总分 < 20，请输出 "NEEDS_REVISION" 并说明需要修改的地方。"""

_REFINE_PROMPT = """\
根据以下审查反馈，修改口播稿：

原始口播稿：
---
{draft}
---

审查反馈：
---
{review_feedback}
---

原始内容：
---
{content}
---

语气：{tone}
语速：{pace}
语言：{language}

请输出修改后的完整口播稿："""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _research(state: ScriptState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _RESEARCH_PROMPT.format(content=state["content"])
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"research_notes": result.content}


def _draft(state: ScriptState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _DRAFT_PROMPT.format(
        content=state["content"],
        research_notes=state.get("research_notes", ""),
        tone=state.get("tone", "亲切自然"),
        pace=state.get("pace", "中等"),
        language=state.get("language", "中文"),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"draft": result.content}


def _review(state: ScriptState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REVIEW_PROMPT.format(draft=state.get("draft", ""))
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"review_feedback": result.content}


def _refine(state: ScriptState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REFINE_PROMPT.format(
        draft=state.get("draft", ""),
        review_feedback=state.get("review_feedback", ""),
        content=state["content"],
        tone=state.get("tone", "亲切自然"),
        pace=state.get("pace", "中等"),
        language=state.get("language", "中文"),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"final": result.content}


def _route_review(state: ScriptState) -> str:
    feedback = state.get("review_feedback", "")
    if "APPROVED" in feedback:
        return "approved"
    return "needs_fix"


def _set_final_from_draft(state: ScriptState) -> dict:
    return {"final": state.get("draft", "")}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def make_script_agent(config: RunnableConfig) -> StateGraph:
    """Build and compile the script generation graph."""
    g = StateGraph(ScriptState)
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
