"""Tests for inbound message dedupe (provider redelivery guard).

Feishu re-posts an event whose ack window expired while the agent was still
running. Without dedupe that is a second full agent run: duplicate billing and a
duplicate reply.
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

import pytest

from app.channels.dedupe_store import MemoryInboundDedupeStore
from app.channels.manager import CHAT_SCOPED_WORKSPACE_CHANNELS, ChannelManager
from app.channels.message_bus import InboundMessage, InboundMessageType, MessageBus
from app.channels.store import ChannelStore


def _feishu_msg(message_id: str, *, text: str = "你好", chat_id: str = "oc_chat1") -> InboundMessage:
    return InboundMessage(
        channel_name="feishu",
        chat_id=chat_id,
        user_id="ou_user1",
        text=text,
        metadata={"message_id": message_id, "root_id": None},
    )


def _make_manager(bus: MessageBus) -> ChannelManager:
    store = ChannelStore(path=Path(tempfile.mkdtemp()) / "store.json")
    return ChannelManager(bus=bus, store=store)


class _DispatchRecorder:
    """Replaces _handle_message so we count dispatches without running an agent."""

    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls: list[InboundMessage] = []
        self._fail_first = fail_first

    async def __call__(self, msg: InboundMessage) -> None:
        self.calls.append(msg)
        if self._fail_first and len(self.calls) == 1:
            raise RuntimeError("transient boom")


async def _wait_for(condition, *, timeout: float = 5.0) -> None:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return
        await asyncio.sleep(0.02)
    raise TimeoutError("condition not met")


# ---------------------------------------------------------------------------
# Dedupe key construction — the "installed but never fires" trap
# ---------------------------------------------------------------------------


class TestInboundDedupeKey:
    def test_feishu_key_falls_back_to_chat_id_as_workspace(self):
        """InboundMessage has no workspace_id field; feishu must still get a key.

        Guards the regression where the key builder returns None for every feishu
        message, silently disabling dedupe entirely.
        """
        key = ChannelManager._inbound_dedupe_key(_feishu_msg("om_abc"))

        assert key is not None, "feishu must not fall through to the no-dedupe path"
        assert key == ("feishu", "oc_chat1", "oc_chat1", "om_abc")
        # The chat_id fallback is what populates the workspace slot.
        assert key[1] == "oc_chat1"
        assert "feishu" in CHAT_SCOPED_WORKSPACE_CHANNELS

    def test_key_excludes_text_so_identical_text_yields_distinct_keys(self):
        first = ChannelManager._inbound_dedupe_key(_feishu_msg("om_1", text="你好"))
        second = ChannelManager._inbound_dedupe_key(_feishu_msg("om_2", text="你好"))

        assert first != second

    def test_no_stable_message_id_skips_dedupe(self):
        msg = InboundMessage(channel_name="feishu", chat_id="oc_chat1", user_id="u", text="hi", metadata={})

        assert ChannelManager._inbound_dedupe_key(msg) is None

    def test_non_chat_scoped_channel_without_workspace_skips_dedupe(self):
        """Slack channel ids are not globally unique — fail closed rather than collapse tenants."""
        msg = InboundMessage(
            channel_name="slack",
            chat_id="C123",
            user_id="u",
            text="hi",
            metadata={"event_id": "Ev123"},
        )

        assert ChannelManager._inbound_dedupe_key(msg) is None

    def test_explicit_workspace_metadata_wins_over_chat_id(self):
        msg = InboundMessage(
            channel_name="discord",
            chat_id="chan1",
            user_id="u",
            text="hi",
            metadata={"guild_id": "g1", "message_id": "m1"},
        )

        assert ChannelManager._inbound_dedupe_key(msg) == ("discord", "g1", "chan1", "m1")

    def test_message_id_read_from_nested_raw_message(self):
        msg = InboundMessage(
            channel_name="wechat",
            chat_id="ilink1",
            user_id="u",
            text="hi",
            metadata={"raw_message": {"msg_id": "wx_9"}},
        )

        assert ChannelManager._inbound_dedupe_key(msg) == ("wechat", "ilink1", "ilink1", "wx_9")


# ---------------------------------------------------------------------------
# Dispatch-loop behaviour
# ---------------------------------------------------------------------------


class TestDispatchLoopDedupe:
    @pytest.mark.asyncio
    async def test_same_message_id_delivered_twice_dispatches_once(self):
        bus = MessageBus()
        manager = _make_manager(bus)
        recorder = _DispatchRecorder()
        manager._handle_message = recorder

        await manager.start()
        try:
            await bus.publish_inbound(_feishu_msg("om_dup"))
            await _wait_for(lambda: len(recorder.calls) >= 1)
            await bus.publish_inbound(_feishu_msg("om_dup"))
            await asyncio.sleep(0.2)
        finally:
            await manager.stop()

        assert len(recorder.calls) == 1

    @pytest.mark.asyncio
    async def test_distinct_message_ids_with_identical_text_both_dispatch(self):
        """A user deliberately sending "你好" twice must not have the second swallowed."""
        bus = MessageBus()
        manager = _make_manager(bus)
        recorder = _DispatchRecorder()
        manager._handle_message = recorder

        await manager.start()
        try:
            await bus.publish_inbound(_feishu_msg("om_1", text="你好"))
            await bus.publish_inbound(_feishu_msg("om_2", text="你好"))
            await _wait_for(lambda: len(recorder.calls) >= 2)
            await asyncio.sleep(0.1)
        finally:
            await manager.stop()

        assert len(recorder.calls) == 2
        assert [m.text for m in recorder.calls] == ["你好", "你好"]

    @pytest.mark.asyncio
    async def test_redelivery_after_handling_failure_is_reprocessed(self):
        """A transient error must not black-hole the message_id for the TTL."""
        bus = MessageBus()
        manager = _make_manager(bus)
        store = manager._inbound_dedupe_store
        key = ChannelManager._inbound_dedupe_key(_feishu_msg("om_retry"))

        recorder = _DispatchRecorder()

        async def handle(msg: InboundMessage) -> None:
            await recorder(msg)
            if len(recorder.calls) == 1:
                # Simulates _handle_message's generic-Exception branch.
                await manager._release_inbound_dedupe_key(msg)

        manager._handle_message = handle

        await manager.start()
        try:
            await bus.publish_inbound(_feishu_msg("om_retry"))
            await _wait_for(lambda: len(recorder.calls) >= 1)
            await _wait_for(lambda: key not in store._store)
            await bus.publish_inbound(_feishu_msg("om_retry"))
            await _wait_for(lambda: len(recorder.calls) >= 2)
        finally:
            await manager.stop()

        assert len(recorder.calls) == 2

    @pytest.mark.asyncio
    async def test_handle_message_releases_key_on_unexpected_error(self):
        """Wire-level check that the except branch actually calls release."""
        bus = MessageBus()
        manager = _make_manager(bus)
        msg = _feishu_msg("om_boom")
        key = ChannelManager._inbound_dedupe_key(msg)

        async def boom(_msg, *args, **kwargs):
            raise RuntimeError("transient boom")

        manager._handle_chat = boom
        manager._send_error = lambda *a, **k: asyncio.sleep(0)
        manager._semaphore = asyncio.Semaphore(1)

        assert await manager._is_duplicate_inbound(msg) is False
        assert key in manager._inbound_dedupe_store._store

        await manager._handle_message(msg)

        assert key not in manager._inbound_dedupe_store._store
        assert await manager._is_duplicate_inbound(msg) is False

    @pytest.mark.asyncio
    async def test_command_failure_releases_key(self):
        """A failing /new must not black-hole its message_id for the full TTL.

        Feishu emits slash commands as InboundMessageType.COMMAND, which takes an
        early-return branch that never reaches the chat try/except. A transient
        Gateway error there would otherwise leave the key recorded, so the user's
        retry of the same command is silently dropped.
        """
        bus = MessageBus()
        manager = _make_manager(bus)
        msg = _feishu_msg("om_cmd", text="/new")
        msg.msg_type = InboundMessageType.COMMAND
        key = ChannelManager._inbound_dedupe_key(msg)

        async def boom(_msg):
            raise RuntimeError("gateway down")

        manager._handle_command = boom
        manager._send_error = lambda *a, **k: asyncio.sleep(0)
        manager._semaphore = asyncio.Semaphore(1)

        assert await manager._is_duplicate_inbound(msg) is False
        assert key in manager._inbound_dedupe_store._store

        await manager._handle_message(msg)

        assert key not in manager._inbound_dedupe_store._store, "failed command black-holed its message_id"

    @pytest.mark.asyncio
    async def test_invalid_session_config_keeps_key_recorded(self):
        """A permanent misconfiguration must not be re-run on every redelivery."""
        from app.channels.manager import InvalidChannelSessionConfigError

        bus = MessageBus()
        manager = _make_manager(bus)
        msg = _feishu_msg("om_badcfg")
        key = ChannelManager._inbound_dedupe_key(msg)

        async def bad_config(_msg, *args, **kwargs):
            raise InvalidChannelSessionConfigError("bad assistant_id")

        manager._handle_chat = bad_config
        manager._send_error = lambda *a, **k: asyncio.sleep(0)
        manager._semaphore = asyncio.Semaphore(1)

        await manager._is_duplicate_inbound(msg)
        await manager._handle_message(msg)

        assert key in manager._inbound_dedupe_store._store


# ---------------------------------------------------------------------------
# Store semantics
# ---------------------------------------------------------------------------


class TestMemoryInboundDedupeStore:
    @pytest.mark.asyncio
    async def test_try_record_reports_duplicate_on_second_call(self):
        store = MemoryInboundDedupeStore()
        key = ("feishu", "c1", "c1", "m1")

        assert await store.try_record(key) is False
        assert await store.try_record(key) is True

    @pytest.mark.asyncio
    async def test_release_readmits_key(self):
        store = MemoryInboundDedupeStore()
        key = ("feishu", "c1", "c1", "m1")

        await store.try_record(key)
        await store.release(key)

        assert await store.try_record(key) is False

    @pytest.mark.asyncio
    async def test_release_of_unknown_key_is_noop(self):
        store = MemoryInboundDedupeStore()

        await store.release(("feishu", "c1", "c1", "never-seen"))

    @pytest.mark.asyncio
    async def test_expired_entry_is_readmitted(self):
        store = MemoryInboundDedupeStore(ttl_seconds=0)
        key = ("feishu", "c1", "c1", "m1")

        assert await store.try_record(key) is False
        await asyncio.sleep(0.01)
        assert await store.try_record(key) is False

    @pytest.mark.asyncio
    async def test_capacity_bound_evicts_oldest(self):
        # try_record evicts overflow BEFORE inserting (dedupe_store.py: evict
        # `while len > max`, then insert), so the steady state sits at
        # max_entries + 1. A bare `len <= max + 1` assertion passes under ANY
        # eviction policy (even evict-newest) — only the surviving key set
        # proves evict-oldest.
        store = MemoryInboundDedupeStore(max_entries=2)

        for i in range(4):
            await store.try_record(("feishu", "c1", "c1", f"m{i}"))

        # m0 (oldest) is gone; m1..m3 survive (2 + 1 steady state).
        assert set(store._store) == {
            ("feishu", "c1", "c1", "m1"),
            ("feishu", "c1", "c1", "m2"),
            ("feishu", "c1", "c1", "m3"),
        }

        # The next insert evicts m1 — strictly FIFO order.
        await store.try_record(("feishu", "c1", "c1", "m4"))
        assert set(store._store) == {
            ("feishu", "c1", "c1", "m2"),
            ("feishu", "c1", "c1", "m3"),
            ("feishu", "c1", "c1", "m4"),
        }
