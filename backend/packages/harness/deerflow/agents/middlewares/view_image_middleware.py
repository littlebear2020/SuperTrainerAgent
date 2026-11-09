"""Middleware injecting viewed images into the LLM inference window (not state).

2026-08-18 rewrite (view_image history bloat incident): base64 injection moved
from ``before_model`` state writes to a ``wrap_model_call`` inference-window
override, mirroring the proven DeerFlowSummarizationMiddleware /
DanglingToolCallMiddleware pattern. Consequences (all intentional):

- zero state writes ⇒ no ``RemoveMessage`` cleanup needed, and the transient
  image payloads never appear in ANY checkpoint / values event (the old
  before_model/after_model split left them briefly visible between the two
  supersteps — that leak is structurally gone);
- a K-image sliding window keeps base64 for only the ``_MAX_INJECTED_IMAGES``
  most recently viewed images (dict recency order, see
  ``merge_viewed_images``); older views degrade to lightweight text
  placeholders so the model still knows what it has seen and can re-call
  ``view_image`` on demand;
- a same-turn batch exemption (``_MAX_SAME_TURN_IMAGES``): every image
  requested by the current turn's own parallel view_image calls is injected
  whole, so "look at all N and summarize" lands in one shot (live-run
  evidence: a 2-wide window alone made a 6-image task churn through 20
  view calls and still leave 2 images unconfirmed).

Registration order matters: this middleware must be registered AFTER
Summarization and DanglingToolCallMiddleware so its wrapper (innermost in the
chain, "first registered = outermost") sees the summarized, patched window.
Pinned by tests.
"""

import asyncio
import base64
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, override
from uuid import uuid4

from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import ModelCallResult, ModelRequest, ModelResponse
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from deerflow.agents.thread_state import ThreadState

logger = logging.getLogger(__name__)

# Mirror the tool-side size cap as a defense-in-depth check. The tool
# enforces this at write time; the middleware re-checks at read time in
# case the file grew on disk between view and injection.
_MAX_IMAGE_BYTES = 20 * 1024 * 1024
# K-window (2026-08-18 用户拍板): inject base64 for at most the K most
# recently viewed images; every older view degrades to a text placeholder.
# Hardcoded by decision — revisit only if tuning evidence appears.
_MAX_INJECTED_IMAGES = 2
# Same-turn batch exemption (2026-08-18 用户拍板, live-run evidence): every
# image requested by THIS turn's parallel view_image calls gets base64, so
# "看这 6 张图并汇总" lands in one shot instead of churning through a 2-wide
# window (measured: 20 view calls for 6 images, 4/6 natively confirmed).
# The cap bounds a pathological turn; cross-turn history stays K-windowed,
# so the every-turn reinjection incident cannot recur.
_MAX_SAME_TURN_IMAGES = 8
_IMAGE_CONTEXT_MESSAGE_ID_PREFIX = "view-image-context:"
_IMAGE_CONTEXT_MESSAGE_MARKER_KEY = "deerflow_view_image_context"
_PLACEHOLDER_TEMPLATE = "[已查看图片: {path}, {mime}, {size}B — 内容已省略，如需再看请重新调用 view_image]"


class ViewImageMiddlewareState(ThreadState):
    """Reuse the thread state so reducer-backed keys keep their annotations."""


class ViewImageMiddleware(AgentMiddleware[ViewImageMiddlewareState]):
    """Injects viewed images into the model's inference window via ``wrap_model_call``.

    Trigger (unchanged from the before_model era): the last AIMessage contains
    ``view_image`` tool calls and every one of them has a completed ToolMessage.
    When it fires, a transient HumanMessage carrying this turn's full batch
    (capped) plus the K most recent other images (base64) and placeholders for
    older views is appended to the inference window only — state and
    checkpoints are never touched.

    ``viewed_images`` state carries only metadata (path, mime_type, size) in
    dict recency order (``merge_viewed_images`` moves re-viewed keys to the
    end), which is what makes the K-window "most recent" approximation work.
    """

    state_schema = ViewImageMiddlewareState

    @staticmethod
    def _is_image_context_message(message: object) -> bool:
        """Return whether a message is trusted transient image context."""
        return isinstance(message, HumanMessage) and bool(message.id) and message.id.startswith(_IMAGE_CONTEXT_MESSAGE_ID_PREFIX) and message.additional_kwargs.get(_IMAGE_CONTEXT_MESSAGE_MARKER_KEY) is True

    def _get_last_assistant_message(self, messages: list) -> AIMessage | None:
        """Get the last assistant message from the message list.

        Args:
            messages: List of messages

        Returns:
            Last AIMessage or None if not found
        """
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _has_view_image_tool(self, message: AIMessage) -> bool:
        """Check if the assistant message contains view_image tool calls.

        Args:
            message: Assistant message to check

        Returns:
            True if message contains view_image tool calls
        """
        if not hasattr(message, "tool_calls") or not message.tool_calls:
            return False

        return any(tool_call.get("name") == "view_image" for tool_call in message.tool_calls)

    def _all_tools_completed(self, messages: list, assistant_msg: AIMessage) -> bool:
        """Check if all tool calls in the assistant message have been completed.

        Args:
            messages: List of all messages
            assistant_msg: The assistant message containing tool calls

        Returns:
            True if all tool calls have corresponding ToolMessages
        """
        if not hasattr(assistant_msg, "tool_calls") or not assistant_msg.tool_calls:
            return False

        # Get all tool call IDs from the assistant message
        tool_call_ids = {tool_call.get("id") for tool_call in assistant_msg.tool_calls if tool_call.get("id")}

        # Find the index of the assistant message
        try:
            assistant_idx = messages.index(assistant_msg)
        except ValueError:
            return False

        # Get all ToolMessages after the assistant message
        completed_tool_ids = set()
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, ToolMessage) and msg.tool_call_id:
                completed_tool_ids.add(msg.tool_call_id)

        # Check if all tool calls have been completed
        return tool_call_ids.issubset(completed_tool_ids)

    @staticmethod
    def _read_image_as_data_url(actual_path: str, mime_type: str, expected_size: int) -> str | None:
        """Read image file and return a `data:` URL, or None on failure.

        Trust assumption: ``actual_path`` is set by ``view_image_tool``
        (server-side, validated against the allowed virtual roots at write
        time) and held in LangGraph-controlled state. Client input cannot
        reach this field, so the read scope is trusted. We still re-check
        size at read time to defend against TOCTOU growth and skip files
        exceeding ``_MAX_IMAGE_BYTES``.
        """
        from deerflow.tools.builtins.view_image_tool import _COMPRESSION_THRESHOLD, _compress_image

        try:
            file_path = Path(actual_path)
            if not file_path.exists() or not file_path.is_file():
                return None
            current_size = file_path.stat().st_size
            if current_size != expected_size:
                # File changed between view and inject - skip.
                return None
            if current_size > _MAX_IMAGE_BYTES:
                return None
            with open(file_path, "rb") as f:
                image_bytes = f.read()
            # D1 (2026-08-13): 超阈值图片自动压缩——压缩从 tool 的存 state 路径
            # 移到本按需读盘路径，语义不变（防 doubao vision 250s 超时）。
            if len(image_bytes) > _COMPRESSION_THRESHOLD:
                try:
                    image_bytes, mime_type = _compress_image(image_bytes)
                except Exception:
                    pass  # 压缩失败时原样发送（不阻塞用户流程）
            base64_data = base64.b64encode(image_bytes).decode("utf-8")
            return f"data:{mime_type};base64,{base64_data}"
        except OSError:
            return None

    @staticmethod
    def _same_turn_view_paths(messages: list) -> list[str]:
        """Ordered, deduped image paths requested by the last AIMessage's
        view_image calls — the "this turn's batch" for the exemption above."""
        last_assistant_msg = ViewImageMiddleware._get_last_assistant_message_static(messages)
        if last_assistant_msg is None or not getattr(last_assistant_msg, "tool_calls", None):
            return []
        paths: list[str] = []
        for tool_call in last_assistant_msg.tool_calls:
            if tool_call.get("name") != "view_image":
                continue
            path = (tool_call.get("args") or {}).get("image_path")
            if isinstance(path, str) and path and path not in paths:
                paths.append(path)
        return paths

    @staticmethod
    def _get_last_assistant_message_static(messages: list) -> AIMessage | None:
        for msg in reversed(messages):
            if isinstance(msg, AIMessage):
                return msg
        return None

    def _create_image_details_message(self, viewed_images: dict[str, dict[str, Any]], same_turn_paths: list[str] | None = None) -> list[str | dict]:
        """Build the content blocks for the inference-window image message.

        Window policy (2026-08-18, two user decisions):
        - **Same-turn batch exemption**: every image requested by this turn's
          own view_image calls is injected as base64 (capped at
          ``_MAX_SAME_TURN_IMAGES``) — a parallel "look at all N" lands whole.
        - **Cross-turn K-window**: for the remaining history, the last
          ``_MAX_INJECTED_IMAGES`` most recently viewed entries get base64;
          everything earlier degrades to a text placeholder carrying
          path/mime/size plus re-view guidance.

        Args:
            viewed_images: image_path -> {mime_type, size, actual_path} metadata
            same_turn_paths: image paths requested by the trigger turn's
                view_image calls (None = none, e.g. direct helper calls)

        Returns:
            List of content blocks (text and images) for the HumanMessage
        """
        if not viewed_images:
            # Return a properly formatted text block, not a plain string array
            return [{"type": "text", "text": "No images have been viewed."}]

        # This turn's batch (only paths that actually landed in viewed_images;
        # failed views were never stored) — capped to bound a pathological turn.
        batch = [p for p in (same_turn_paths or []) if p in viewed_images][:_MAX_SAME_TURN_IMAGES]
        # Cross-turn history window: the K most recent entries that this turn
        # did NOT request. Overflow batch paths (beyond the cap) are excluded
        # too — otherwise they would flow back in as "recent history" and the
        # cap would only ever cut images the model never asked to keep.
        requested = set(same_turn_paths or [])
        history_recent = [p for p in viewed_images if p not in requested][-_MAX_INJECTED_IMAGES:]
        window_paths = set(batch) | set(history_recent)

        # Build the message with image information
        content_blocks: list[str | dict] = [{"type": "text", "text": "Here are the images you've viewed:"}]

        for image_path, image_data in viewed_images.items():
            mime_type = image_data.get("mime_type", "unknown")
            actual_path = image_data.get("actual_path", "")
            expected_size = image_data.get("size", 0)

            if image_path not in window_paths:
                content_blocks.append({"type": "text", "text": _PLACEHOLDER_TEMPLATE.format(path=image_path, mime=mime_type, size=expected_size)})
                continue

            # Add text description
            content_blocks.append({"type": "text", "text": f"\n- **{image_path}** ({mime_type})"})

            # Read the image file on-demand and encode as base64 for the model
            if actual_path:
                data_url = self._read_image_as_data_url(actual_path, mime_type, expected_size)
                if data_url:
                    content_blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": data_url},
                        }
                    )
                else:
                    content_blocks.append({"type": "text", "text": f"  (file unavailable or changed on disk: {actual_path})"})

        return content_blocks

    def _should_inject_image_message(self, messages: list) -> bool:
        """Determine if we should inject an image details message.

        Args:
            messages: The inference window's message list

        Returns:
            True if we should inject the message
        """
        if not messages:
            return False

        # Get the last assistant message
        last_assistant_msg = self._get_last_assistant_message(messages)
        if not last_assistant_msg:
            return False

        # Check if it has view_image tool calls
        if not self._has_view_image_tool(last_assistant_msg):
            return False

        # Check if all tools have been completed
        if not self._all_tools_completed(messages, last_assistant_msg):
            return False

        # Check if we've already added an image details message
        # Look for a human message after the last assistant message that contains image details
        assistant_idx = messages.index(last_assistant_msg)
        for msg in messages[assistant_idx + 1 :]:
            if isinstance(msg, HumanMessage):
                if self._is_image_context_message(msg):
                    return False
                content_str = str(msg.content)
                if "Here are the images you've viewed" in content_str or "Here are the details of the images you've viewed" in content_str:
                    # Already added, don't add again
                    return False

        return True

    @staticmethod
    def _create_image_context_message(content: list[str | dict]) -> HumanMessage:
        """Create an identifiable, model-only image context message."""
        return HumanMessage(
            id=f"{_IMAGE_CONTEXT_MESSAGE_ID_PREFIX}{uuid4().hex}",
            content=content,
            additional_kwargs={
                "hide_from_ui": True,
                _IMAGE_CONTEXT_MESSAGE_MARKER_KEY: True,
            },
        )

    def _prepare_window(self, request: ModelRequest) -> ModelRequest | None:
        """Compute the inference-window override, or None when untouched.

        Two window-only transformations (never state writes):

        1. Drop legacy transient image-context messages left in the message
           history — mid-run threads checkpointed under the old
           before_model/after_model code would otherwise carry their base64
           payloads forever now that after_model cleanup is gone.
        2. When the trigger fires, append the window image message (this
           turn's batch fully + K most recent other views).

        Args:
            request: The incoming model request

        Returns:
            A new ModelRequest with the modified window, or None to pass through.
        """
        messages = [msg for msg in request.messages if not self._is_image_context_message(msg)]
        filtered = len(messages) != len(request.messages)

        inject = self._should_inject_image_message(messages)
        if not filtered and not inject:
            return None

        new_messages = messages
        if inject:
            viewed_images = dict(request.state.get("viewed_images") or {})
            same_turn_paths = self._same_turn_view_paths(messages)
            image_content = self._create_image_details_message(viewed_images, same_turn_paths)
            new_messages = [*messages, self._create_image_context_message(image_content)]
            logger.debug(
                "Injecting window image message (same-turn batch %d + history ≤%d base64 images) into inference window",
                min(len(same_turn_paths), _MAX_SAME_TURN_IMAGES),
                _MAX_INJECTED_IMAGES,
            )

        return request.override(messages=new_messages)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        """Inject the window image message into the inference window (sync).

        The state/checkpoint are never modified — only what the LLM sees for
        this single call, via ``request.override(messages=...)``.
        """
        modified = self._prepare_window(request)
        if modified is None:
            return handler(request)
        return handler(modified)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        """Inject the window image message into the inference window (async).

        Image reads + base64 encoding can be slow (up to 20MB), so offload
        the blocking work to a thread rather than stalling the event loop.
        """
        messages = [msg for msg in request.messages if not self._is_image_context_message(msg)]
        filtered = len(messages) != len(request.messages)
        inject = self._should_inject_image_message(messages)
        if not filtered and not inject:
            return await handler(request)

        new_messages = messages
        if inject:
            viewed_images = dict(request.state.get("viewed_images") or {})
            same_turn_paths = self._same_turn_view_paths(messages)
            # Image reads + base64 encoding can be slow (up to 20MB), so
            # offload the blocking work to a thread.
            image_content = await asyncio.to_thread(self._create_image_details_message, viewed_images, same_turn_paths)
            new_messages = [*messages, self._create_image_context_message(image_content)]
            logger.debug(
                "Injecting window image message (same-turn batch %d + history ≤%d base64 images) into inference window",
                min(len(same_turn_paths), _MAX_SAME_TURN_IMAGES),
                _MAX_INJECTED_IMAGES,
            )

        return await handler(request.override(messages=new_messages))
