"""Tests for content generation sub-agents (outline, quiz, script)."""

from __future__ import annotations

import pytest

from deerflow.agents.content_agents.outline import (
    OutlineState,
    _draft,
    _refine,
    _research,
    _review,
    _route_review,
    _set_final_from_draft,
    make_outline_agent,
)
from deerflow.agents.content_agents.quiz import (
    make_quiz_agent,
)
from deerflow.agents.content_agents.script import (
    make_script_agent,
)

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeContent:
    def __init__(self, text: str):
        self.content = text


class _FakeModel:
    """Returns canned responses based on prompt content."""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ["RESEARCH", "DRAFT", "APPROVED score=22", "REFINE"]
        self._idx = 0

    def invoke(self, messages, *, config=None):
        text = self._responses[min(self._idx, len(self._responses) - 1)]
        self._idx += 1
        return _FakeContent(text)


# ---------------------------------------------------------------------------
# Unit tests: node functions
# ---------------------------------------------------------------------------


def test_route_review_approved():
    state = {"review_feedback": "Score 23/25. APPROVED."}
    assert _route_review(state) == "approved"


def test_route_review_needs_fix():
    state = {"review_feedback": "Score 15/25. NEEDS_REVISION."}
    assert _route_review(state) == "needs_fix"


def test_route_review_empty():
    state = {"review_feedback": ""}
    assert _route_review(state) == "needs_fix"


def test_set_final_from_draft():
    state = {"draft": "## My Outline\n- Point 1"}
    result = _set_final_from_draft(state)
    assert result == {"final": "## My Outline\n- Point 1"}


def test_set_final_from_draft_empty():
    state = {}
    result = _set_final_from_draft(state)
    assert result == {"final": ""}


# ---------------------------------------------------------------------------
# Unit tests: graph structure
# ---------------------------------------------------------------------------


def test_outline_graph_structure():
    """Verify the outline graph compiles and has expected nodes."""
    graph = make_outline_agent(config={})
    node_names = set(graph.nodes.keys())
    expected = {"research", "draft", "review", "refine", "set_final", "__start__"}
    assert expected == node_names


def test_quiz_graph_structure():
    graph = make_quiz_agent(config={})
    node_names = set(graph.nodes.keys())
    expected = {"research", "draft", "review", "refine", "set_final", "__start__"}
    assert expected == node_names


def test_script_graph_structure():
    graph = make_script_agent(config={})
    node_names = set(graph.nodes.keys())
    expected = {"research", "draft", "review", "refine", "set_final", "__start__"}
    assert expected == node_names


# ---------------------------------------------------------------------------
# Unit tests: state types
# ---------------------------------------------------------------------------


def test_outline_state_fields():
    """The compiled outline graph's state schema carries the documented fields."""
    graph = make_outline_agent(config={})
    assert set(graph.builder.channels.keys()) == {
        "topic",
        "audience",
        "duration_min",
        "style",
        "research_notes",
        "draft",
        "review_feedback",
        "final",
    }


def test_quiz_state_fields():
    """The compiled quiz graph's state schema carries the documented fields."""
    graph = make_quiz_agent(config={})
    assert set(graph.builder.channels.keys()) == {
        "content",
        "num_questions",
        "difficulty",
        "language",
        "research_notes",
        "draft",
        "review_feedback",
        "final",
    }


def test_script_state_fields():
    """The compiled script graph's state schema carries the documented fields."""
    graph = make_script_agent(config={})
    assert set(graph.builder.channels.keys()) == {
        "content",
        "tone",
        "pace",
        "language",
        "research_notes",
        "draft",
        "review_feedback",
        "final",
    }


# ---------------------------------------------------------------------------
# Unit tests: node functions with mocked LLM
# ---------------------------------------------------------------------------


def test_research_node(monkeypatch):
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(["Research notes about Python"])
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    state: OutlineState = {"topic": "Python", "audience": "beginners", "duration_min": 30}
    result = _research(state, config={})
    assert "research_notes" in result
    assert result["research_notes"] == "Research notes about Python"


def test_draft_node(monkeypatch):
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(["## Draft Outline\n- Point 1"])
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    state: OutlineState = {
        "topic": "Python",
        "audience": "beginners",
        "duration_min": 30,
        "research_notes": "Some research",
    }
    result = _draft(state, config={})
    assert "draft" in result
    assert "Draft Outline" in result["draft"]


def test_review_node_approved(monkeypatch):
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(["APPROVED — Score 22/25."])
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    state: OutlineState = {"draft": "Some draft content"}
    result = _review(state, config={})
    assert "APPROVED" in result["review_feedback"]


def test_refine_node(monkeypatch):
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(["## Refined Outline\n- Better points"])
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    state: OutlineState = {
        "topic": "Python",
        "audience": "beginners",
        "duration_min": 30,
        "draft": "Old draft",
        "review_feedback": "NEEDS_REVISION — add more examples.",
    }
    result = _refine(state, config={})
    assert "final" in result
    assert "Refined" in result["final"]


# ---------------------------------------------------------------------------
# Integration: full graph invocation (mocked LLM)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_outline_graph_approved_path(monkeypatch):
    """When review approves, final comes from set_final (draft copy)."""
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(
        [
            "Research notes",
            "## Draft Outline\n- Point 1\n- Point 2",
            "Score 22/25. APPROVED.",
        ]
    )
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    graph = make_outline_agent(config={})
    result = await graph.ainvoke(
        {
            "topic": "Python",
            "audience": "beginners",
            "duration_min": 30,
        }
    )
    assert "final" in result
    assert "Draft Outline" in result["final"]


@pytest.mark.asyncio
async def test_outline_graph_needs_revision_path(monkeypatch):
    """When review fails, refine node is called and produces final."""
    from deerflow.agents.content_agents import outline

    fake = _FakeModel(
        [
            "Research notes",
            "## Draft Outline",
            "Score 15/25. NEEDS_REVISION.",
            "## Refined Outline\n- Better",
        ]
    )
    monkeypatch.setattr(outline, "create_chat_model", lambda **kw: fake)

    graph = make_outline_agent(config={})
    result = await graph.ainvoke(
        {
            "topic": "Python",
            "audience": "beginners",
            "duration_min": 30,
        }
    )
    assert "final" in result
    assert "Refined" in result["final"]


@pytest.mark.asyncio
async def test_quiz_graph_full_run(monkeypatch):
    from deerflow.agents.content_agents import quiz as quiz_mod

    fake = _FakeModel(
        [
            "Key concepts analysis",
            "## Quiz\n1. Question 1\n2. Question 2",
            "Score 23/25. APPROVED.",
        ]
    )
    monkeypatch.setattr(quiz_mod, "create_chat_model", lambda **kw: fake)

    graph = make_quiz_agent(config={})
    result = await graph.ainvoke(
        {
            "content": "ML basics chapter",
            "num_questions": 5,
        }
    )
    assert "final" in result
    assert "Quiz" in result["final"]


@pytest.mark.asyncio
async def test_script_graph_full_run(monkeypatch):
    from deerflow.agents.content_agents import script as script_mod

    fake = _FakeModel(
        [
            "Timing analysis",
            "## Script\nHello everyone...",
            "Score 21/25. APPROVED.",
        ]
    )
    monkeypatch.setattr(script_mod, "create_chat_model", lambda **kw: fake)

    graph = make_script_agent(config={})
    result = await graph.ainvoke(
        {
            "content": "Outline for intro",
            "tone": "energetic",
        }
    )
    assert "final" in result
    assert "Script" in result["final"]


# ---------------------------------------------------------------------------
# langgraph.json registration check
# ---------------------------------------------------------------------------


def test_langgraph_json_contains_content_agents():
    """Ensure all three content agents are registered in langgraph.json."""
    import json
    from pathlib import Path

    lg_json = Path(__file__).parent.parent / "langgraph.json"
    data = json.loads(lg_json.read_text())
    graphs = data.get("graphs", {})
    assert "outline_agent" in graphs
    assert "quiz_agent" in graphs
    assert "script_agent" in graphs
    assert graphs["outline_agent"] == "deerflow.agents.content_agents.outline:make_outline_agent"
    assert graphs["quiz_agent"] == "deerflow.agents.content_agents.quiz:make_quiz_agent"
    assert graphs["script_agent"] == "deerflow.agents.content_agents.script:make_script_agent"
