"""Tests for memory merge utility."""

from deerflow.agents.memory.merge import merge_memories


def _make_memory(
    work: str = "",
    personal: str = "",
    top: str = "",
    recent: str = "",
    facts: list[dict] | None = None,
) -> dict:
    """Helper to create a memory dict for testing."""
    return {
        "version": "1.0",
        "lastUpdated": "2026-01-01T00:00:00Z",
        "user": {
            "workContext": {"summary": work, "updatedAt": "2026-01-01T00:00:00Z" if work else ""},
            "personalContext": {"summary": personal, "updatedAt": "2026-01-01T00:00:00Z" if personal else ""},
            "topOfMind": {"summary": top, "updatedAt": "2026-01-01T00:00:00Z" if top else ""},
        },
        "history": {
            "recentMonths": {"summary": recent, "updatedAt": "2026-01-01T00:00:00Z" if recent else ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": facts or [],
    }


class TestMergeMemories:
    """Tests for merge_memories function."""

    def test_both_empty(self):
        result = merge_memories({}, {})
        assert result == {}

    def test_global_only(self):
        global_mem = _make_memory(work="AI engineer")
        result = merge_memories(global_mem, {})
        assert result["user"]["workContext"]["summary"] == "AI engineer"

    def test_agent_only(self):
        agent_mem = _make_memory(work="Designer")
        result = merge_memories({}, agent_mem)
        assert result["user"]["workContext"]["summary"] == "Designer"

    def test_global_wins_over_agent(self):
        """Global memory should have higher priority."""
        global_mem = _make_memory(work="AI engineer", personal="prefers dark mode")
        agent_mem = _make_memory(work="Designer", personal="likes cats")
        result = merge_memories(global_mem, agent_mem)
        assert result["user"]["workContext"]["summary"] == "AI engineer"
        assert result["user"]["personalContext"]["summary"] == "prefers dark mode"

    def test_agent_fills_empty_global_fields(self):
        """Agent memory fills in fields that global doesn't have."""
        global_mem = _make_memory(work="AI engineer")  # personal is empty
        agent_mem = _make_memory(personal="likes cats")
        result = merge_memories(global_mem, agent_mem)
        assert result["user"]["workContext"]["summary"] == "AI engineer"
        assert result["user"]["personalContext"]["summary"] == "likes cats"

    def test_facts_merge_no_duplicates(self):
        global_mem = _make_memory(
            facts=[
                {"id": "f1", "content": "prefers Python", "category": "preference", "confidence": 0.9},
            ]
        )
        agent_mem = _make_memory(
            facts=[
                {"id": "f2", "content": "prefers 16:9 ratio", "category": "preference", "confidence": 0.8},
            ]
        )
        result = merge_memories(global_mem, agent_mem)
        assert len(result["facts"]) == 2

    def test_facts_deduplicate_same_content(self):
        """Same content in both should only appear once, global version wins."""
        global_mem = _make_memory(
            facts=[
                {"id": "f1", "content": "prefers dark theme", "category": "preference", "confidence": 0.9},
            ]
        )
        agent_mem = _make_memory(
            facts=[
                {"id": "f2", "content": "prefers dark theme", "category": "preference", "confidence": 0.7},
            ]
        )
        result = merge_memories(global_mem, agent_mem)
        assert len(result["facts"]) == 1
        assert result["facts"][0]["id"] == "f1"  # global version wins
        assert result["facts"][0]["confidence"] == 0.9

    def test_facts_deduplicate_case_insensitive(self):
        """Deduplication should be case-insensitive."""
        global_mem = _make_memory(
            facts=[
                {"id": "f1", "content": "Prefers Dark Theme", "category": "preference", "confidence": 0.9},
            ]
        )
        agent_mem = _make_memory(
            facts=[
                {"id": "f2", "content": "prefers dark theme", "category": "preference", "confidence": 0.7},
            ]
        )
        result = merge_memories(global_mem, agent_mem)
        assert len(result["facts"]) == 1

    def test_history_global_wins(self):
        global_mem = _make_memory(recent="Global recent summary")
        agent_mem = _make_memory(recent="Agent recent summary")
        result = merge_memories(global_mem, agent_mem)
        assert result["history"]["recentMonths"]["summary"] == "Global recent summary"

    def test_no_mutation_of_inputs(self):
        """Merge should not mutate the input dictionaries."""
        global_mem = _make_memory(
            facts=[
                {"id": "f1", "content": "fact 1", "category": "context", "confidence": 0.9},
            ]
        )
        agent_mem = _make_memory(
            facts=[
                {"id": "f2", "content": "fact 2", "category": "context", "confidence": 0.8},
            ]
        )
        global_facts_before = len(global_mem["facts"])
        agent_facts_before = len(agent_mem["facts"])
        merge_memories(global_mem, agent_mem)
        assert len(global_mem["facts"]) == global_facts_before
        assert len(agent_mem["facts"]) == agent_facts_before

    def test_empty_facts_in_both(self):
        global_mem = _make_memory()
        agent_mem = _make_memory()
        result = merge_memories(global_mem, agent_mem)
        assert result["facts"] == []

    def test_none_values_handled(self):
        """Should handle missing keys gracefully."""
        result = merge_memories({"version": "1.0"}, {"version": "1.0"})
        assert result["facts"] == []
