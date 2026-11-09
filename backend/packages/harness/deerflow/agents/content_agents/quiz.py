"""Quiz agent — generate multiple-choice and short-answer questions.

Graph: research → draft → review → (refine | END)

Input state fields:
  content, num_questions (optional), difficulty (optional), language (optional)

Output: ``final`` field contains the quiz text with answers.
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


class QuizState(TypedDict, total=False):
    content: str
    num_questions: NotRequired[int]
    difficulty: NotRequired[str]  # easy / medium / hard
    language: NotRequired[str]  # zh-CN / en-US

    # Internal nodes
    research_notes: NotRequired[str]
    draft: NotRequired[str]
    review_feedback: NotRequired[str]
    final: NotRequired[str]


# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

_SYSTEM = "你是一位专业的培训评估设计师。你擅长设计高质量的测验题目，包括选择题和简答题。"

_RESEARCH_PROMPT = """\
请分析以下培训内容，识别适合出题的知识点：

---
{content}
---

请列出：
1. 关键概念和定义（适合出选择题）
2. 需要理解的原理解释（适合出简答题）
3. 容易混淆的知识点（适合做干扰选项）

仅输出分析结果。"""

_DRAFT_PROMPT = """\
根据以下内容和知识点分析，设计一套培训测验题。

原始内容：
---
{content}
---

知识点分析：
{research_notes}

要求：
- 共 {num_questions} 道题
- 难度：{difficulty}
- 语言：{language}
- 题型分布：约 70% 选择题（含 4 个选项），30% 简答题
- 每道选择题标注正确答案和解析
- 每道简答题提供参考答案要点
- 使用 Markdown 格式

请直接输出测验题："""

_REVIEW_PROMPT = """\
请审查以下培训测验题，评估其质量：

---
{draft}
---

评估维度（每项 1-5 分）：
1. 题目准确性（答案是否正确）
2. 覆盖度（是否覆盖关键知识点）
3. 难度适当性
4. 干扰选项质量（选择题）
5. 答案解析清晰度

请先给出总分（/25），然后列出需要改进的具体问题。
如果总分 >= 20，请输出 "APPROVED"。
如果总分 < 20，请输出 "NEEDS_REVISION" 并说明需要修改的地方。"""

_REFINE_PROMPT = """\
根据以下审查反馈，修改测验题：

原始测验：
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

请输出修改后的完整测验题："""


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------


def _research(state: QuizState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _RESEARCH_PROMPT.format(content=state["content"])
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"research_notes": result.content}


def _draft(state: QuizState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _DRAFT_PROMPT.format(
        content=state["content"],
        research_notes=state.get("research_notes", ""),
        num_questions=state.get("num_questions", 10),
        difficulty=state.get("difficulty", "medium"),
        language=state.get("language", "中文"),
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"draft": result.content}


def _review(state: QuizState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REVIEW_PROMPT.format(draft=state.get("draft", ""))
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"review_feedback": result.content}


def _refine(state: QuizState, config: RunnableConfig) -> dict:
    model = create_chat_model(thinking_enabled=False)
    prompt = _REFINE_PROMPT.format(
        draft=state.get("draft", ""),
        review_feedback=state.get("review_feedback", ""),
        content=state["content"],
    )
    result = model.invoke(
        [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": prompt}],
        config=config,
    )
    return {"final": result.content}


def _route_review(state: QuizState) -> str:
    feedback = state.get("review_feedback", "")
    if "APPROVED" in feedback:
        return "approved"
    return "needs_fix"


def _set_final_from_draft(state: QuizState) -> dict:
    return {"final": state.get("draft", "")}


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------


def make_quiz_agent(config: RunnableConfig) -> StateGraph:
    """Build and compile the quiz generation graph."""
    g = StateGraph(QuizState)
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
