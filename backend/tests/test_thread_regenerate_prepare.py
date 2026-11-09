"""Tests for the regenerate / edit-regenerate ``/prepare`` endpoints.

Ported subset of upstream ``test_thread_regenerate_prepare.py``. Local
adaptation: the gateway has no ``CheckpointStateAccessor`` / delta
materialization; checkpoint reads go through the raw-checkpointer accessor
(``app.gateway.services.build_thread_checkpoint_state_accessor``), which the
tests drive via ``app.state.checkpointer`` instead of patching an accessor
builder.

Skipped upstream cases (documented in the task report):
- wait-run materialized-values readers (local ``wait_run`` is a different,
  untouched raw-checkpointer implementation);
- dynamic-context ``__user`` id-swap flows (middleware does not exist locally;
  the strip helper itself is covered by a focused unit test);
- delta-materialized history (no delta mode locally).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.runtime import RunStatus
from deerflow.utils.messages import ORIGINAL_USER_CONTENT_KEY

# ---------------------------------------------------------------------------
# Fixtures (raw-checkpointer adapted)
# ---------------------------------------------------------------------------


def _checkpoint(
    checkpoint_id: str,
    messages: list[object],
    *,
    metadata: dict | None = None,
    goal: dict | None = None,
    next_tasks: tuple[str, ...] = (),
):
    channel_values = {"messages": messages}
    if goal is not None:
        channel_values["goal"] = goal
    return SimpleNamespace(
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
                "checkpoint_map": None,
            }
        },
        checkpoint={"channel_values": channel_values},
        metadata=metadata or {},
        next=next_tasks,
    )


class FakeCheckpointer:
    """Raw checkpointer fake matching the langgraph saver read surface."""

    def __init__(self, history, *, latest=None):
        self.history = history
        self.latest = latest
        self.alist_limits = []

    async def aget_tuple(self, config):
        checkpoint_id = config.get("configurable", {}).get("checkpoint_id")
        if checkpoint_id:
            return next((item for item in self.history if item.config["configurable"]["checkpoint_id"] == checkpoint_id), None)
        return self.latest or (self.history[0] if self.history else None)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        self.alist_limits.append(limit)
        for item in self.history[:limit]:
            yield item


def _snapshot(checkpoint_id: str, messages: list[object], *, metadata: dict | None = None):
    return SimpleNamespace(
        values={"messages": messages},
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": checkpoint_id,
                "checkpoint_map": None,
            }
        },
        metadata=metadata or {},
    )


class FakeEventStore:
    def __init__(self, rows):
        self.rows = rows

    async def list_messages(self, thread_id, *, limit=50, before_seq=None, after_seq=None):
        return self.rows[-limit:]


class FakeRunManager:
    def __init__(self, records):
        self.records = records

    async def list_by_thread(self, thread_id, *, user_id=None, limit=100):
        return self.records[:limit]

    async def get(self, run_id, *, user_id=None):
        return next((record for record in self.records if record.run_id == run_id), None)


def _request(checkpointer, event_store, *, run_manager=None):
    thread_store = MagicMock()
    thread_store.get = AsyncMock(return_value=None)
    return SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                checkpointer=checkpointer,
                run_event_store=event_store,
                run_manager=run_manager or FakeRunManager([]),
                thread_store=thread_store,
            )
        ),
    )


@pytest.fixture(autouse=True)
def _stub_current_user(monkeypatch):
    """Local ``get_current_user`` reads JWT cookies; stub it for direct calls."""
    from app.gateway.routers import thread_runs

    monkeypatch.setattr(thread_runs, "get_current_user", AsyncMock(return_value="user-1"))


def _answer_run_fixtures() -> tuple[FakeEventStore, FakeRunManager]:
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer v1"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    run_manager = FakeRunManager([SimpleNamespace(run_id="run-old", status=RunStatus.success, metadata={}, last_ai_message="answer v1")])
    return event_store, run_manager


# ---------------------------------------------------------------------------
# Endpoint-level tests (HTTP surface)
# ---------------------------------------------------------------------------


def _make_app(checkpointer, event_store, run_manager=None):
    from app.gateway.routers import thread_runs

    app = make_authed_test_app()
    app.include_router(thread_runs.router)
    app.state.checkpointer = checkpointer
    app.state.run_event_store = event_store
    app.state.run_manager = run_manager or FakeRunManager([])
    return app


def test_prepare_regenerate_endpoint_returns_payload():
    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer v1")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [human, ai]),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer v1"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    app = _make_app(checkpointer, event_store)

    with TestClient(app) as client:
        response = client.post("/api/threads/thread-1/runs/regenerate/prepare", json={"message_id": "ai-1"})

    assert response.status_code == 200
    body = response.json()
    assert body["checkpoint"] == {"checkpoint_ns": "", "checkpoint_id": "ckpt-base", "checkpoint_map": None}
    assert body["target_run_id"] == "run-old"
    assert body["metadata"] == {
        "regenerate_from_message_id": "ai-1",
        "regenerate_from_run_id": "run-old",
        "regenerate_checkpoint_id": "ckpt-base",
    }
    assert body["input"]["messages"][0]["id"] == "human-1"


def test_prepare_regenerate_endpoint_missing_message_returns_404():
    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer v1")
    checkpointer = FakeCheckpointer([_checkpoint("ckpt-ai", [human, ai])])
    app = _make_app(checkpointer, FakeEventStore([]))

    with TestClient(app) as client:
        response = client.post("/api/threads/thread-1/runs/regenerate/prepare", json={"message_id": "no-such-message"})

    assert response.status_code == 404
    # The handler (not a missing route) produced the 404.
    assert response.json() == {"detail": "Message no-such-message not found"}


def test_prepare_edit_regenerate_endpoint_returns_payload():
    human = HumanMessage(id="human-1", content="original question")
    ai = AIMessage(id="ai-1", content="answer v1")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [human, ai]),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )
    event_store, run_manager = _answer_run_fixtures()
    app = _make_app(checkpointer, event_store, run_manager)

    with TestClient(app) as client:
        response = client.post(
            "/api/threads/thread-1/runs/edit-regenerate/prepare",
            json={"human_message_id": "human-1", "replacement_text": "edited question"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["checkpoint"]["checkpoint_id"] == "ckpt-base"
    assert body["target_run_id"] == "run-old"
    assert body["metadata"]["replay_kind"] == "edit"
    assert body["replacement_human_message_id"] != "human-1"
    assert body["source_message_ids"] == ["human-1", "ai-1"]
    assert body["input"]["messages"][0]["content"] == [{"type": "text", "text": "edited question"}]


# ---------------------------------------------------------------------------
# Regenerate payload
# ---------------------------------------------------------------------------


def test_prepare_regenerate_payload_returns_clean_input_and_base_checkpoint():
    from app.gateway.routers import thread_runs

    human = HumanMessage(
        id="human-1",
        content="<uploaded_files>injected</uploaded_files>\n\n/data-analysis analyze data.csv",
        additional_kwargs={
            ORIGINAL_USER_CONTENT_KEY: "/data-analysis analyze data.csv",
            "files": [{"filename": "data.csv", "path": "/mnt/user-data/uploads/data.csv"}],
        },
    )
    ai = AIMessage(id="ai-1", content="answer v1")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer v1"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )

    original_builder = thread_runs.build_thread_checkpoint_state_accessor
    thread_builder = AsyncMock(side_effect=original_builder)
    with patch.object(thread_runs, "build_thread_checkpoint_state_accessor", thread_builder):
        response = asyncio.run(thread_runs._prepare_regenerate_payload("thread-1", "ai-1", _request(checkpointer, event_store)))

    # Both the latest-checkpoint read and the replay-base resolution resolve
    # through the thread accessor.
    assert [call.kwargs["thread_id"] for call in thread_builder.await_args_list] == ["thread-1", "thread-1"]

    assert response.checkpoint == {
        "checkpoint_ns": "",
        "checkpoint_id": "ckpt-base",
        "checkpoint_map": None,
    }
    assert response.target_run_id == "run-old"
    assert response.metadata == {
        "regenerate_from_message_id": "ai-1",
        "regenerate_from_run_id": "run-old",
        "regenerate_checkpoint_id": "ckpt-base",
    }
    assert "title" not in response.input
    regenerated_human = response.input["messages"][0]
    assert regenerated_human["id"] == "human-1"
    assert regenerated_human["content"] == [{"type": "text", "text": "/data-analysis analyze data.csv"}]
    assert regenerated_human["additional_kwargs"] == {"files": [{"filename": "data.csv", "path": "/mnt/user-data/uploads/data.csv"}]}


def test_prepare_regenerate_payload_preserves_latest_thread_title():
    from app.gateway.routers import thread_runs

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer v1")
    base = _checkpoint("ckpt-base", [])
    latest = _checkpoint("ckpt-ai", [human, ai])
    latest.checkpoint["channel_values"]["title"] = "User renamed title"
    checkpointer = FakeCheckpointer([latest, base])
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer v1"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )

    response = asyncio.run(
        thread_runs._prepare_regenerate_payload(
            "thread-1",
            "ai-1",
            _request(checkpointer, event_store),
        )
    )

    assert response.checkpoint["checkpoint_id"] == "ckpt-base"
    assert response.input["title"] == "User renamed title"


def test_prepare_regenerate_payload_supports_latest_interrupted_response_missing_from_checkpoint():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(
        id="human-1",
        content="question",
        additional_kwargs={"run_id": "run-interrupted"},
    )
    base = _checkpoint("ckpt-base", [])
    latest = _checkpoint("ckpt-human", [human])
    checkpointer = FakeCheckpointer([latest, base])
    run_manager = FakeRunManager(
        [
            SimpleNamespace(
                run_id="run-interrupted",
                thread_id="thread-1",
                status=RunStatus.interrupted,
            )
        ]
    )

    response = asyncio.run(
        _prepare_regenerate_payload(
            "thread-1",
            "lc_run--partial-response",
            _request(
                checkpointer,
                FakeEventStore([]),
                run_manager=run_manager,
            ),
        )
    )

    assert response.checkpoint["checkpoint_id"] == "ckpt-base"
    assert response.target_run_id == "run-interrupted"
    assert response.metadata == {
        "regenerate_from_message_id": "lc_run--partial-response",
        "regenerate_from_run_id": "run-interrupted",
        "regenerate_checkpoint_id": "ckpt-base",
    }
    assert response.input["messages"][0]["id"] == "human-1"


@pytest.mark.parametrize(
    ("status", "record_thread_id"),
    [
        (RunStatus.success, "thread-1"),
        (RunStatus.interrupted, "another-thread"),
    ],
)
def test_prepare_regenerate_payload_does_not_accept_unverified_missing_response(
    status: RunStatus,
    record_thread_id: str,
):
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(
        id="human-1",
        content="question",
        additional_kwargs={"run_id": "source-run"},
    )
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )
    run_manager = FakeRunManager(
        [
            SimpleNamespace(
                run_id="source-run",
                thread_id=record_thread_id,
                status=status,
            )
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_regenerate_payload(
                "thread-1",
                "missing-response",
                _request(
                    checkpointer,
                    FakeEventStore([]),
                    run_manager=run_manager,
                ),
            )
        )

    assert exc.value.status_code == 404
    assert exc.value.detail == "Message missing-response not found"


def test_prepare_regenerate_payload_rejects_thread_without_replay_base():
    """A thread whose first checkpoint already contains the target turn has no
    addressable replay base: 409, and the checkpoint history stays untouched.

    Adapted from the upstream legacy-branch cases, which seeded the same shape
    (single root checkpoint holding human + ai) through the branch feature; the
    assertion here is branch-agnostic and uses a real ``InMemorySaver`` so the
    raw accessor + lineage walk run against a genuine langgraph saver.
    """
    from langgraph.checkpoint.base import empty_checkpoint, uuid6
    from langgraph.checkpoint.memory import InMemorySaver

    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    checkpointer = InMemorySaver()
    thread_id = "single-checkpoint-thread"
    human = HumanMessage(id="human-1", content="question", additional_kwargs={"run_id": "source-run"})
    ai = AIMessage(id="ai-1", content="answer")

    async def _seed() -> str:
        checkpoint = empty_checkpoint()
        checkpoint["id"] = str(uuid6())
        checkpoint["channel_values"] = {"messages": [human, ai]}
        checkpoint["channel_versions"] = {"messages": 1}
        config = await checkpointer.aput(
            {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
            checkpoint,
            {"step": 1, "source": "loop", "writes": {"test": {}}, "parents": {}},
            {"messages": 1},
        )
        return config["configurable"]["checkpoint_id"]

    head_id = asyncio.run(_seed())

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_prepare_regenerate_payload(thread_id, "ai-1", _request(checkpointer, FakeEventStore([]))))

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not find an addressable checkpoint before the target user message"

    # Read-only: the thread's checkpoint history must not be mutated.
    latest = asyncio.run(checkpointer.aget_tuple({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}))
    assert latest is not None
    assert latest.config["configurable"]["checkpoint_id"] == head_id
    history = asyncio.run(_collect_checkpoints(checkpointer, thread_id))
    assert [item.config["configurable"]["checkpoint_id"] for item in history] == [head_id]


async def _collect_checkpoints(checkpointer, thread_id: str) -> list:
    return [checkpoint async for checkpoint in checkpointer.alist({"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}})]


def test_prepare_regenerate_payload_rejects_non_latest_assistant():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    old_ai = AIMessage(id="ai-old", content="old")
    latest_ai = AIMessage(id="ai-latest", content="latest")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-latest", [human, old_ai, latest_ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "ai_message",
                "category": "message",
                "content": {"id": "ai-old", "type": "ai", "content": "old"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_prepare_regenerate_payload("thread-1", "ai-old", _request(checkpointer, event_store)))

    assert exc.value.status_code == 409
    assert exc.value.detail == "Only the latest assistant message can be regenerated"


def test_prepare_regenerate_payload_falls_back_to_matching_run_when_events_are_missing():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    run_manager = FakeRunManager(
        [
            SimpleNamespace(run_id="run-latest", status=RunStatus.success, last_ai_message="answer"),
            SimpleNamespace(run_id="run-older", status=RunStatus.error, last_ai_message="answer"),
        ]
    )

    response = asyncio.run(
        _prepare_regenerate_payload(
            "thread-1",
            "ai-1",
            _request(checkpointer, FakeEventStore([]), run_manager=run_manager),
        )
    )

    assert response.target_run_id == "run-latest"
    assert response.metadata["regenerate_from_run_id"] == "run-latest"


def test_prepare_regenerate_payload_uses_server_stamped_human_run_id_without_parent_events():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question", additional_kwargs={"run_id": "parent-run"})
    ai = AIMessage(id="ai-1", content="answer")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    event_store = FakeEventStore([])

    response = asyncio.run(_prepare_regenerate_payload("thread-1", "ai-1", _request(checkpointer, event_store)))

    assert response.target_run_id == "parent-run"


def test_prepare_regenerate_payload_rejects_unverified_run_fallback_when_events_are_missing():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    run_manager = FakeRunManager(
        [
            SimpleNamespace(run_id="run-latest", status=RunStatus.success, last_ai_message="different"),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_regenerate_payload(
                "thread-1",
                "ai-1",
                _request(checkpointer, FakeEventStore([]), run_manager=run_manager),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not find source run for assistant message"


def test_prepare_regenerate_payload_requires_addressable_checkpoint_before_human():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest])
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_prepare_regenerate_payload("thread-1", "ai-1", _request(checkpointer, event_store)))

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not find an addressable checkpoint before the target user message"
    assert checkpointer.alist_limits == [400]


def test_prepare_regenerate_payload_reports_recent_checkpoint_scan_limit():
    from app.gateway.routers.thread_runs import _prepare_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    latest = _checkpoint("ckpt-latest", [human, ai])
    history_without_human = [_checkpoint(f"ckpt-{index}", []) for index in range(201)]
    checkpointer = FakeCheckpointer(history_without_human, latest=latest)
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(_prepare_regenerate_payload("thread-1", "ai-1", _request(checkpointer, event_store)))

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not locate target user message in recent checkpoint history (limit=200)"
    assert checkpointer.alist_limits == [400]


def test_find_base_checkpoint_ignores_duration_only_checkpoints() -> None:
    from app.gateway.routers.thread_runs import _find_base_checkpoint_before_human

    human = HumanMessage(id="human-1", content="question")
    duration_checkpoints = [
        _checkpoint(
            f"duration-{index}",
            [],
            metadata={"writes": {"runtime_run_duration": {"run_ids": [f"run-{index}"]}}},
        )
        for index in range(200)
    ]
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    checkpointer = FakeCheckpointer([*duration_checkpoints, after_human, base])

    result = asyncio.run(_find_base_checkpoint_before_human("thread-1", "human-1", _request(checkpointer, FakeEventStore([]))))

    assert result.config == base.config
    assert checkpointer.alist_limits == [400]


# ---------------------------------------------------------------------------
# Replay-base lineage semantics
# ---------------------------------------------------------------------------


def test_prepare_regenerate_rejects_cyclic_lineage_without_chronological_fallback():
    from app.gateway.routers import thread_runs

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")

    def linked_snapshot(checkpoint_id: str, messages: list[object], parent_id: str) -> SimpleNamespace:
        return SimpleNamespace(
            values={"messages": messages},
            config={
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "",
                    "checkpoint_id": checkpoint_id,
                }
            },
            metadata={},
            parent_config={
                "configurable": {
                    "thread_id": "thread-1",
                    "checkpoint_ns": "",
                    "checkpoint_id": parent_id,
                }
            },
        )

    head = linked_snapshot("head", [human, ai], "cycle")
    cycle = linked_snapshot("cycle", [human], "head")
    wrong_sibling_base = linked_snapshot("wrong-sibling", [], "root")
    by_id = {"head": head, "cycle": cycle}

    async def aget(config):
        return by_id[config["configurable"]["checkpoint_id"]]

    accessor = SimpleNamespace(
        aget=aget,
        ahistory=AsyncMock(return_value=[head, wrong_sibling_base]),
    )
    builder = AsyncMock(
        return_value=(
            accessor,
            {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}},
        )
    )

    with patch.object(thread_runs, "build_thread_checkpoint_state_accessor", builder):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                thread_runs._find_base_checkpoint_before_human(
                    "thread-1",
                    "human-1",
                    _request(FakeCheckpointer([]), FakeEventStore([])),
                    head_checkpoint=head,
                )
            )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not safely resolve the checkpoint before the target user message"
    accessor.ahistory.assert_not_awaited()


def test_prepare_regenerate_rejects_dangling_parent_without_chronological_fallback():
    from app.gateway.routers import thread_runs

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    head = SimpleNamespace(
        values={"messages": [human, ai]},
        config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "head",
            }
        },
        metadata={},
        parent_config={
            "configurable": {
                "thread_id": "thread-1",
                "checkpoint_ns": "",
                "checkpoint_id": "missing",
            }
        },
    )
    missing = SimpleNamespace(
        values={},
        config=head.parent_config,
        metadata=None,
        created_at=None,
        parent_config=None,
    )
    accessor = SimpleNamespace(
        aget=AsyncMock(return_value=missing),
        ahistory=AsyncMock(return_value=[head, _snapshot("wrong-sibling", [])]),
    )
    builder = AsyncMock(
        return_value=(
            accessor,
            {"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}},
        )
    )

    with patch.object(thread_runs, "build_thread_checkpoint_state_accessor", builder):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                thread_runs._find_base_checkpoint_before_human(
                    "thread-1",
                    "human-1",
                    _request(FakeCheckpointer([]), FakeEventStore([])),
                    head_checkpoint=head,
                )
            )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Could not safely resolve the checkpoint before the target user message"
    accessor.ahistory.assert_not_awaited()


def test_prepare_edit_regenerate_payload_prefers_checkpoint_lineage():
    """Edit replay must resolve its base the same lineage-first way regenerate does.

    The lineage walk is attempted before any chronological scan; legacy
    checkpoints without parent links degrade to the bounded scan. Under the
    local raw-checkpointer accessor ``next`` is not derivable (permissive by
    design), so the degraded scan selects ``ckpt-mid`` here where the upstream
    graph-backed accessor skipped it as a pending-task checkpoint.
    """
    from app.gateway.checkpoint_lineage import CheckpointParentMissingError
    from app.gateway.routers import thread_runs

    system = SystemMessage(id="human-1", content="<system-reminder>date</system-reminder>")
    swapped_human = HumanMessage(id="human-1__user", content="original question")
    raw_human = HumanMessage(id="human-1", content="original question")
    ai = AIMessage(id="ai-1", content="answer v1")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-head", [system, swapped_human, ai]),
            _checkpoint("ckpt-after-inject", [system, swapped_human], next_tasks=("LoopDetectionMiddleware.before_agent",)),
            _checkpoint("ckpt-mid", [raw_human], next_tasks=("DynamicContextMiddleware.before_agent",)),
            _checkpoint("ckpt-input", [], next_tasks=("__start__",)),
            _checkpoint("ckpt-empty", []),
        ]
    )
    event_store, run_manager = _answer_run_fixtures()

    walk = AsyncMock(side_effect=CheckpointParentMissingError("no parent link"))

    with patch.object(thread_runs, "find_checkpoint_before_message", walk):
        response = asyncio.run(
            thread_runs._prepare_edit_regenerate_payload(
                "thread-1",
                "human-1__user",
                "edited question",
                _request(checkpointer, event_store, run_manager=run_manager),
            )
        )

    assert walk.await_count == 1
    head_checkpoint = walk.await_args.args[1]
    assert head_checkpoint.config["configurable"]["checkpoint_id"] == "ckpt-head"
    # Legacy checkpoints without parent links still degrade to the bounded scan.
    assert response.checkpoint["checkpoint_id"] == "ckpt-mid"
    assert response.metadata["regenerate_checkpoint_id"] == "ckpt-mid"


# ---------------------------------------------------------------------------
# Edit-regenerate payload
# ---------------------------------------------------------------------------


def test_prepare_edit_regenerate_payload_returns_new_human_and_edit_metadata():
    from app.gateway.routers import thread_runs

    human = HumanMessage(
        id="human-1",
        content="<uploaded_files>injected</uploaded_files>\n\noriginal question",
        name="researcher",
        additional_kwargs={
            ORIGINAL_USER_CONTENT_KEY: "original question",
            "files": [{"filename": "data.csv", "path": "/mnt/user-data/uploads/data.csv"}],
            "referenced_message_contexts": [{"message_id": "ai-prev", "quote": "quoted"}],
            "hide_from_ui": False,
            "run_id": "old-run",
            "timestamp": "2026-07-22T00:00:00Z",
            "middleware_private": "do-not-copy",
        },
    )
    ai = AIMessage(id="ai-1", content="answer v1")
    base = _checkpoint("ckpt-base", [])
    after_human = _checkpoint("ckpt-human", [human])
    latest = _checkpoint("ckpt-ai", [human, ai])
    checkpointer = FakeCheckpointer([latest, after_human, base])
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer v1"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    run_manager = FakeRunManager(
        [
            SimpleNamespace(
                run_id="run-old",
                status=RunStatus.success,
                metadata={},
                last_ai_message="answer v1",
            )
        ]
    )

    response = asyncio.run(
        thread_runs._prepare_edit_regenerate_payload(
            "thread-1",
            "human-1",
            "  updated question\nwith details  ",
            _request(checkpointer, event_store, run_manager=run_manager),
        )
    )

    assert response.checkpoint == {
        "checkpoint_ns": "",
        "checkpoint_id": "ckpt-base",
        "checkpoint_map": None,
    }
    assert response.target_run_id == "run-old"
    assert response.replacement_human_message_id != "human-1"
    assert response.source_message_ids == ["human-1", "ai-1"]
    assert response.metadata == {
        "replay_kind": "edit",
        "regenerate_from_message_id": "ai-1",
        "regenerate_from_run_id": "run-old",
        "regenerate_checkpoint_id": "ckpt-base",
        "edit_from_message_id": "human-1",
        "edit_message_id": response.replacement_human_message_id,
        "edit_version_group_id": "human-1",
    }
    replacement = response.input["messages"][0]
    assert replacement == {
        "type": "human",
        "id": response.replacement_human_message_id,
        "name": "researcher",
        "content": [{"type": "text", "text": "updated question\nwith details"}],
        "additional_kwargs": {
            "files": [{"filename": "data.csv", "path": "/mnt/user-data/uploads/data.csv"}],
            "referenced_message_contexts": [{"message_id": "ai-prev", "quote": "quoted"}],
        },
    }


def test_prepare_edit_regenerate_payload_preserves_a_rename_the_replay_base_predates():
    """Replaying a turn must not roll the thread back to an older title (#4457)."""
    from app.gateway.routers import thread_runs

    human = HumanMessage(id="human-1", content="original question")
    ai = AIMessage(id="ai-1", content="answer v1")
    base = _checkpoint("ckpt-base", [])
    base.checkpoint["channel_values"]["title"] = "auto generated title"
    latest = _checkpoint("ckpt-ai", [human, ai])
    latest.checkpoint["channel_values"]["title"] = "User renamed title"
    event_store, run_manager = _answer_run_fixtures()

    response = asyncio.run(
        thread_runs._prepare_edit_regenerate_payload(
            "thread-1",
            "human-1",
            "edited question",
            _request(FakeCheckpointer([latest, base]), event_store, run_manager=run_manager),
        )
    )

    assert response.checkpoint["checkpoint_id"] == "ckpt-base"
    assert response.input["title"] == "User renamed title"


def test_prepare_edit_regenerate_payload_lets_an_untitled_base_name_the_edited_turn():
    """A base with no title belongs to a thread that has not been named yet.

    Pinning the current title there would keep a name generated from the prompt
    the edit just replaced, so leave the channel empty and let the title
    middleware name the rewritten turn.
    """
    from app.gateway.routers import thread_runs

    human = HumanMessage(id="human-1", content="original question")
    ai = AIMessage(id="ai-1", content="answer v1")
    latest = _checkpoint("ckpt-ai", [human, ai])
    latest.checkpoint["channel_values"]["title"] = "title of the replaced question"
    event_store, run_manager = _answer_run_fixtures()

    response = asyncio.run(
        thread_runs._prepare_edit_regenerate_payload(
            "thread-1",
            "human-1",
            "edited question",
            _request(FakeCheckpointer([latest, _checkpoint("ckpt-base", [])]), event_store, run_manager=run_manager),
        )
    )

    assert response.checkpoint["checkpoint_id"] == "ckpt-base"
    assert "title" not in response.input


@pytest.mark.parametrize(
    ("replacement_text", "detail"),
    [
        ("  \n\t", "Edited message cannot be empty"),
        ("  original question  ", "Edited message is unchanged"),
    ],
)
def test_prepare_edit_regenerate_payload_rejects_empty_or_unchanged_text(replacement_text: str, detail: str):
    from app.gateway.routers.thread_runs import _prepare_edit_regenerate_payload

    human = HumanMessage(id="human-1", content="original question")
    ai = AIMessage(id="ai-1", content="answer")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [human, ai]),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    run_manager = FakeRunManager([SimpleNamespace(run_id="run-old", status=RunStatus.success, metadata={}, last_ai_message="answer")])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_edit_regenerate_payload(
                "thread-1",
                "human-1",
                replacement_text,
                _request(checkpointer, event_store, run_manager=run_manager),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == detail


def test_prepare_edit_regenerate_payload_requires_latest_human_turn():
    from app.gateway.routers.thread_runs import _prepare_edit_regenerate_payload

    old_human = HumanMessage(id="human-old", content="old question")
    old_ai = AIMessage(id="ai-old", content="old answer")
    latest_human = HumanMessage(id="human-latest", content="latest question")
    latest_ai = AIMessage(id="ai-latest", content="latest answer")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-latest", [old_human, old_ai, latest_human, latest_ai]),
            _checkpoint("ckpt-base", []),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_edit_regenerate_payload(
                "thread-1",
                "human-old",
                "edited old question",
                _request(checkpointer, FakeEventStore([])),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Only the latest completed user turn can be edited"


def test_prepare_edit_regenerate_payload_allows_answered_historical_clarification():
    from app.gateway.routers import thread_runs

    old_human = HumanMessage(id="human-old", content="ambiguous request")
    clarification = ToolMessage(
        id="tool-clarify",
        tool_call_id="call-clarify",
        content="need input",
        artifact={
            "human_input": {
                "version": 1,
                "kind": "human_input_request",
                "request_id": "clarify-1",
                "prompt": "Which format?",
            }
        },
    )
    clarification_answer = HumanMessage(
        id="human-clarify-answer",
        content="Use Markdown",
        additional_kwargs={
            "hide_from_ui": True,
            "human_input_response": {
                "version": 1,
                "kind": "human_input_response",
                "request_id": "clarify-1",
                "source": "user",
                "value": "Use Markdown",
            },
        },
    )
    latest_human = HumanMessage(id="human-latest", content="latest question")
    latest_ai = AIMessage(id="ai-latest", content="latest answer")
    historical_messages = [old_human, clarification, clarification_answer]
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [*historical_messages, latest_human, latest_ai]),
            _checkpoint("ckpt-human", [*historical_messages, latest_human]),
            _checkpoint("ckpt-base", historical_messages),
        ]
    )
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-latest",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-latest", "type": "ai", "content": "latest answer"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    run_manager = FakeRunManager(
        [
            SimpleNamespace(
                run_id="run-latest",
                status=RunStatus.success,
                metadata={},
                last_ai_message="latest answer",
            )
        ]
    )

    response = asyncio.run(
        thread_runs._prepare_edit_regenerate_payload(
            "thread-1",
            "human-latest",
            "updated latest question",
            _request(checkpointer, event_store, run_manager=run_manager),
        )
    )

    assert response.checkpoint["checkpoint_id"] == "ckpt-base"
    assert response.target_run_id == "run-latest"
    assert response.source_message_ids == ["human-latest", "ai-latest"]


def test_prepare_edit_regenerate_payload_rejects_open_clarification_turn():
    from app.gateway.routers.thread_runs import _prepare_edit_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    tool = ToolMessage(
        id="tool-1",
        tool_call_id="call-1",
        content="need input",
        artifact={"human_input": {"request_id": "clarify-1", "status": "pending"}},
    )
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-tool", [human, tool]),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_edit_regenerate_payload(
                "thread-1",
                "human-1",
                "updated question",
                _request(checkpointer, FakeEventStore([])),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Only completed assistant text turns can be edited"


def test_prepare_edit_regenerate_payload_rejects_active_goal():
    from app.gateway.routers.thread_runs import _prepare_edit_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [human, ai], goal={"status": "active", "objective": "finish"}),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_edit_regenerate_payload(
                "thread-1",
                "human-1",
                "updated question",
                _request(checkpointer, FakeEventStore([])),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Cannot edit while a goal is active"


def test_prepare_edit_regenerate_payload_requires_successful_source_run():
    from app.gateway.routers.thread_runs import _prepare_edit_regenerate_payload

    human = HumanMessage(id="human-1", content="question")
    ai = AIMessage(id="ai-1", content="answer")
    checkpointer = FakeCheckpointer(
        [
            _checkpoint("ckpt-ai", [human, ai]),
            _checkpoint("ckpt-human", [human]),
            _checkpoint("ckpt-base", []),
        ]
    )
    event_store = FakeEventStore(
        [
            {
                "run_id": "run-old",
                "event_type": "llm.ai.response",
                "category": "message",
                "content": {"id": "ai-1", "type": "ai", "content": "answer"},
                "metadata": {"caller": "lead_agent"},
            }
        ]
    )
    run_manager = FakeRunManager([SimpleNamespace(run_id="run-old", status=RunStatus.error, metadata={}, last_ai_message="answer")])

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            _prepare_edit_regenerate_payload(
                "thread-1",
                "human-1",
                "updated question",
                _request(checkpointer, event_store, run_manager=run_manager),
            )
        )

    assert exc.value.status_code == 409
    assert exc.value.detail == "Only successful assistant runs can be edited and rerun"


# ---------------------------------------------------------------------------
# Replay id semantics (strip helper)
# ---------------------------------------------------------------------------


def test_strip_injected_user_message_id_suffix():
    from app.gateway.routers.thread_runs import strip_injected_user_message_id_suffix

    assert strip_injected_user_message_id_suffix("abc__user") == "abc"
    assert strip_injected_user_message_id_suffix("abc") == "abc"
    assert strip_injected_user_message_id_suffix(None) is None
    # Stripping to empty keeps the original id.
    assert strip_injected_user_message_id_suffix("__user") == "__user"


def test_clean_human_message_for_regenerate_replays_pre_swap_id():
    """Replaying a persisted ``{id}__user`` user turn must feed the graph the id
    the client originally sent (upstream DynamicContextMiddleware swaps the
    first-turn id; the middleware does not exist locally, but the replay
    semantic is preserved for imported/legacy histories)."""
    from app.gateway.routers import thread_runs

    message = HumanMessage(
        id="human-1__user",
        content="original question",
        additional_kwargs={ORIGINAL_USER_CONTENT_KEY: "original question", "hide_from_ui": False},
    )

    clean = thread_runs._clean_human_message_for_regenerate(message)

    assert clean["id"] == "human-1"
    assert clean["type"] == "human"
    assert clean["content"] == [{"type": "text", "text": "original question"}]
    assert ORIGINAL_USER_CONTENT_KEY not in clean["additional_kwargs"]
