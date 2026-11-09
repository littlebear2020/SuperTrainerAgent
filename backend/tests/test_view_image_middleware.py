"""Unit tests for ViewImageMiddleware.

2026-08-18 rewrite: the middleware no longer writes state (``before_model`` /
``after_model`` removed). Injection happens inside ``wrap_model_call`` /
``awrap_model_call`` on the inference window only, with a K-image sliding
window (K = ``_MAX_INJECTED_IMAGES`` = 2) over ``viewed_images`` dict recency
order and text placeholders for older views.

Covered behavior:
- trigger helpers: last AIMessage, view_image tool detection, tool completion.
- ``_create_image_details_message``: K-window truncation, placeholder
  metadata semantics, per-image description blocks, on-demand base64.
- ``_should_inject_image_message``: gating preconditions + dedup markers.
- ``wrap_model_call`` / ``awrap_model_call``: window-only injection, zero
  state writes, legacy transient message filtering, pass-through identity.
- re-view regression: viewing K+1 images then re-viewing the first must bring
  that image back into the window as base64 (red under the old reducer).
- registration order: ViewImage after Summarization and DanglingToolCall in
  both assembly paths (lead agent + feature factory).
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware.types import ModelRequest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from deerflow.agents.middlewares.view_image_middleware import (
    _MAX_INJECTED_IMAGES,
    _MAX_SAME_TURN_IMAGES,
    ViewImageMiddleware,
)
from deerflow.agents.thread_state import merge_viewed_images


def _view_image_call(call_id: str = "call_1", path: str = "/mnt/user-data/uploads/img.png") -> dict:
    return {"name": "view_image", "id": call_id, "args": {"image_path": path}}


def _other_tool_call(call_id: str = "call_other", name: str = "bash") -> dict:
    return {"name": name, "id": call_id, "args": {"command": "ls"}}


def _make_request(messages: list, state: dict | None = None) -> ModelRequest:
    """Build a real ModelRequest (model is never invoked in these tests)."""
    return ModelRequest(
        model=MagicMock(),
        messages=messages,
        state=state if state is not None else {"messages": list(messages)},
    )


def _capture_handler(sink: list):
    """A handler that records the request it receives and returns a stub response."""

    def handler(req: ModelRequest):
        sink.append(req)
        return MagicMock(name="model_response")

    return handler


def _image_url_blocks(messages: list) -> list[dict]:
    return [b for m in messages for b in (m.content if isinstance(m.content, list) else []) if isinstance(b, dict) and b.get("type") == "image_url"]


def _meta_for(path_str: str, actual: object, payload: bytes) -> dict:
    return {"mime_type": "image/png", "size": len(payload), "actual_path": str(actual)}


class TestGetLastAssistantMessage:
    def test_returns_none_on_empty_list(self):
        mw = ViewImageMiddleware()
        assert mw._get_last_assistant_message([]) is None

    def test_returns_none_when_no_ai_message(self):
        mw = ViewImageMiddleware()
        messages = [
            SystemMessage(content="sys"),
            HumanMessage(content="hi"),
        ]
        assert mw._get_last_assistant_message(messages) is None

    def test_returns_most_recent_ai_message(self):
        mw = ViewImageMiddleware()
        older = AIMessage(content="older")
        newer = AIMessage(content="newer")
        messages = [HumanMessage(content="q"), older, HumanMessage(content="q2"), newer]
        assert mw._get_last_assistant_message(messages) is newer


class TestHasViewImageTool:
    def test_returns_false_when_tool_calls_attr_missing(self):
        """Exercise the `not hasattr(message, "tool_calls")` guard.

        AIMessage always has a `tool_calls` attribute, so we use a plain
        object that truly lacks the attribute to cover this branch.
        """
        mw = ViewImageMiddleware()
        msg = SimpleNamespace(content="just text")  # no tool_calls attribute
        assert not hasattr(msg, "tool_calls")  # precondition
        assert mw._has_view_image_tool(msg) is False

    def test_returns_false_when_ai_message_has_no_tool_calls(self):
        """AIMessage without tool_calls kwarg defaults to an empty list."""
        mw = ViewImageMiddleware()
        msg = AIMessage(content="just text")
        assert mw._has_view_image_tool(msg) is False

    def test_returns_false_when_tool_calls_empty(self):
        mw = ViewImageMiddleware()
        msg = AIMessage(content="", tool_calls=[])
        assert mw._has_view_image_tool(msg) is False

    def test_returns_true_when_view_image_present(self):
        mw = ViewImageMiddleware()
        msg = AIMessage(content="", tool_calls=[_view_image_call()])
        assert mw._has_view_image_tool(msg) is True

    def test_returns_true_when_view_image_mixed_with_others(self):
        mw = ViewImageMiddleware()
        msg = AIMessage(
            content="",
            tool_calls=[_other_tool_call(), _view_image_call(call_id="call_vi")],
        )
        assert mw._has_view_image_tool(msg) is True

    def test_returns_false_when_only_other_tools(self):
        mw = ViewImageMiddleware()
        msg = AIMessage(content="", tool_calls=[_other_tool_call()])
        assert mw._has_view_image_tool(msg) is False


class TestAllToolsCompleted:
    def test_returns_false_when_no_tool_calls(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[])
        assert mw._all_tools_completed([assistant], assistant) is False

    def test_returns_true_when_all_completed(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(
            content="",
            tool_calls=[_view_image_call("c1"), _view_image_call("c2", "/p2.png")],
        )
        messages = [
            assistant,
            ToolMessage(content="ok", tool_call_id="c1"),
            ToolMessage(content="ok", tool_call_id="c2"),
        ]
        assert mw._all_tools_completed(messages, assistant) is True

    def test_returns_false_when_some_tool_call_unanswered(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(
            content="",
            tool_calls=[_view_image_call("c1"), _view_image_call("c2", "/p2.png")],
        )
        messages = [assistant, ToolMessage(content="ok", tool_call_id="c1")]
        assert mw._all_tools_completed(messages, assistant) is False

    def test_returns_false_when_assistant_not_in_messages(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        messages = [HumanMessage(content="hi")]
        assert mw._all_tools_completed(messages, assistant) is False

    def test_ignores_tool_messages_before_assistant(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        # A stale ToolMessage with matching id appears BEFORE the assistant turn.
        # It should not count — only ToolMessages after the assistant close the call.
        messages = [
            ToolMessage(content="stale", tool_call_id="c1"),
            assistant,
        ]
        assert mw._all_tools_completed(messages, assistant) is False


class TestCreateImageDetailsMessage:
    """K-window content construction (takes the viewed_images dict directly)."""

    def test_returns_placeholder_when_no_images(self):
        mw = ViewImageMiddleware()
        blocks = mw._create_image_details_message({})
        assert blocks == [{"type": "text", "text": "No images have been viewed."}]

    def test_builds_blocks_for_single_image(self, tmp_path):
        mw = ViewImageMiddleware()
        actual = tmp_path / "cat.png"
        payload = b"\x89PNG\r\n\x1a\nfake-bytes"
        actual.write_bytes(payload)
        blocks = mw._create_image_details_message({"/path/to/cat.png": _meta_for("/path/to/cat.png", actual, payload)})

        # header text + per-image description text + per-image image_url block
        assert len(blocks) == 3
        assert blocks[0] == {"type": "text", "text": "Here are the images you've viewed:"}
        assert blocks[1]["type"] == "text"
        assert "/path/to/cat.png" in blocks[1]["text"]
        assert "image/png" in blocks[1]["text"]
        # R-11: image is read from disk on-demand and encoded here.
        assert blocks[2]["type"] == "image_url"
        assert blocks[2]["image_url"]["url"].startswith("data:image/png;base64,")

    def test_within_k_all_images_injected(self, tmp_path):
        """K images viewed → all K get base64 (window not yet full)."""
        mw = ViewImageMiddleware()
        files = {}
        viewed = {}
        for name in ("a", "b"):
            f = tmp_path / f"{name}.png"
            f.write_bytes(f"png-bytes-{name}".encode())
            files[name] = f
            viewed[f"/{name}.png"] = _meta_for(name, f, f"png-bytes-{name}".encode())
        assert len(viewed) == _MAX_INJECTED_IMAGES

        blocks = mw._create_image_details_message(viewed)
        image_url_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "image_url"]
        assert len(image_url_blocks) == _MAX_INJECTED_IMAGES

    def test_beyond_k_older_images_become_placeholders(self, tmp_path):
        """3 images viewed (dict recency order a,b,c) → only last K as base64,
        a becomes a text placeholder carrying path/mime/size + re-view guidance."""
        mw = ViewImageMiddleware()
        viewed = {}
        for name in ("a", "b", "c"):
            f = tmp_path / f"{name}.png"
            payload = f"png-bytes-{name}".encode()
            f.write_bytes(payload)
            viewed[f"/{name}.png"] = _meta_for(name, f, payload)

        blocks = mw._create_image_details_message(viewed)
        image_url_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "image_url"]
        import base64 as b64

        decoded = [b64.b64decode(u.split(",", 1)[1]) for u in (b["image_url"]["url"] for b in image_url_blocks)]
        # exactly K images injected, and they are the most recent (b, c)
        assert len(decoded) == _MAX_INJECTED_IMAGES
        assert any(b"png-bytes-b" in d for d in decoded)
        assert any(b"png-bytes-c" in d for d in decoded)
        # a degraded to a placeholder with metadata semantics
        placeholder_blocks = [b for b in blocks if isinstance(b, dict) and b.get("type") == "text" and "/a.png" in b.get("text", "")]
        assert len(placeholder_blocks) == 1
        text = placeholder_blocks[0]["text"]
        assert "已查看图片" in text
        assert "image/png" in text
        assert str(viewed["/a.png"]["size"]) in text
        assert "view_image" in text  # told it may re-view

    def test_omits_image_url_block_when_actual_path_missing(self):
        mw = ViewImageMiddleware()
        blocks = mw._create_image_details_message({"/broken.png": {"mime_type": "image/png"}})
        # header + description only (no image_url since no actual_path)
        assert len(blocks) == 2
        assert all(not (isinstance(b, dict) and b.get("type") == "image_url") for b in blocks)

    def test_uses_unknown_mime_type_when_missing(self):
        mw = ViewImageMiddleware()
        blocks = mw._create_image_details_message({"/mystery.bin": {}})
        description_blocks = [b for b in blocks if b.get("type") == "text" and "/mystery.bin" in b.get("text", "")]
        assert len(description_blocks) == 1
        assert "unknown" in description_blocks[0]["text"]


class TestShouldInjectImageMessage:
    """Trigger gating on the inference window's message list."""

    def test_false_when_no_messages(self):
        mw = ViewImageMiddleware()
        assert mw._should_inject_image_message([]) is False

    def test_false_when_no_assistant_message(self):
        mw = ViewImageMiddleware()
        assert mw._should_inject_image_message([HumanMessage(content="hello")]) is False

    def test_false_when_no_view_image_tool_call(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_other_tool_call()])
        messages = [assistant, ToolMessage(content="ok", tool_call_id="call_other")]
        assert mw._should_inject_image_message(messages) is False

    def test_false_when_tool_not_completed(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        assert mw._should_inject_image_message([assistant]) is False

    def test_true_when_all_preconditions_met(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        messages = [assistant, ToolMessage(content="ok", tool_call_id="c1")]
        assert mw._should_inject_image_message(messages) is True

    def test_false_when_marker_message_already_after_assistant(self):
        """Defense in depth: a marker message after the assistant turn (as could
        happen if _should_inject_image_message is ever called on an unfiltered
        list) suppresses re-injection."""
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        injected = ViewImageMiddleware._create_image_context_message([{"type": "text", "text": "Here are the images you've viewed:"}])
        messages = [assistant, ToolMessage(content="ok", tool_call_id="c1"), injected]
        assert mw._should_inject_image_message(messages) is False

    def test_false_when_legacy_text_marker_present(self):
        """The middleware also recognizes the legacy 'Here are the ... images
        you've viewed' text markers (pre-marker-id checkpoints) as an
        already-injected signal."""
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        legacy = HumanMessage(content="Here are the images you've viewed: /img.png")
        messages = [assistant, ToolMessage(content="ok", tool_call_id="c1"), legacy]
        assert mw._should_inject_image_message(messages) is False

    def test_false_when_legacy_details_marker_present(self):
        mw = ViewImageMiddleware()
        assistant = AIMessage(content="", tool_calls=[_view_image_call("c1")])
        legacy = HumanMessage(content="Here are the details of the images you've viewed: ...")
        messages = [assistant, ToolMessage(content="ok", tool_call_id="c1"), legacy]
        assert mw._should_inject_image_message(messages) is False


class TestWrapModelCall:
    """Window-only injection via wrap_model_call / awrap_model_call."""

    def _triggered_messages(self, path: str = "/mnt/user-data/uploads/img.png") -> list:
        return [
            AIMessage(content="", tool_calls=[_view_image_call("c1", path)]),
            ToolMessage(content="Successfully read image", tool_call_id="c1"),
        ]

    def test_window_gets_image_message_when_trigger_fires(self, tmp_path):
        mw = ViewImageMiddleware()
        img = tmp_path / "img.png"
        payload = b"png-bytes"
        img.write_bytes(payload)
        request = _make_request(
            self._triggered_messages(),
            state={"messages": [], "viewed_images": {"/img.png": _meta_for("/img.png", img, payload)}},
        )
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        assert len(sink) == 1
        seen = sink[0]
        injected = seen.messages[-1]
        assert isinstance(injected, HumanMessage)
        # Marker id + hide_from_ui convention preserved (serialization-layer
        # defense keys off hide_from_ui if this payload ever persists again).
        assert injected.id.startswith("view-image-context:")
        assert injected.additional_kwargs.get("hide_from_ui") is True
        assert injected.additional_kwargs.get("deerflow_view_image_context") is True
        assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in injected.content)

    def test_zero_state_writes(self, tmp_path):
        """Architecture pin: no state mutation is possible from the wrap path."""
        mw = ViewImageMiddleware()
        img = tmp_path / "img.png"
        payload = b"png-bytes"
        img.write_bytes(payload)
        original_messages = self._triggered_messages()
        state = {"messages": list(original_messages), "viewed_images": {"/img.png": _meta_for("/img.png", img, payload)}}
        request = _make_request(original_messages, state=state)
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        # original request + state untouched
        assert request.messages == original_messages
        assert len(state["messages"]) == 2
        assert state["viewed_images"] == {"/img.png": _meta_for("/img.png", img, payload)}
        # the handler saw a NEW request object, not a mutated original
        assert sink[0] is not request
        assert len(sink[0].messages) == 3

    def test_no_before_or_after_model_overrides(self):
        """The state-writing hooks must stay removed — their reappearance would
        reintroduce the checkpoint transient-payload leak this rewrite fixed."""
        assert "before_model" not in ViewImageMiddleware.__dict__
        assert "abefore_model" not in ViewImageMiddleware.__dict__
        assert "after_model" not in ViewImageMiddleware.__dict__
        assert "aafter_model" not in ViewImageMiddleware.__dict__

    def test_passthrough_identity_when_no_trigger(self):
        """No view_image activity → handler receives the very same request object."""
        mw = ViewImageMiddleware()
        request = _make_request([HumanMessage(content="hi"), AIMessage(content="hello")])
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))
        assert sink[0] is request

    def test_no_injection_when_view_image_not_completed(self, tmp_path):
        mw = ViewImageMiddleware()
        img = tmp_path / "img.png"
        payload = b"png-bytes"
        img.write_bytes(payload)
        request = _make_request(
            [AIMessage(content="", tool_calls=[_view_image_call("c1")])],
            state={"messages": [], "viewed_images": {"/img.png": _meta_for("/img.png", img, payload)}},
        )
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))
        assert sink[0] is request  # untouched

    def test_legacy_marker_messages_filtered_from_window(self):
        """Transient image-context messages persisted by the old before_model
        code (e.g. a run interrupted between injection and removal) must be
        dropped from the window — after_model cleanup no longer exists."""
        mw = ViewImageMiddleware()
        legacy = ViewImageMiddleware._create_image_context_message(
            [
                {"type": "text", "text": "Here are the images you've viewed:"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,STALE"}},
            ]
        )
        request = _make_request([HumanMessage(content="hi"), legacy, AIMessage(content="done")])
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))
        assert sink[0] is not request
        assert legacy not in sink[0].messages
        assert not _image_url_blocks(sink[0].messages)

    def test_window_image_count_capped_at_k(self, tmp_path):
        """复现验证（缩样）: 42-image incident mechanism — after viewing many
        images, the window still carries at most K base64 payloads."""
        mw = ViewImageMiddleware()
        viewed = {}
        for i in range(6):
            f = tmp_path / f"i{i}.png"
            payload = f"payload-{i}-{'x' * 512}".encode()  # distinguishable
            f.write_bytes(payload)
            viewed[f"/i{i}.png"] = _meta_for(f"/i{i}.png", f, payload)

        request = _make_request(
            self._triggered_messages("/i5.png"),
            state={"messages": [], "viewed_images": viewed},
        )
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))
        # 同轮批量 1 张 + 历史 K 张 = 恒定上限（旧机制第 N 轮携带 N 张）
        assert len(_image_url_blocks(sink[0].messages)) == 1 + _MAX_INJECTED_IMAGES

    @pytest.mark.anyio
    async def test_awrap_model_call_matches_sync_behavior(self, tmp_path):
        mw = ViewImageMiddleware()
        img = tmp_path / "img.png"
        payload = b"png-bytes"
        img.write_bytes(payload)
        request = _make_request(
            self._triggered_messages(),
            state={"messages": [], "viewed_images": {"/img.png": _meta_for("/img.png", img, payload)}},
        )
        sink: list = []

        async def ahandler(req: ModelRequest):
            sink.append(req)
            return MagicMock(name="model_response")

        await mw.awrap_model_call(request, ahandler)
        assert len(sink) == 1
        injected = sink[0].messages[-1]
        assert isinstance(injected, HumanMessage)
        assert any(isinstance(b, dict) and b.get("type") == "image_url" for b in injected.content)

    @pytest.mark.anyio
    async def test_awrap_model_call_passthrough_when_no_trigger(self):
        mw = ViewImageMiddleware()
        request = _make_request([HumanMessage(content="hi")])
        sink: list = []

        async def ahandler(req: ModelRequest):
            sink.append(req)
            return MagicMock(name="model_response")

        result = await mw.awrap_model_call(request, ahandler)
        assert sink[0] is request
        assert result is not None


class TestSameTurnBatchExemption:
    """2026-08-18 用户拍板（活体证据：2 宽窗口让 6 图任务重看 14 次仍只确认 4/6）：
    本轮 view_image 调用请求的图**全部**注入（上限 _MAX_SAME_TURN_IMAGES），
    跨轮历史仍维持 K 窗口——事故级"每轮全量"只可能发生在跨轮，不会回归。"""

    @staticmethod
    def _multi_view_turn(paths: list[str]) -> list:
        calls = [{"name": "view_image", "id": f"c{i}", "args": {"image_path": p}} for i, p in enumerate(paths)]
        return [
            HumanMessage(content="看这些图"),
            AIMessage(content="", tool_calls=calls),
            *[ToolMessage(content="Successfully read image", tool_call_id=f"c{i}") for i in range(len(paths))],
        ]

    def _viewed(self, tmp_path, names: list[str]) -> tuple[dict, dict]:
        viewed, payloads = {}, {}
        for name in names:
            f = tmp_path / f"{name}.png"
            payload = f"payload-{name}".encode()
            f.write_bytes(payload)
            viewed[f"/{name}.png"] = _meta_for(name, f, payload)
            payloads[name] = payload
        return viewed, payloads

    def test_whole_batch_injected_even_for_old_images(self, tmp_path):
        """本轮并行请求 3 张老图（都已看过更早轮）→ 3 张全部 base64 进窗口，
        另加最近 K 张历史。"""
        mw = ViewImageMiddleware()
        # 按时间顺序看过 a,b,c,d,e；本轮请求的批量是 a,b,c（老图）
        viewed, payloads = self._viewed(tmp_path, ["a", "b", "c", "d", "e"])
        messages = self._multi_view_turn(["/a.png", "/b.png", "/c.png"])
        request = _make_request(messages, state={"messages": [], "viewed_images": viewed})
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        import base64 as b64

        urls = [blk["image_url"]["url"] for blk in _image_url_blocks(sink[0].messages)]
        # 批量 3 张(a,b,c) + 历史 K 张中最新的 d,e → 全部 5 张都在
        assert len(urls) == 3 + _MAX_INJECTED_IMAGES
        for name in ("a", "b", "c", "d", "e"):
            assert any(b64.b64encode(f"payload-{name}".encode()).decode() in u for u in urls), name

    def test_batch_capped_at_limit(self, tmp_path):
        """病态轮（超过上限的并行批量）→ 只注入前 _MAX_SAME_TURN_IMAGES 张，
        其余降占位符——批量豁免不能变成无界后门。"""
        names = [f"img{i}" for i in range(_MAX_SAME_TURN_IMAGES + 3)]
        viewed, _payloads = self._viewed(tmp_path, names)
        mw = ViewImageMiddleware()
        messages = self._multi_view_turn([f"/{n}.png" for n in names])
        request = _make_request(messages, state={"messages": [], "viewed_images": viewed})
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        assert len(_image_url_blocks(sink[0].messages)) == _MAX_SAME_TURN_IMAGES
        # 溢出的批量图不得作为"最近历史"回流（否则上限失去意义）
        injected_text = "".join(b.get("text", "") for b in sink[0].messages[-1].content if isinstance(b, dict))
        overflowed = [n for n in names[len(names) - (len(names) - _MAX_SAME_TURN_IMAGES) :]]
        assert any(f"/{n}.png" in injected_text and "已查看图片" in injected_text for n in overflowed)

    def test_batch_paths_not_in_viewed_images_are_ignored(self):
        """批量里含失败路径（从未写入 viewed_images，例如越权路径报错）→
        不进窗口也不出占位符——与旧行为一致（只列真正看过的图）。"""
        mw = ViewImageMiddleware()
        messages = self._multi_view_turn(["/never-viewed.png"])
        request = _make_request(messages, state={"messages": [], "viewed_images": {}})
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))
        injected = sink[0].messages[-1]
        assert isinstance(injected, HumanMessage)
        # 空历史 → "No images have been viewed." 占位说明
        assert injected.content == [{"type": "text", "text": "No images have been viewed."}]

    def test_history_window_still_k_across_turns(self, tmp_path):
        """上一轮批量看过的图，到下一轮（无新 view 调用触发时本就不注入；
        有新单图调用时）只剩 K 张历史 + 新批量——跨轮不豁免。"""
        mw = ViewImageMiddleware()
        viewed, _ = self._viewed(tmp_path, ["a", "b", "c", "d", "e"])
        # 本轮只看一张新图 f
        f = tmp_path / "f.png"
        payload = b"payload-f"
        f.write_bytes(payload)
        viewed["/f.png"] = _meta_for("f", f, payload)
        messages = self._multi_view_turn(["/f.png"])
        request = _make_request(messages, state={"messages": [], "viewed_images": viewed})
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        import base64 as b64

        urls = [blk["image_url"]["url"] for blk in _image_url_blocks(sink[0].messages)]
        # 新批量 1 张 + 最近历史 d,e → 共 3 张；a,b,c 降占位符
        assert len(urls) == 1 + _MAX_INJECTED_IMAGES
        assert any(b64.b64encode(payload).decode() in u for u in urls)
        injected_text = "".join(b.get("text", "") for b in sink[0].messages[-1].content if isinstance(b, dict))
        for gone in ("a", "b", "c"):
            assert f"/{gone}.png" in injected_text and "已查看图片" in injected_text


class TestReViewRegressionWindow:
    """P0-重看 acceptance: viewing K+1 images then re-viewing the first must
    bring that image back into the window as base64.

    The state dict is built through the REAL reducer (merge_viewed_images)
    so this test doubles as the mutation anchor: under the old reducer (plain
    dict merge, re-viewed keys keep first-seen position) the re-viewed image
    stays outside the K window and this test fails.
    """

    def _viewed_state_after(self, view_order: list[str], files: dict) -> dict[str, dict]:
        state: dict[str, dict] | None = None
        for path in view_order:
            update = {path: files[path]}
            state = merge_viewed_images(state, update) if state is not None else update
        return state

    def test_reviewed_image_reenters_window(self, tmp_path):
        mw = ViewImageMiddleware()
        files = {}
        for name in ("a", "b", "c"):
            f = tmp_path / f"{name}.png"
            payload = f"payload-{name}".encode()
            f.write_bytes(payload)
            files[f"/{name}.png"] = _meta_for(name, f, payload)

        # view a, b, c (K+1 views), then RE-VIEW a via the real reducer
        viewed = self._viewed_state_after(["/a.png", "/b.png", "/c.png", "/a.png"], files)
        # recency order after the reducer fix: b, c, a
        assert list(viewed) == ["/b.png", "/c.png", "/a.png"]

        request = _make_request(
            [AIMessage(content="", tool_calls=[_view_image_call("c1", "/a.png")]), ToolMessage(content="ok", tool_call_id="c1")],
            state={"messages": [], "viewed_images": viewed},
        )
        sink: list = []
        mw.wrap_model_call(request, _capture_handler(sink))

        urls = [b["image_url"]["url"] for b in _image_url_blocks(sink[0].messages)]
        # 重看的 a 经同轮批量豁免必在窗口；另有最近 K 张历史（b, c）。
        # 注：批量豁免落地后，"a 不进窗口"的旧 reducer 变异锚点改由
        # test_thread_state_reducers.py 的 recency 单测承担（批量路径不再
        # 依赖 dict 顺序）；本测试守护重看行为的最终效果不回退。
        assert len(urls) == 1 + _MAX_INJECTED_IMAGES
        import base64 as b64

        assert any(b64.b64encode(b"payload-a").decode() in u for u in urls)
        # 3 图场景窗口装得下全部 → 无占位符
        placeholder_texts = [b.get("text", "") for b in sink[0].messages[-1].content if isinstance(b, dict) and "已查看图片" in b.get("text", "")]
        assert placeholder_texts == []


class TestMiddlewareRegistrationOrder:
    """ViewImage must register AFTER Summarization and DanglingToolCall.

    The wrap chain wires "first registered = outermost", so a later
    registration wraps inner — ViewImage then sees the summarized window and
    the Dangling-patched tool responses, which the K-window logic assumes.
    """

    def test_lead_agent_build_order(self, monkeypatch):
        from deerflow.agents.lead_agent import agent as lead_agent_module
        from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware
        from deerflow.config.app_config import AppConfig
        from deerflow.config.model_config import ModelConfig
        from deerflow.config.sandbox_config import SandboxConfig

        vision_model = ModelConfig(
            name="vision-model",
            display_name="vision-model",
            description=None,
            use="langchain_openai:ChatOpenAI",
            model="vision-model",
            supports_thinking=False,
            supports_vision=True,
        )
        app_config = AppConfig(
            models=[vision_model],
            sandbox=SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider"),
        )
        summarization_mock = MagicMock(name="summarization-middleware")
        monkeypatch.setattr(lead_agent_module, "get_app_config", lambda: app_config)
        monkeypatch.setattr(lead_agent_module, "_create_summarization_middleware", lambda **kwargs: summarization_mock)
        monkeypatch.setattr(lead_agent_module, "_create_todo_list_middleware", lambda is_plan_mode: None)

        middlewares = lead_agent_module._build_middlewares(
            {"configurable": {"is_plan_mode": False, "subagent_enabled": False}},
            model_name="vision-model",
            app_config=app_config,
        )

        idx_view = next(i for i, m in enumerate(middlewares) if isinstance(m, ViewImageMiddleware))
        idx_dangling = next(i for i, m in enumerate(middlewares) if isinstance(m, DanglingToolCallMiddleware))
        idx_summ = next(i for i, m in enumerate(middlewares) if m is summarization_mock)
        assert idx_dangling < idx_view, "ViewImage must wrap inside DanglingToolCall (sees patched window)"
        assert idx_summ < idx_view, "ViewImage must wrap inside Summarization (sees summarized window)"

    def test_factory_features_order(self):
        from langchain.agents.middleware import AgentMiddleware

        from deerflow.agents.factory import _assemble_from_features
        from deerflow.agents.features import RuntimeFeatures
        from deerflow.agents.middlewares.dangling_tool_call_middleware import DanglingToolCallMiddleware

        class _StubSummarization(AgentMiddleware):
            pass

        summarization_stub = _StubSummarization()
        chain, _extra_tools = _assemble_from_features(RuntimeFeatures(sandbox=False, summarization=summarization_stub, vision=True))

        idx_view = next(i for i, m in enumerate(chain) if isinstance(m, ViewImageMiddleware))
        idx_dangling = next(i for i, m in enumerate(chain) if isinstance(m, DanglingToolCallMiddleware))
        idx_summ = next(i for i, m in enumerate(chain) if m is summarization_stub)
        assert idx_dangling < idx_view
        assert idx_summ < idx_view


class TestReadImageAsDataUrlCompression:
    """D1 compression regression: the >2MB compression moved from the tool's
    store-state path to the middleware's on-demand read path (R-11)."""

    def _noise_png(self, tmp_path, threshold=100_000) -> tuple[bytes, object]:
        import io as _io

        import numpy as np
        from PIL import Image as PILImage

        arr = np.random.randint(0, 256, (1500, 1500, 3), dtype=np.uint8)
        buf = _io.BytesIO()
        PILImage.fromarray(arr).save(buf, format="PNG")
        large = buf.getvalue()
        assert len(large) > 500_000
        path = tmp_path / "large.png"
        path.write_bytes(large)
        return large, path

    def test_large_image_compressed_in_read_path(self, tmp_path, monkeypatch):
        import base64 as b64
        import importlib

        tool_mod = importlib.import_module("deerflow.tools.builtins.view_image_tool")
        monkeypatch.setattr(tool_mod, "_COMPRESSION_THRESHOLD", 100_000)
        large, path = self._noise_png(tmp_path)

        data_url = ViewImageMiddleware._read_image_as_data_url(str(path), "image/png", len(large))
        assert data_url is not None
        assert data_url.startswith("data:image/jpeg;base64,")  # PNG→JPEG 转换
        compressed = b64.b64decode(data_url.split(",", 1)[1])
        assert len(compressed) < len(large) // 2
        assert compressed[:3] == b"\xff\xd8\xff"  # JPEG magic

    def test_small_image_not_compressed(self, tmp_path):
        import base64 as b64

        path = tmp_path / "small.png"
        path.write_bytes(b"small-png-bytes")

        data_url = ViewImageMiddleware._read_image_as_data_url(str(path), "image/png", len(b"small-png-bytes"))
        assert data_url == f"data:image/png;base64,{b64.b64encode(b'small-png-bytes').decode()}"

    def test_changed_file_skipped(self, tmp_path):
        path = tmp_path / "changed.png"
        path.write_bytes(b"abc")

        assert ViewImageMiddleware._read_image_as_data_url(str(path), "image/png", 999) is None
