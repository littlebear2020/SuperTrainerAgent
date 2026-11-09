"""Patched ChatOpenAI that preserves thought_signature for Gemini thinking models.

When using Gemini with thinking enabled via an OpenAI-compatible gateway (e.g.
Vertex AI, Google AI Studio, or any proxy), the API requires that the
``thought_signature`` field on tool-call objects is echoed back verbatim in
every subsequent request.

The OpenAI-compatible gateway stores the raw tool-call dicts (including
``thought_signature``) in ``additional_kwargs["tool_calls"]``, but standard
``langchain_openai.ChatOpenAI`` only serialises the standard fields (``id``,
``type``, ``function``) into the outgoing payload, silently dropping the
signature.  That causes an HTTP 400 ``INVALID_ARGUMENT`` error:

    Unable to submit request because function call `<tool>` in the N. content
    block is missing a `thought_signature`.

This module fixes the problem by overriding ``_get_request_payload`` to
re-inject tool-call signatures back into the outgoing payload for any assistant
message that originally carried them.

B1b: Also overrides ``_convert_chunk_to_generation_chunk`` to capture the
360 gateway's real-bill ``amount`` from streaming usage chunks.  The base
implementation only extracts a white-list of token fields into
``usage_metadata``, so ``amount`` (a non-standard extension) is silently
dropped during streaming.  This patch writes it into
``response_metadata["amount"]`` so the journal's ``_extract_amount`` can
read it later.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models import LanguageModelInput
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)


def _normalize_cache_field(usage: dict) -> dict:
    """B2: rewrite 360/Ark's non-standard ``prompt_cache_hit_tokens`` to the
    OpenAI-standard ``prompt_tokens_details.cached_tokens``.

    LangChain's ``_create_usage_metadata`` only reads ``prompt_tokens_details.cached_tokens``
    → ``input_token_details.cache_read``. The 360/Ark gateway puts cache-hit info at the
    top level as ``prompt_cache_hit_tokens`` (Doubao's own field), which LangChain silently
    drops → ``cache_read`` is always 0 → console cache-aware pricing gets no data.

    This helper rewrites the usage dict **in place** and also returns it for convenience.
    Idempotent: if the field is absent or already under ``prompt_tokens_details``, no-op.
    """
    raw = usage.get("prompt_cache_hit_tokens")
    if raw is None:
        return usage
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return usage
    details = usage.get("prompt_tokens_details")
    if not isinstance(details, dict):
        details = {}
        usage["prompt_tokens_details"] = details
    details.setdefault("cached_tokens", val)
    return usage


class PatchedChatOpenAI(ChatOpenAI):
    """ChatOpenAI with ``thought_signature`` preservation for Gemini thinking via OpenAI gateway.

    When using Gemini with thinking enabled via an OpenAI-compatible gateway,
    the API expects ``thought_signature`` to be present on tool-call objects in
    multi-turn conversations.  This patched version restores those signatures
    from ``AIMessage.additional_kwargs["tool_calls"]`` into the serialised
    request payload before it is sent to the API.

    Usage in ``config.yaml``::

        - name: gemini-2.5-pro-thinking
          display_name: Gemini 2.5 Pro (Thinking)
          use: deerflow.models.patched_openai:PatchedChatOpenAI
          model: google/gemini-2.5-pro-preview
          api_key: $GEMINI_API_KEY
          base_url: https://<your-openai-compat-gateway>/v1
          max_tokens: 16384
          supports_thinking: true
          supports_vision: true
          when_thinking_enabled:
            extra_body:
              thinking:
                type: enabled

    R2 (2026-09-24 DEF-01'): also hosts the ``reasoning_effort_none_with_tools``
    guard — set ``reasoning_effort_none_with_tools: true`` on a model entry to
    force ``reasoning_effort="none"`` on any request that carries function
    tools (gpt-6 on the 360 gateway 400s on every non-none effort value once
    tools are bound). Declared as a real field (not via model kwargs) so it is
    consumed here and never reaches the wire.
    """

    reasoning_effort_none_with_tools: bool = False
    """Config-only guard flag (DEF-01'); see class docstring above."""

    def _get_request_payload(
        self,
        input_: LanguageModelInput,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict:
        """Get request payload with ``thought_signature`` preserved on tool-call objects.

        Overrides the parent method to re-inject ``thought_signature`` fields
        on tool-call objects that were stored in
        ``additional_kwargs["tool_calls"]`` by LangChain but dropped during
        serialisation.
        """
        # Capture the original LangChain messages *before* conversion so we can
        # access fields that the serialiser might drop.
        original_messages = self._convert_input(input_).to_messages()

        # Obtain the base payload from the parent implementation.
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)

        # R2 (2026-09-24 DEF-01'): when tools are bound and the model opts in,
        # force reasoning_effort to "none" in BOTH the top-level field and
        # extra_body. The OpenAI SDK merges extra_body over the top level
        # (``_merge_mappings(json_data, extra_json)`` — second mapping wins),
        # so clearing only the top level would be overridden by a thinking-tier
        # extra_body value (e.g. "high") on the wire.
        #
        # 360-gateway实测（spike 2026-09-24, temp/360_spike_0924_gpt6_tools.py）：
        # gpt-6 带 tools 时网关只认**嵌套** ``extra_body.reasoning_effort``（顶层键
        # 被忽略并回落注册默认档→400）；而 SDK 会把 extra_body kwarg 展平到顶层，
        # 嵌套对象唯有藏在展平键 ``extra_body`` 里才能存活到最终请求体。故守卫写
        # 双层结构：顶层 "none" + ``extra_body.extra_body.reasoning_effort="none"``
        # ——展平后即实测通过的 G 形态（顶层 none + 嵌套 extra_body none）。
        # Thinking tiers therefore map to none in tool-bearing (agent) sessions
        # for these models — accepted per review Q10; pure-chat paths
        # (title/suggestions) carry no tools and keep their effort semantics.
        if self.reasoning_effort_none_with_tools and payload.get("tools"):
            payload["reasoning_effort"] = "none"
            extra_body = payload.get("extra_body")
            merged = {**(extra_body if isinstance(extra_body, dict) else {}), "reasoning_effort": "none"}
            merged["extra_body"] = {**merged.get("extra_body", {}), "reasoning_effort": "none"}
            payload["extra_body"] = merged

        payload_messages = payload.get("messages", [])

        if len(payload_messages) == len(original_messages):
            for payload_msg, orig_msg in zip(payload_messages, original_messages):
                if payload_msg.get("role") == "assistant" and isinstance(orig_msg, AIMessage):
                    _restore_tool_call_signatures(payload_msg, orig_msg)
        else:
            # Fallback: match assistant-role entries positionally against AIMessages.
            ai_messages = [m for m in original_messages if isinstance(m, AIMessage)]
            assistant_payloads = [(i, m) for i, m in enumerate(payload_messages) if m.get("role") == "assistant"]
            for (_, payload_msg), ai_msg in zip(assistant_payloads, ai_messages):
                _restore_tool_call_signatures(payload_msg, ai_msg)

        return payload

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: Any,
        base_generation_info: dict,
    ) -> Any:
        """Capture 360 gateway's ``amount`` + normalize ``prompt_cache_hit_tokens`` (B1b + B2).

        B1b: The base implementation normalises usage into ``usage_metadata``
        (a white-list of token fields) and discards the raw dict, so the
        non-standard ``amount`` field is lost during streaming.  This
        override calls ``super()`` first, then copies ``amount`` from the
        chunk's ``usage`` dict into ``message_chunk.response_metadata["amount"]``
        so the journal can read it via the same two-level extract as the
        non-streaming path.

        B2: Rewrites ``prompt_cache_hit_tokens`` → ``prompt_tokens_details.cached_tokens``
        **before** calling ``super()``, so the parent's ``_create_usage_metadata``
        populates ``input_token_details.cache_read`` correctly. Without this, the
        360/Ark gateway's cache-hit field is silently dropped → cache_read is always 0.
        """
        # B2: Normalize cache field before the parent processes the chunk
        if isinstance(chunk, dict):
            usage = chunk.get("usage")
            if isinstance(usage, dict):
                _normalize_cache_field(usage)
        generation_chunk = super()._convert_chunk_to_generation_chunk(chunk, default_chunk_class, base_generation_info)

        # The base method returns None for chunks that produce no generation
        # (e.g. ``type == "content.delta"`` beta frames or ``delta is None``).
        # Bail out before touching ``.message`` — the accounting patch must
        # never break the streaming main loop.
        if generation_chunk is None:
            return None

        try:
            usage = chunk.get("usage") if isinstance(chunk, dict) else None
            if isinstance(usage, dict) and "amount" in usage:
                raw_amount = usage["amount"]
                if raw_amount is not None:
                    amount = float(raw_amount)
                    message = generation_chunk.message
                    if message.response_metadata is None:
                        message.response_metadata = {}
                    message.response_metadata["amount"] = amount
        except (TypeError, ValueError):
            logger.debug("patched_openai: failed to extract amount from streaming chunk", exc_info=True)

        return generation_chunk

    def _create_chat_result(self, response: Any, generation_info: dict | None = None) -> Any:
        """B2: Normalize ``prompt_cache_hit_tokens`` on the non-streaming response.

        The 360/Ark gateway puts cache-hit info at the top level of ``usage``;
        ``_create_usage_metadata`` only reads ``prompt_tokens_details.cached_tokens``.
        For dict responses we normalize in-place before ``super()``. For Pydantic
        model responses (OpenAI SDK), we convert to dict and normalize, then pass
        the dict — the parent handles both shapes.
        """
        if isinstance(response, dict):
            usage = response.get("usage")
            if isinstance(usage, dict):
                _normalize_cache_field(usage)
        elif hasattr(response, "model_dump"):
            rd = response.model_dump(exclude={"choices": {"__all__": {"message": {"parsed"}}}})
            usage = rd.get("usage")
            if isinstance(usage, dict):
                _normalize_cache_field(usage)
                response = rd  # pass the normalized dict to super()

        return super()._create_chat_result(response, generation_info)


def _restore_tool_call_signatures(payload_msg: dict, orig_msg: AIMessage) -> None:
    """Re-inject ``thought_signature`` onto tool-call objects in *payload_msg*.

    When the Gemini OpenAI-compatible gateway returns a response with function
    calls, each tool-call object may carry a ``thought_signature``.  LangChain
    stores the raw tool-call dicts in ``additional_kwargs["tool_calls"]`` but
    only serialises the standard fields (``id``, ``type``, ``function``) into
    the outgoing payload, silently dropping the signature.

    This function matches raw tool-call entries (by ``id``, falling back to
    positional order) and copies the signature back onto the serialised
    payload entries.
    """
    raw_tool_calls: list[dict] = orig_msg.additional_kwargs.get("tool_calls") or []
    payload_tool_calls: list[dict] = payload_msg.get("tool_calls") or []

    if not raw_tool_calls or not payload_tool_calls:
        return

    # Build an id → raw_tc lookup for efficient matching.
    raw_by_id: dict[str, dict] = {}
    for raw_tc in raw_tool_calls:
        tc_id = raw_tc.get("id")
        if tc_id:
            raw_by_id[tc_id] = raw_tc

    for idx, payload_tc in enumerate(payload_tool_calls):
        # Try matching by id first, then fall back to positional.
        raw_tc = raw_by_id.get(payload_tc.get("id", ""))
        if raw_tc is None and idx < len(raw_tool_calls):
            raw_tc = raw_tool_calls[idx]

        if raw_tc is None:
            continue

        # The gateway may use either snake_case or camelCase.
        sig = raw_tc.get("thought_signature") or raw_tc.get("thoughtSignature")
        if sig:
            payload_tc["thought_signature"] = sig
