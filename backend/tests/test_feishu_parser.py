import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.commands import KNOWN_CHANNEL_COMMANDS
from app.channels.feishu import FeishuChannel
from app.channels.message_bus import InboundMessage, MessageBus


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_feishu_on_message_plain_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Plain text content
    content_dict = {"text": "Hello world"}
    event.event.message.content = json.dumps(content_dict)

    # Call _on_message
    channel._on_message(event)

    # Since main_loop isn't running in this synchronous test, we can't easily assert on bus,
    # but we can intercept _make_inbound to check the parsed text.
    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["text"] == "Hello world"


def test_feishu_on_message_rich_text():
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    # Create mock event
    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text content (topic group / post)
    content_dict = {"content": [[{"tag": "text", "text": "Paragraph 1, part 1."}, {"tag": "text", "text": "Paragraph 1, part 2."}], [{"tag": "at", "text": "@bot"}, {"tag": "text", "text": " Paragraph 2."}]]}
    event.event.message.content = json.dumps(content_dict)

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        parsed_text = mock_make_inbound.call_args[1]["text"]

        # Expected text:
        # Paragraph 1, part 1. Paragraph 1, part 2.
        #
        # @bot  Paragraph 2.
        assert "Paragraph 1, part 1. Paragraph 1, part 2." in parsed_text
        assert "@bot  Paragraph 2." in parsed_text
        assert "\n\n" in parsed_text


def test_feishu_receive_file_replaces_placeholders_in_order():
    async def go():
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

        msg = InboundMessage(
            channel_name="feishu",
            chat_id="chat_1",
            user_id="user_1",
            text="before [image] middle [file] after",
            thread_ts="msg_1",
            files=[{"image_key": "img_key"}, {"file_key": "file_key"}],
        )

        channel._receive_single_file = AsyncMock(side_effect=["/mnt/user-data/uploads/a.png", "/mnt/user-data/uploads/b.pdf"])

        result = await channel.receive_file(msg, "thread_1")

        assert result.text == "before /mnt/user-data/uploads/a.png middle /mnt/user-data/uploads/b.pdf after"

    _run(go())


def test_feishu_on_message_extracts_image_and_file_keys():
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"

    # Rich text with one image and one file element.
    event.event.message.content = json.dumps(
        {
            "content": [
                [
                    {"tag": "text", "text": "See"},
                    {"tag": "img", "image_key": "img_123"},
                    {"tag": "file", "file_key": "file_456"},
                ]
            ]
        }
    )

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        files = mock_make_inbound.call_args[1]["files"]
        assert files == [{"image_key": "img_123"}, {"file_key": "file_456"}]
        assert "[image]" in mock_make_inbound.call_args[1]["text"]
        assert "[file]" in mock_make_inbound.call_args[1]["text"]


@pytest.mark.parametrize("command", sorted(KNOWN_CHANNEL_COMMANDS))
def test_feishu_recognizes_all_known_slash_commands(command):
    """Every entry in KNOWN_CHANNEL_COMMANDS must be classified as a command."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": command})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "command", f"{command!r} should be classified as COMMAND"


@pytest.mark.parametrize(
    "text",
    [
        "/unknown",
        "/mnt/user-data/outputs/prd/technical-design.md",
        "/etc/passwd",
        "/not-a-command at all",
    ],
)
def test_feishu_treats_unknown_slash_text_as_chat(text):
    """Slash-prefixed text that is not a known command must be classified as CHAT."""
    bus = MessageBus()
    config = {"app_id": "test", "app_secret": "test"}
    channel = FeishuChannel(bus, config)

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_1"
    event.event.message.root_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps({"text": text})

    with pytest.MonkeyPatch.context() as m:
        mock_make_inbound = MagicMock()
        m.setattr(channel, "_make_inbound", mock_make_inbound)
        channel._on_message(event)

        mock_make_inbound.assert_called_once()
        assert mock_make_inbound.call_args[1]["msg_type"].value == "chat", f"{text!r} should be classified as CHAT"


def _make_file_event(
    file_key: str,
    *,
    chat_id: str = "chat_1",
    message_id: str = "msg_1",
    user_id: str = "user_1",
    root_id: str | None = None,
    parent_id: str | None = None,
    thread_id: str | None = None,
):
    event = MagicMock()
    event.event.message.chat_id = chat_id
    event.event.message.message_id = message_id
    event.event.message.root_id = root_id
    event.event.message.parent_id = parent_id
    event.event.message.thread_id = thread_id
    event.event.sender.sender_id.open_id = user_id
    event.event.message.content = json.dumps({"file_key": file_key})
    return event


def test_feishu_batches_top_level_file_messages_from_same_user(monkeypatch):
    """Multiple top-level file messages from the same user within the batch
    window must be merged into a single inbound message (one agent run)."""

    async def go():
        monkeypatch.setattr("app.channels.feishu.FEISHU_INBOUND_BATCH_WINDOW_SECONDS", 0.01)
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
        channel._main_loop = asyncio.get_running_loop()
        channel._add_reaction = AsyncMock()
        channel._ensure_running_card_started = MagicMock()

        channel._on_message(_make_file_event("file_a", message_id="msg_file_1"))
        channel._on_message(_make_file_event("file_b", message_id="msg_file_2"))

        inbound = await asyncio.wait_for(bus.get_inbound(), timeout=0.5)

        assert inbound.thread_ts == "msg_file_1"
        assert inbound.topic_id == "msg_file_1"
        assert inbound.text == "[file]\n\n[file]"
        assert inbound.files == [{"file_key": "file_a"}, {"file_key": "file_b"}]
        assert inbound.metadata["message_id"] == "msg_file_1"
        assert inbound.metadata["batched_message_ids"] == ["msg_file_1", "msg_file_2"]
        channel._ensure_running_card_started.assert_called_once()
        assert channel._ensure_running_card_started.call_args.args == ("msg_file_1",)
        assert [call.args for call in channel._add_reaction.call_args_list] == [
            ("msg_file_1", "OK"),
            ("msg_file_2", "OK"),
        ]

    _run(go())


def test_feishu_rich_text_file_message_does_not_enter_batch():
    """Files embedded in rich-text (post) messages must bypass batching."""
    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    channel._schedule_prepare_inbound = MagicMock()

    event = MagicMock()
    event.event.message.chat_id = "chat_1"
    event.event.message.message_id = "msg_rich_file"
    event.event.message.root_id = None
    event.event.message.parent_id = None
    event.event.message.thread_id = None
    event.event.sender.sender_id.open_id = "user_1"
    event.event.message.content = json.dumps(
        {
            "content": [
                [
                    {"tag": "text", "text": "Review"},
                    {"tag": "file", "file_key": "file_a"},
                ]
            ]
        }
    )

    channel._on_message(event)

    channel._schedule_prepare_inbound.assert_called_once()
    inbound = channel._schedule_prepare_inbound.call_args.args[1]
    assert inbound.text == "Review [file]"
    assert inbound.files == [{"file_key": "file_a"}]
    assert channel._pending_inbound_batches == {}


def test_feishu_file_batch_window_expiry_starts_new_topic(monkeypatch):
    """File messages arriving after the batch window expired must start a new
    batch (new topic) instead of merging into the flushed one."""

    async def go():
        monkeypatch.setattr("app.channels.feishu.FEISHU_INBOUND_BATCH_WINDOW_SECONDS", 0.01)
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
        channel._main_loop = asyncio.get_running_loop()
        channel._add_reaction = AsyncMock()
        channel._ensure_running_card_started = MagicMock()

        channel._on_message(_make_file_event("file_a", message_id="msg_file_1"))
        first = await asyncio.wait_for(bus.get_inbound(), timeout=0.5)

        channel._on_message(_make_file_event("file_b", message_id="msg_file_2"))
        second = await asyncio.wait_for(bus.get_inbound(), timeout=0.5)

        assert first.topic_id == "msg_file_1"
        assert first.files == [{"file_key": "file_a"}]
        assert second.topic_id == "msg_file_2"
        assert second.files == [{"file_key": "file_b"}]
        assert channel._ensure_running_card_started.call_args_list[0].args == ("msg_file_1",)
        assert channel._ensure_running_card_started.call_args_list[1].args == ("msg_file_2",)

    _run(go())


def test_feishu_explicit_file_reply_does_not_enter_batch(monkeypatch):
    """File messages that are explicit replies (root_id set) must not be batched."""

    async def go():
        monkeypatch.setattr("app.channels.feishu.FEISHU_INBOUND_BATCH_WINDOW_SECONDS", 10.0)
        bus = MessageBus()
        channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
        channel._main_loop = asyncio.get_running_loop()
        channel._add_reaction = AsyncMock()
        channel._ensure_running_card_started = MagicMock()

        channel._on_message(_make_file_event("file_a", message_id="msg_reply", root_id="msg_root"))

        inbound = await asyncio.wait_for(bus.get_inbound(), timeout=0.5)
        assert inbound.thread_ts == "msg_reply"
        assert inbound.topic_id == "msg_root"
        assert inbound.files == [{"file_key": "file_a"}]
        assert channel._pending_inbound_batches == {}

    _run(go())


def test_feishu_expired_file_batch_does_not_get_overwritten(monkeypatch):
    """When a new file message arrives after the pending batch expired, the
    expired batch must be dispatched as-is and a fresh batch started."""
    monkeypatch.setattr("app.channels.feishu.FEISHU_INBOUND_BATCH_WINDOW_SECONDS", 0.5)
    now = 0.0
    monkeypatch.setattr("app.channels.feishu.time.time", lambda: now)

    bus = MessageBus()
    channel = FeishuChannel(bus, {"app_id": "test", "app_secret": "test"})
    channel._schedule_batch_flush = MagicMock()
    channel._schedule_prepare_inbound = MagicMock()

    first = InboundMessage(
        channel_name="feishu",
        chat_id="chat_1",
        user_id="user_1",
        text="[file]",
        thread_ts="msg_file_1",
        files=[{"file_key": "file_a"}],
    )
    second = InboundMessage(
        channel_name="feishu",
        chat_id="chat_1",
        user_id="user_1",
        text="[file]",
        thread_ts="msg_file_2",
        files=[{"file_key": "file_b"}],
    )

    channel._queue_file_inbound_batch("msg_file_1", first)
    now = 1.0
    channel._queue_file_inbound_batch("msg_file_2", second)

    channel._schedule_prepare_inbound.assert_called_once_with(
        "msg_file_1",
        first,
        source_message_ids=["msg_file_1"],
    )
    assert channel._pop_pending_inbound_batch(channel._pending_key("chat_1", "user_1"), anchor_message_id="msg_file_1") is None

    current = channel._pop_pending_inbound_batch(channel._pending_key("chat_1", "user_1"), anchor_message_id="msg_file_2")
    assert current == ("msg_file_2", second, ["msg_file_2"])
