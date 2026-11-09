"""TrainerAI vendor-add: LLM accounting wrapper for ViMax agents.

This file is ADDITIVE — upstream ViMax never touches it, so rebases are safe.

Wraps any ``BaseChatModel`` so that every ``.invoke``/``.ainvoke`` call
is recorded to the operations console via ``llm_usage_sink``. The wrapper
delegates all generation to the inner model (so LCEL chains like
``prompt | model | parser`` work unchanged) and records usage after
the call completes.

E1 design (from the 2026-08-08 plan):

- ``BaseChatModel`` subclass (not a bare proxy) so ``|`` composition works.
- ``__getattr__`` transparently delegates non-overridden attributes to the
  inner model — same BUG-B lesson as provider wrapper chains.
- Sync ``_generate`` uses fire-and-forget (ViMax's novel_compressor calls
  ``.invoke`` in a sync context).
- ``_llm_type`` property returns the inner's type.
- Errors are recorded as ``status="error"`` (no tokens) then re-raised —
  @retry's semantics are automatically correct.
"""

from __future__ import annotations

import logging
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

logger = logging.getLogger(__name__)


def _record_sync(
    *,
    model_name: str | None,
    usage_metadata: Any,
    response_metadata: Any,
    caller_tag: str,
    status: str = "success",
) -> None:
    """Fire-and-forget record for sync paths (novel_compressor:89)."""
    try:
        from app.gateway._shared.llm_usage_sink import record_llm_usage_fire_and_forget

        record_llm_usage_fire_and_forget(
            model_name=model_name,
            usage_metadata=usage_metadata,
            response_metadata=response_metadata,
            caller_tag=caller_tag,
            domain="vimax",
            status=status,
        )
    except Exception:
        logger.debug("vimax accounting: fire-and-forget failed", exc_info=True)


async def _record_async(
    *,
    model_name: str | None,
    usage_metadata: Any,
    response_metadata: Any,
    caller_tag: str,
    status: str = "success",
) -> None:
    """Async record for async paths."""
    try:
        from app.gateway._shared.llm_usage_sink import record_llm_usage

        await record_llm_usage(
            model_name=model_name,
            usage_metadata=usage_metadata,
            response_metadata=response_metadata,
            caller_tag=caller_tag,
            domain="vimax",
            status=status,
        )
    except Exception:
        logger.debug("vimax accounting: async record failed", exc_info=True)


class AccountingChatModel(BaseChatModel):
    """Delegates generation to an inner model, recording usage to the console.

    Usage::

        from vimax._trainerai_llm_accounting import wrap_for_accounting
        self.chat_model = wrap_for_accounting(init_chat_model(...), tag="script_planner")
    """

    def __init__(self, inner: BaseChatModel, *, tag: str = "vimax", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_tag", tag)

    # -- BaseChatModel required implementations --

    @property
    def _llm_type(self) -> str:
        return f"accounting({self._inner._llm_type})"

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Sync generation: delegate to inner, record via fire-and-forget."""
        try:
            result = self._inner.invoke(messages, stop=stop, **kwargs)
            # Build a ChatResult from the AIMessage
            _record_sync(
                model_name=getattr(result, "response_metadata", {}).get("model_name") if isinstance(getattr(result, "response_metadata", None), dict) else None,
                usage_metadata=getattr(result, "usage_metadata", None),
                response_metadata=getattr(result, "response_metadata", None),
                caller_tag=self._tag,
            )
            gen = ChatGeneration(message=result)
            return ChatResult(generations=[gen])
        except Exception:
            _record_sync(model_name=None, usage_metadata=None, response_metadata=None, caller_tag=self._tag, status="error")
            raise

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Async generation: delegate to inner, record via async sink."""
        try:
            result = await self._inner.ainvoke(messages, stop=stop, **kwargs)
            await _record_async(
                model_name=getattr(result, "response_metadata", {}).get("model_name") if isinstance(getattr(result, "response_metadata", None), dict) else None,
                usage_metadata=getattr(result, "usage_metadata", None),
                response_metadata=getattr(result, "response_metadata", None),
                caller_tag=self._tag,
            )
            gen = ChatGeneration(message=result)
            return ChatResult(generations=[gen])
        except Exception:
            await _record_async(model_name=None, usage_metadata=None, response_metadata=None, caller_tag=self._tag, status="error")
            raise

    # -- BUG-B lesson: __getattr__ transparently delegates everything else --

    def __getattr__(self, name: str) -> Any:
        # Called only when normal attribute lookup fails.
        # _inner and _tag are set via object.__setattr__ in __init__,
        # so they are found normally and never reach here.
        inner = object.__getattribute__(self, "_inner")
        return getattr(inner, name)


def wrap_for_accounting(inner: BaseChatModel, *, tag: str = "vimax") -> AccountingChatModel:
    """Convenience factory: wrap a chat model for ViMax LLM accounting."""
    return AccountingChatModel(inner, tag=tag)
