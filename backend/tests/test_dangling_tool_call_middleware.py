"""Tests for DanglingToolCallMiddleware."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.middlewares.dangling_tool_call_middleware import (
    DanglingToolCallMiddleware,
)


def _ai_with_tool_calls(tool_calls):
    return AIMessage(content="", tool_calls=tool_calls)


def _tool_msg(tool_call_id, name="test_tool"):
    return ToolMessage(content="result", tool_call_id=tool_call_id, name=name)


def _tc(name="bash", tc_id="call_1"):
    return {"name": name, "id": tc_id, "args": {}}


def _invalid_tc(name="write_file", tc_id="write_file:36", error="bad args"):
    return {
        "type": "invalid_tool_call",
        "name": name,
        "id": tc_id,
        "args": "",
        "error": error,
    }


class TestBuildPatchedMessagesNoPatch:
    def test_empty_messages(self):
        mw = DanglingToolCallMiddleware()
        assert mw._build_patched_messages([]) is None

    def test_no_ai_messages(self):
        mw = DanglingToolCallMiddleware()
        msgs = [HumanMessage(content="hello")]
        assert mw._build_patched_messages(msgs) is None

    def test_ai_without_tool_calls(self):
        mw = DanglingToolCallMiddleware()
        msgs = [AIMessage(content="hello")]
        assert mw._build_patched_messages(msgs) is None

    def test_all_tool_calls_responded(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            _tool_msg("call_1", "bash"),
        ]
        assert mw._build_patched_messages(msgs) is None


class TestBuildPatchedMessagesPatching:
    def test_single_dangling_call(self):
        mw = DanglingToolCallMiddleware()
        msgs = [_ai_with_tool_calls([_tc("bash", "call_1")])]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 2
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "call_1"
        assert patched[1].status == "error"

    def test_multiple_dangling_calls_same_message(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1"), _tc("read", "call_2")]),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        # Original AI + 2 synthetic ToolMessages
        assert len(patched) == 3
        tool_msgs = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        assert {tm.tool_call_id for tm in tool_msgs} == {"call_1", "call_2"}

    def test_patch_inserted_after_offending_ai_message(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            HumanMessage(content="hi"),
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            HumanMessage(content="still here"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        # HumanMessage, AIMessage, synthetic ToolMessage, HumanMessage
        assert len(patched) == 4
        assert isinstance(patched[0], HumanMessage)
        assert isinstance(patched[1], AIMessage)
        assert isinstance(patched[2], ToolMessage)
        assert patched[2].tool_call_id == "call_1"
        assert isinstance(patched[3], HumanMessage)

    def test_mixed_responded_and_dangling(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1"), _tc("read", "call_2")]),
            _tool_msg("call_1", "bash"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        synthetic = [m for m in patched if isinstance(m, ToolMessage) and m.status == "error"]
        assert len(synthetic) == 1
        assert synthetic[0].tool_call_id == "call_2"

    def test_multiple_ai_messages_each_patched(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            HumanMessage(content="next turn"),
            _ai_with_tool_calls([_tc("read", "call_2")]),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        synthetic = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(synthetic) == 2

    def test_synthetic_message_content(self):
        mw = DanglingToolCallMiddleware()
        msgs = [_ai_with_tool_calls([_tc("bash", "call_1")])]
        patched = mw._build_patched_messages(msgs)
        tool_msg = patched[1]
        assert "interrupted" in tool_msg.content.lower()
        assert tool_msg.name == "bash"

    def test_raw_provider_tool_calls_are_patched(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            AIMessage(
                content="",
                tool_calls=[],
                additional_kwargs={
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": '{"command":"ls"}'},
                        }
                    ]
                },
            )
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 2
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "call_1"
        assert patched[1].name == "bash"
        assert patched[1].status == "error"

    def test_invalid_tool_call_is_patched(self):
        mw = DanglingToolCallMiddleware()
        msgs = [AIMessage(content="", tool_calls=[], invalid_tool_calls=[_invalid_tc()])]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 2
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "write_file:36"
        assert patched[1].name == "write_file"
        assert patched[1].status == "error"
        assert "arguments were invalid" in patched[1].content
        assert "bad args" in patched[1].content

    def test_valid_and_invalid_tool_calls_are_both_patched(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            AIMessage(
                content="",
                tool_calls=[_tc("bash", "call_1")],
                invalid_tool_calls=[_invalid_tc()],
            )
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        tool_msgs = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        assert {tm.tool_call_id for tm in tool_msgs} == {"call_1", "write_file:36"}

    def test_invalid_tool_call_already_responded_is_not_patched(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            AIMessage(content="", tool_calls=[], invalid_tool_calls=[_invalid_tc()]),
            _tool_msg("write_file:36", "write_file"),
        ]
        assert mw._build_patched_messages(msgs) is None


class TestBuildPatchedMessagesAdjacency:
    """Tests for tool result adjacency normalization (rebuild mode)."""

    def test_non_adjacent_tool_result_is_moved_next_to_tool_call(self):
        middleware = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            HumanMessage(content="interruption"),
            _tool_msg("call_1", "bash"),
        ]
        patched = middleware._build_patched_messages(msgs)
        assert patched is not None
        assert isinstance(patched[0], AIMessage)
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "call_1"
        assert isinstance(patched[2], HumanMessage)

    def test_multiple_tool_results_stay_grouped_after_ai_tool_call(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1"), _tc("read", "call_2")]),
            HumanMessage(content="interruption"),
            _tool_msg("call_2", "read"),
            _tool_msg("call_1", "bash"),
        ]

        patched = mw._build_patched_messages(msgs)

        assert patched is not None
        assert isinstance(patched[0], AIMessage)
        assert isinstance(patched[1], ToolMessage)
        assert isinstance(patched[2], ToolMessage)
        assert [patched[1].tool_call_id, patched[2].tool_call_id] == ["call_1", "call_2"]
        assert isinstance(patched[3], HumanMessage)

    def test_valid_adjacent_tool_results_are_unchanged(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            _tool_msg("call_1", "bash"),
            HumanMessage(content="next"),
        ]

        assert mw._build_patched_messages(msgs) is None

    def test_tool_results_are_grouped_with_their_own_ai_turn_across_multiple_ai_messages(self):
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            HumanMessage(content="interruption"),
            _ai_with_tool_calls([_tc("read", "call_2")]),
            _tool_msg("call_1", "bash"),
            _tool_msg("call_2", "read"),
        ]

        patched = mw._build_patched_messages(msgs)

        assert patched is not None
        assert isinstance(patched[0], AIMessage)
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "call_1"
        assert isinstance(patched[2], HumanMessage)
        assert isinstance(patched[3], AIMessage)
        assert isinstance(patched[4], ToolMessage)
        assert patched[4].tool_call_id == "call_2"

    def test_orphan_tool_message_is_removed_during_grouping(self):
        mw = DanglingToolCallMiddleware()
        orphan = _tool_msg("orphan_call", "orphan")
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            orphan,
            HumanMessage(content="interruption"),
            _tool_msg("call_1", "bash"),
        ]

        patched = mw._build_patched_messages(msgs)

        assert patched is not None
        assert isinstance(patched[0], AIMessage)
        assert isinstance(patched[1], ToolMessage)
        assert patched[1].tool_call_id == "call_1"
        # Orphaned ToolMessage is removed (no AIMessage references orphan_call)
        assert orphan not in patched


class TestBuildPatchedMessagesDuplicateIds:
    """Tests for handling duplicate tool_call_ids across turns (deque mode)."""

    def test_same_tool_call_id_in_different_ai_turns(self):
        mw = DanglingToolCallMiddleware()
        # Two AI turns reuse the same tool_call_id "web_search:11"
        msgs = [
            _ai_with_tool_calls([_tc("web_search", "web_search:11")]),
            _tool_msg("web_search:11", "web_search"),
            HumanMessage(content="search again"),
            _ai_with_tool_calls([_tc("web_search", "web_search:11")]),
            _tool_msg("web_search:11", "web_search"),
        ]
        # Already valid — no patching needed
        assert mw._build_patched_messages(msgs) is None

    def test_duplicate_id_with_second_dangling(self):
        mw = DanglingToolCallMiddleware()
        # First turn has result, second turn with same id is dangling
        msgs = [
            _ai_with_tool_calls([_tc("web_search", "web_search:11")]),
            _tool_msg("web_search:11", "web_search"),
            HumanMessage(content="search again"),
            _ai_with_tool_calls([_tc("web_search", "web_search:11")]),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        # Second AI message should get a synthetic ToolMessage
        synthetic = [m for m in patched if isinstance(m, ToolMessage) and m.status == "error"]
        assert len(synthetic) == 1
        assert synthetic[0].tool_call_id == "web_search:11"

    def test_duplicate_id_both_have_results(self):
        mw = DanglingToolCallMiddleware()
        # Both turns have results, but out of order
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            HumanMessage(content="next"),
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            _tool_msg("call_1", "bash"),  # second turn result
            _tool_msg("call_1", "bash"),  # first turn result
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        # Each AI message should get one ToolMessage
        tool_msgs = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2


class TestWrapModelCall:
    def test_no_patch_passthrough(self):
        mw = DanglingToolCallMiddleware()
        request = MagicMock()
        request.messages = [AIMessage(content="hello")]
        handler = MagicMock(return_value="response")

        result = mw.wrap_model_call(request, handler)

        handler.assert_called_once_with(request)
        assert result == "response"

    def test_patched_request_forwarded(self):
        mw = DanglingToolCallMiddleware()
        request = MagicMock()
        request.messages = [_ai_with_tool_calls([_tc("bash", "call_1")])]
        patched_request = MagicMock()
        request.override.return_value = patched_request
        handler = MagicMock(return_value="response")

        result = mw.wrap_model_call(request, handler)

        # Verify override was called with the patched messages
        request.override.assert_called_once()
        call_kwargs = request.override.call_args
        passed_messages = call_kwargs.kwargs["messages"]
        assert len(passed_messages) == 2
        assert isinstance(passed_messages[1], ToolMessage)
        assert passed_messages[1].tool_call_id == "call_1"

        handler.assert_called_once_with(patched_request)
        assert result == "response"


class TestBuildPatchedMessagesLoopDetectionHardStop:
    """Tests for loop detection hard-stop scenario producing orphaned ToolMessages.

    When LoopDetectionMiddleware hard-stops, it strips tool_calls from the last
    AIMessage. If ToolMessages for those calls were already in the history, they
    become orphaned (no AIMessage references their tool_call_id). These orphans
    must be removed — keeping them violates OpenAI/Anthropic causal ordering
    constraints ("tool call result does not follow tool call").
    """

    def test_hard_stop_with_orphaned_tool_message_after_cleared_ai(self):
        """After loop detection hard-stop, orphan TM must be removed."""
        mw = DanglingToolCallMiddleware()
        msgs = [
            HumanMessage(content="search again"),
            AIMessage(content="[Hard stop] I will stop.", tool_calls=[]),
            _tool_msg("call_search_2", "web_search"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 2
        assert isinstance(patched[0], HumanMessage)
        assert isinstance(patched[1], AIMessage)
        # Orphaned ToolMessage removed
        tool_msgs = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 0

    def test_hard_stop_preserves_valid_pairs_removes_orphans(self):
        """Valid AI/TM pairs preserved, only orphaned TMs removed."""
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),
            _tool_msg("call_1", "bash"),
            AIMessage(content="hard stop", tool_calls=[]),
            _tool_msg("call_2", "str_replace"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 3
        # call_1 preserved
        assert isinstance(patched[1], ToolMessage) and patched[1].tool_call_id == "call_1"
        # call_2 removed (orphan)
        tool_msg_ids = {m.tool_call_id for m in patched if isinstance(m, ToolMessage)}
        assert "call_2" not in tool_msg_ids
        assert "call_1" in tool_msg_ids

    def test_hard_stop_with_multiple_orphaned_tool_messages(self):
        """Multiple orphaned ToolMessages all removed after hard-stop."""
        mw = DanglingToolCallMiddleware()
        msgs = [
            AIMessage(content="", tool_calls=[]),
            _tool_msg("call_1", "str_replace"),
            _tool_msg("call_2", "str_replace"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        assert len(patched) == 1  # only the AIMessage remains
        assert isinstance(patched[0], AIMessage)

    def test_hard_stop_both_dangling_and_orphaned(self):
        """Both inject placeholders for dangling AND remove orphaned ToolMessages."""
        mw = DanglingToolCallMiddleware()
        msgs = [
            _ai_with_tool_calls([_tc("bash", "call_1")]),  # dangling
            AIMessage(content="hard stop", tool_calls=[]),  # stripped
            _tool_msg("call_2", "str_replace"),  # orphaned
            _ai_with_tool_calls([_tc("read", "call_3")]),  # dangling
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        tool_msgs = [m for m in patched if isinstance(m, ToolMessage)]
        assert len(tool_msgs) == 2
        tool_msg_ids = {tm.tool_call_id for tm in tool_msgs}
        assert tool_msg_ids == {"call_1", "call_3"}

    def test_hard_stop_with_existing_valid_history_then_orphan(self):
        """Full realistic scenario: prior turns work, then hard-stop creates orphan."""
        mw = DanglingToolCallMiddleware()
        msgs = [
            HumanMessage(content="search"),
            _ai_with_tool_calls([_tc("web_search", "call_s1")]),
            _tool_msg("call_s1", "web_search"),
            HumanMessage(content="search again"),
            # Loop detection replaced original AI with stripped version
            AIMessage(content="[Loop detected] Stopping.", tool_calls=[]),
            # ToolMessage for the stripped calls — now orphaned
            _tool_msg("call_s2", "web_search"),
        ]
        patched = mw._build_patched_messages(msgs)
        assert patched is not None
        # call_s1 preserved (valid pair), call_s2 removed (orphan)
        tool_msg_ids = {m.tool_call_id for m in patched if isinstance(m, ToolMessage)}
        assert "call_s1" in tool_msg_ids
        assert "call_s2" not in tool_msg_ids

    @pytest.mark.anyio
    async def test_async_no_patch(self):
        mw = DanglingToolCallMiddleware()
        request = MagicMock()
        request.messages = [AIMessage(content="hello")]
        handler = AsyncMock(return_value="response")

        result = await mw.awrap_model_call(request, handler)

        handler.assert_called_once_with(request)
        assert result == "response"

    @pytest.mark.anyio
    async def test_async_patched(self):
        mw = DanglingToolCallMiddleware()
        request = MagicMock()
        request.messages = [_ai_with_tool_calls([_tc("bash", "call_1")])]
        patched_request = MagicMock()
        request.override.return_value = patched_request
        handler = AsyncMock(return_value="response")

        result = await mw.awrap_model_call(request, handler)

        # Verify override was called with the patched messages
        request.override.assert_called_once()
        call_kwargs = request.override.call_args
        passed_messages = call_kwargs.kwargs["messages"]
        assert len(passed_messages) == 2
        assert isinstance(passed_messages[1], ToolMessage)
        assert passed_messages[1].tool_call_id == "call_1"

        handler.assert_called_once_with(patched_request)
        assert result == "response"
