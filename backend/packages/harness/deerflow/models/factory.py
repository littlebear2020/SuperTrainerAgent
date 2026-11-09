import copy
import logging

from langchain.chat_models import BaseChatModel

from deerflow.config import get_app_config
from deerflow.config.app_config import AppConfig
from deerflow.reflection import resolve_class
from deerflow.tracing import build_tracing_callbacks

logger = logging.getLogger(__name__)


def _deep_merge_dicts(base: dict | None, override: dict) -> dict:
    """Recursively merge two dictionaries without mutating the inputs."""
    merged = dict(base or {})
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        elif isinstance(value, dict):
            merged[key] = copy.deepcopy(value)
        else:
            merged[key] = value
    return merged


def _vllm_disable_chat_template_kwargs(chat_template_kwargs: dict) -> dict:
    """Build the disable payload for vLLM/Qwen chat template kwargs."""
    disable_kwargs: dict[str, bool] = {}
    if "thinking" in chat_template_kwargs:
        disable_kwargs["thinking"] = False
    if "enable_thinking" in chat_template_kwargs:
        disable_kwargs["enable_thinking"] = False
    return disable_kwargs


def _enable_stream_usage_by_default(model_use_path: str, model_settings_from_config: dict) -> None:
    """Enable stream usage for OpenAI-compatible models unless explicitly configured.

    LangChain only auto-enables ``stream_usage`` for OpenAI models when no custom
    base URL or client is configured. DeerFlow frequently uses OpenAI-compatible
    gateways, so token usage tracking would otherwise stay empty and the
    TokenUsageMiddleware would have nothing to log.
    """
    if model_use_path != "langchain_openai:ChatOpenAI":
        return
    if "stream_usage" in model_settings_from_config:
        return
    if "base_url" in model_settings_from_config or "openai_api_base" in model_settings_from_config:
        model_settings_from_config["stream_usage"] = True


# Default chunk-gap budget for OpenAI-compatible streaming responses.
#
# langchain-openai raises ``StreamChunkTimeoutError`` after this many seconds
# without receiving a chunk. Its own default is 120s, which is too aggressive
# for reasoning models (doubao-thinking, qwen) whose first chunk can
# legitimately take 90~150s (C-02 / T6 root-cause item, 2026-08-13). We
# default to 240s; an explicit per-model ``stream_chunk_timeout`` in
# config.yaml wins. Previously this default only existed as a config.example
# + runbook hand-edit (K14).
_DEFAULT_STREAM_CHUNK_TIMEOUT_SECONDS: float = 240.0


def _apply_stream_chunk_timeout_default(model_class: type, model_settings_from_config: dict) -> None:
    """Inject a generous ``stream_chunk_timeout`` for OpenAI-compatible clients.

    ``stream_chunk_timeout`` is a field of langchain-openai's ``BaseChatOpenAI``,
    so it is accepted by ``ChatOpenAI`` and by every DeerFlow provider that
    subclasses it (PatchedChatOpenAI, PatchedChatDeepSeek, VllmChatModel, ...).
    We gate on ``issubclass(model_class, BaseChatOpenAI)`` rather than an
    explicit class-path allowlist so any OpenAI-compatible subclass inherits
    the default (and honors an explicit override) automatically.

    Behaviour:
    * ``BaseChatOpenAI`` subclass: an explicit config value is preserved; the
      default is injected otherwise.
    * Any other client (e.g. ChatAnthropic): drop the key so it is never
      forwarded to a constructor that does not declare it.
    """
    from langchain_openai.chat_models.base import BaseChatOpenAI

    if not issubclass(model_class, BaseChatOpenAI):
        model_settings_from_config.pop("stream_chunk_timeout", None)
        return
    if "stream_chunk_timeout" in model_settings_from_config:
        return
    model_settings_from_config["stream_chunk_timeout"] = _DEFAULT_STREAM_CHUNK_TIMEOUT_SECONDS


def create_chat_model(name: str | None = None, thinking_enabled: bool = False, *, app_config: AppConfig | None = None, attach_tracing: bool = True, **kwargs) -> BaseChatModel:
    """Create a chat model instance from the config.

    Args:
        name: The name of the model to create. If None, the first model in the config will be used.
        attach_tracing: When True (default), attach tracing callbacks directly to the model.
            Callers that attach tracing at the graph root MUST pass False to avoid duplicate spans.

    Returns:
        A chat model instance.
    """
    config = app_config or get_app_config()
    if name is None:
        name = config.models[0].name
    model_config = config.get_model_config(name)
    if model_config is None:
        raise ValueError(f"Model {name} not found in config") from None
    model_class = resolve_class(model_config.use, BaseChatModel)
    model_settings_from_config = model_config.model_dump(
        exclude_none=True,
        exclude={
            "use",
            "name",
            "display_name",
            "description",
            "supports_thinking",
            "supports_reasoning_effort",
            "reasoning_effort_none_with_tools",
            "when_thinking_enabled",
            "when_thinking_disabled",
            "thinking",
            "supports_vision",
            # Console cost-reporting annotation, not a provider parameter.
            # ``ModelConfig`` allows extra fields, so anything not excluded
            # here is forwarded into the chat-model constructor and reaches
            # the wire: leaving ``pricing`` in made every call fail with
            # "AsyncCompletions.create() got an unexpected keyword argument
            # 'pricing'". Read only by the operations console.
            "pricing",
        },
    )
    # Compute effective when_thinking_enabled by merging in the `thinking` shortcut field.
    # The `thinking` shortcut is equivalent to setting when_thinking_enabled["thinking"].
    has_thinking_settings = (model_config.when_thinking_enabled is not None) or (model_config.thinking is not None)
    effective_wte: dict = dict(model_config.when_thinking_enabled) if model_config.when_thinking_enabled else {}
    if model_config.thinking is not None:
        merged_thinking = {**(effective_wte.get("thinking") or {}), **model_config.thinking}
        effective_wte = {**effective_wte, "thinking": merged_thinking}
    if thinking_enabled and has_thinking_settings:
        if not model_config.supports_thinking:
            raise ValueError(f"Model {name} does not support thinking. Set `supports_thinking` to true in the `config.yaml` to enable thinking.") from None
        if effective_wte:
            # Deep-merge so sibling keys of the profile's top-level sections
            # (e.g. extra_body.tool_stream next to extra_body.thinking) survive
            # when thinking is on — same treatment as the disable path below.
            model_settings_from_config = _deep_merge_dicts(model_settings_from_config, effective_wte)
    if not thinking_enabled:
        if model_config.when_thinking_disabled is not None:
            # User-provided disable settings take full precedence
            model_settings_from_config.update(model_config.when_thinking_disabled)
        elif has_thinking_settings and effective_wte.get("extra_body", {}).get("thinking", {}).get("type"):
            # OpenAI-compatible gateway: thinking is nested under extra_body
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"thinking": {"type": "disabled"}},
            )
            model_settings_from_config["reasoning_effort"] = "minimal"
        elif has_thinking_settings and (disable_chat_template_kwargs := _vllm_disable_chat_template_kwargs(effective_wte.get("extra_body", {}).get("chat_template_kwargs") or {})):
            # vLLM uses chat template kwargs to switch thinking on/off.
            model_settings_from_config["extra_body"] = _deep_merge_dicts(
                model_settings_from_config.get("extra_body"),
                {"chat_template_kwargs": disable_chat_template_kwargs},
            )
        elif has_thinking_settings and effective_wte.get("thinking", {}).get("type"):
            # Native langchain_anthropic: thinking is a direct constructor parameter
            model_settings_from_config["thinking"] = {"type": "disabled"}
    if not model_config.supports_reasoning_effort:
        kwargs.pop("reasoning_effort", None)
        model_settings_from_config.pop("reasoning_effort", None)

    _enable_stream_usage_by_default(model_config.use, model_settings_from_config)

    # R2 (2026-09-24 DEF-01'): forward the config-only guard flag to model
    # classes that declare it (currently PatchedChatOpenAI). Excluded from the
    # model_dump above so it never reaches providers that don't declare it —
    # same wire-leak precaution as ``pricing``.
    if getattr(model_config, "reasoning_effort_none_with_tools", False):
        if "reasoning_effort_none_with_tools" in getattr(model_class, "model_fields", {}):
            model_settings_from_config["reasoning_effort_none_with_tools"] = True
        else:
            logger.warning(
                "Model %s sets reasoning_effort_none_with_tools=true but its class %s does not implement the guard (requires deerflow.models.patched_openai:PatchedChatOpenAI or a subclass); the flag is ignored.",
                name,
                model_config.use,
            )

    # For Codex Responses API models: map thinking mode to reasoning_effort
    from deerflow.models.openai_codex_provider import CodexChatModel

    if issubclass(model_class, CodexChatModel):
        # The ChatGPT Codex endpoint currently rejects max_tokens/max_output_tokens.
        model_settings_from_config.pop("max_tokens", None)

        # Use explicit reasoning_effort from frontend if provided (low/medium/high)
        explicit_effort = kwargs.pop("reasoning_effort", None)
        if not thinking_enabled:
            model_settings_from_config["reasoning_effort"] = "none"
        elif explicit_effort and explicit_effort in ("low", "medium", "high", "xhigh"):
            model_settings_from_config["reasoning_effort"] = explicit_effort
        elif "reasoning_effort" not in model_settings_from_config:
            model_settings_from_config["reasoning_effort"] = "medium"

    # For MindIE models: enforce conservative retry defaults.
    # Timeout normalization is handled inside MindIEChatModel itself.
    if getattr(model_class, "__name__", "") == "MindIEChatModel":
        # Enforce max_retries constraint to prevent cascading timeouts.
        model_settings_from_config["max_retries"] = model_settings_from_config.get("max_retries", 1)

    # Ensure stream_usage is enabled so that token usage metadata is available
    # in streaming responses.  LangChain's BaseChatOpenAI only defaults
    # stream_usage=True when no custom base_url/api_base is set, so models
    # hitting third-party endpoints (e.g. doubao, deepseek) silently lose
    # usage data.  We default it to True unless explicitly configured.
    if "stream_usage" not in model_settings_from_config and "stream_usage" not in kwargs:
        if "stream_usage" in getattr(model_class, "model_fields", {}):
            model_settings_from_config["stream_usage"] = True

    _apply_stream_chunk_timeout_default(model_class, model_settings_from_config)

    model_instance = model_class(**kwargs, **model_settings_from_config)

    if attach_tracing:
        callbacks = build_tracing_callbacks()
        if callbacks:
            existing_callbacks = model_instance.callbacks or []
            model_instance.callbacks = [*existing_callbacks, *callbacks]
            logger.debug(f"Tracing attached to model '{name}' with providers={len(callbacks)}")
    return model_instance
