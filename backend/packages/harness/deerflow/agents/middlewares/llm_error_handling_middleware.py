"""LLM error handling middleware with retry/backoff and user-facing fallbacks."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import (
    ModelCallResult,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage
from langgraph.errors import GraphBubbleUp

from deerflow.agents.middlewares.tool_call_protocol_middleware import ToolProtocolError
from deerflow.config.app_config import AppConfig

logger = logging.getLogger(__name__)

_RETRIABLE_STATUS_CODES = {408, 409, 425, 429, 500, 502, 503, 504}


@dataclass(slots=True)
class _CircuitAdmission:
    """Per-call admission token stamped with the circuit generation at entry.

    Late-arriving success/failure votes or probe releases from an older
    generation are fenced out by ``_owns_current_circuit_generation`` so a
    stale call cannot settle a circuit it no longer belongs to.
    """

    generation: int = -1


# Marker stamped on every fallback AIMessage this middleware synthesizes.
# The middleware deliberately converts provider exceptions into an AIMessage so
# the graph terminates cleanly — but clean termination is NOT task success. This
# marker is the only authoritative signal of that distinction; without it a
# provider outage is journaled as a successful run and the failure sentence is
# handed to the user as if it were an answer.
#
# It lives in ``additional_kwargs`` because that survives every serialization
# path this repo uses (verified: ``model_dump()``, the worker's ``serialize()``
# in both "values" and "messages" modes, LangChain ``dumpd``/``load``, and the
# LangGraph checkpoint serializer). Display text is never a status protocol:
# error-looking prose without this marker stays an ordinary result.
FALLBACK_MARKER_KEY = "deerflow_error_fallback"
_BUSY_PATTERNS = (
    "server busy",
    "temporarily unavailable",
    "try again later",
    "please retry",
    "please try again",
    "overloaded",
    "high demand",
    "rate limit",
    "负载较高",
    "服务繁忙",
    "稍后重试",
    "请稍后重试",
)
_QUOTA_PATTERNS = (
    "insufficient_quota",
    "quota",
    "billing",
    "credit",
    "payment",
    "余额不足",
    "超出限额",
    "额度不足",
    "欠费",
)
_AUTH_PATTERNS = (
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "permission",
    "forbidden",
    "access denied",
    "无权",
    "未授权",
)


class LLMErrorHandlingMiddleware(AgentMiddleware[AgentState]):
    """Retry transient LLM errors and surface graceful assistant messages."""

    retry_max_attempts: int = 3
    retry_base_delay_ms: int = 1000
    retry_cap_delay_ms: int = 8000

    def __init__(self, *, app_config: AppConfig, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.circuit_failure_threshold = app_config.circuit_breaker.failure_threshold
        self.circuit_recovery_timeout_sec = app_config.circuit_breaker.recovery_timeout_sec

        # Circuit Breaker state
        self._circuit_lock = threading.Lock()
        self._circuit_failure_count = 0
        self._circuit_open_until = 0.0
        self._circuit_state = "closed"
        self._circuit_generation = 0
        self._circuit_probe_in_flight = False
        self._circuit_probe_token: object | None = None

    def _check_circuit(self, *, probe_token: object | None = None) -> bool:
        """Returns True if circuit is OPEN (fast fail), False otherwise."""
        with self._circuit_lock:
            now = time.time()

            if self._circuit_state == "open":
                if now < self._circuit_open_until:
                    return True
                self._circuit_generation += 1
                self._circuit_state = "half_open"
                self._circuit_probe_in_flight = False
                self._circuit_probe_token = None

            if self._circuit_state == "half_open":
                if self._circuit_probe_in_flight:
                    return True
                self._circuit_probe_in_flight = True
                self._circuit_probe_token = probe_token
                if isinstance(probe_token, _CircuitAdmission):
                    probe_token.generation = self._circuit_generation
                return False

            if isinstance(probe_token, _CircuitAdmission):
                probe_token.generation = self._circuit_generation
            return False

    def _owns_current_circuit_generation(self, admission: _CircuitAdmission | None) -> bool:
        """Whether ``admission`` may still settle the circuit's current state.

        ``None`` tolerates token-less callers. Otherwise the admission must
        carry the current generation, and in half_open it must BE the in-flight
        probe; in any other state the circuit must be closed.
        """
        if admission is None:
            return True
        if admission.generation != self._circuit_generation:
            return False
        if self._circuit_state == "half_open":
            return self._circuit_probe_token is admission
        return self._circuit_state == "closed"

    def _record_success(self, *, admission: _CircuitAdmission | None = None) -> None:
        with self._circuit_lock:
            if not self._owns_current_circuit_generation(admission):
                return
            if self._circuit_state != "closed" or self._circuit_failure_count > 0:
                logger.info("Circuit breaker reset (Closed). LLM service recovered.")
            if self._circuit_state == "half_open":
                self._circuit_generation += 1
            self._circuit_failure_count = 0
            self._circuit_open_until = 0.0
            self._circuit_state = "closed"
            self._circuit_probe_in_flight = False
            self._circuit_probe_token = None

    def _record_failure(self, *, admission: _CircuitAdmission | None = None) -> None:
        with self._circuit_lock:
            if not self._owns_current_circuit_generation(admission):
                return
            if self._circuit_state == "half_open":
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                self._circuit_generation += 1
                self._circuit_state = "open"
                self._circuit_probe_in_flight = False
                self._circuit_probe_token = None
                logger.error(
                    "Circuit breaker probe failed (Open). Will probe again after %ds.",
                    self.circuit_recovery_timeout_sec,
                )
                return

            self._circuit_failure_count += 1
            if self._circuit_failure_count >= self.circuit_failure_threshold:
                self._circuit_open_until = time.time() + self.circuit_recovery_timeout_sec
                if self._circuit_state != "open":
                    self._circuit_generation += 1
                    self._circuit_state = "open"
                    self._circuit_probe_in_flight = False
                    self._circuit_probe_token = None
                    logger.error(
                        "Circuit breaker tripped (Open). Threshold reached (%d). Will probe after %ds.",
                        self.circuit_failure_threshold,
                        self.circuit_recovery_timeout_sec,
                    )

    def _release_half_open_probe(self, *, admission: _CircuitAdmission | None = None) -> None:
        """Release the in-flight half-open probe without recording a failure.

        Used when something other than a classified success/failure consumes
        the probe (a GraphBubbleUp control-flow signal, a non-retriable error,
        or task cancellation), so the circuit can admit the next probe instead
        of fast-failing forever. The admission identity prevents an older call
        from releasing a different call's probe.
        """
        with self._circuit_lock:
            if not self._owns_current_circuit_generation(admission):
                return
            if self._circuit_state == "half_open":
                self._circuit_generation += 1
                self._circuit_probe_in_flight = False
                self._circuit_probe_token = None

    def _classify_error(self, exc: BaseException) -> tuple[bool, str]:
        detail = _extract_error_detail(exc)
        lowered = detail.lower()
        error_code = _extract_error_code(exc)
        status_code = _extract_status_code(exc)

        if _matches_any(lowered, _QUOTA_PATTERNS) or _matches_any(str(error_code).lower(), _QUOTA_PATTERNS):
            return False, "quota"
        if _matches_any(lowered, _AUTH_PATTERNS):
            return False, "auth"

        exc_name = exc.__class__.__name__
        if exc_name in {
            "APITimeoutError",
            "APIConnectionError",
            "InternalServerError",
            "ReadError",  # httpx.ReadError: connection dropped mid-stream
            "RemoteProtocolError",  # httpx: server closed connection unexpectedly
        }:
            return True, "transient"
        if status_code in _RETRIABLE_STATUS_CODES:
            return True, "transient"
        if _matches_any(lowered, _BUSY_PATTERNS):
            return True, "busy"

        return False, "generic"

    def _build_retry_delay_ms(self, attempt: int, exc: BaseException) -> int:
        retry_after = _extract_retry_after_ms(exc)
        if retry_after is not None:
            return retry_after
        backoff = self.retry_base_delay_ms * (2 ** max(0, attempt - 1))
        return min(backoff, self.retry_cap_delay_ms)

    def _build_retry_message(self, attempt: int, wait_ms: int, reason: str) -> str:
        seconds = max(1, round(wait_ms / 1000))
        reason_text = "provider is busy" if reason == "busy" else "provider request failed temporarily"
        return f"LLM request retry {attempt}/{self.retry_max_attempts}: {reason_text}. Retrying in {seconds}s."

    def _build_circuit_breaker_message(self) -> str:
        return "The configured LLM provider is currently unavailable due to continuous failures. Circuit breaker is engaged to protect the system. Please wait a moment before trying again."

    def _build_error_fallback_message(
        self,
        content: str,
        *,
        error_type: str,
        reason: str,
        detail: str,
    ) -> AIMessage:
        """Build a fallback AIMessage carrying the structured failure marker.

        Every return site that hands back a synthesized message instead of a
        real model response must go through here, so no fallback can reach a
        consumer looking like a genuine answer.
        """
        return AIMessage(
            content=content,
            additional_kwargs={
                FALLBACK_MARKER_KEY: True,
                "error_type": error_type,
                "error_reason": reason,
                "error_detail": detail,
            },
        )

    def _build_user_fallback_message(self, exc: BaseException, reason: str) -> AIMessage:
        return self._build_error_fallback_message(
            self._build_user_message(exc, reason),
            error_type=type(exc).__name__,
            reason=reason,
            detail=_extract_error_detail(exc),
        )

    def _build_circuit_breaker_fallback_message(self) -> AIMessage:
        return self._build_error_fallback_message(
            self._build_circuit_breaker_message(),
            error_type="CircuitBreakerOpen",
            reason="circuit_open",
            detail="LLM circuit breaker is open",
        )

    def _build_user_message(self, exc: BaseException, reason: str) -> str:
        detail = _extract_error_detail(exc)
        if reason == "quota":
            return "The configured LLM provider rejected the request because the account is out of quota, billing is unavailable, or usage is restricted. Please fix the provider account and try again."
        if reason == "auth":
            return "The configured LLM provider rejected the request because authentication or access is invalid. Please check the provider credentials and try again."
        if reason in {"busy", "transient"}:
            return "The configured LLM provider is temporarily unavailable after multiple retries. Please wait a moment and continue the conversation."
        return f"LLM request failed: {detail}"

    def _emit_retry_event(self, attempt: int, wait_ms: int, reason: str) -> None:
        try:
            from langgraph.config import get_stream_writer

            writer = get_stream_writer()
            writer(
                {
                    "type": "llm_retry",
                    "attempt": attempt,
                    "max_attempts": self.retry_max_attempts,
                    "wait_ms": wait_ms,
                    "reason": reason,
                    "message": self._build_retry_message(attempt, wait_ms, reason),
                }
            )
        except Exception:
            logger.debug("Failed to emit llm_retry event", exc_info=True)

    @staticmethod
    def _stamp_stop_reason(request: ModelRequest, value: str = "llm_error_fallback") -> None:
        """F6（2026-09-29）：兜底合成 AIMessage 时打 stop_reason（run 行可区分
        "provider 故障被迫收尾"与正常收尾）；setdefault 语义——更早的终止原因
        （如 loop_hard_limit）不覆盖。与 slow/empty 计数同模式。never raises。
        """
        try:
            ctx = getattr(getattr(request, "runtime", None), "context", None)
            if isinstance(ctx, dict):
                ctx.setdefault("stop_reason", value)
        except Exception:
            logger.debug("Failed to stamp stop_reason", exc_info=True)

    @override
    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelCallResult:
        _model = _resolve_model_name(request)
        admission = _CircuitAdmission()
        if self._check_circuit(probe_token=admission):
            self._stamp_stop_reason(request)
            return self._build_circuit_breaker_fallback_message()

        attempt = 1
        while True:
            try:
                response = handler(request)
                self._record_success(admission=admission)
                return response
            except GraphBubbleUp:
                # Preserve LangGraph control-flow signals (interrupt/pause/resume).
                self._release_half_open_probe(admission=admission)
                raise
            except ToolProtocolError:
                # 2026-10-15 评审二轮关键修复：正文伪调用二次违规的协议终断
                # 不是 provider 故障——必须穿透本兜底直达 worker 落 status=error
                # （决策 3），不得转 fallback AIMessage 伪装 success。
                self._release_half_open_probe(admission=admission)
                raise
            except Exception as exc:
                retriable, reason = self._classify_error(exc)
                if retriable and attempt < self.retry_max_attempts:
                    wait_ms = self._build_retry_delay_ms(attempt, exc)
                    logger.warning(
                        "Transient LLM error on attempt %d/%d (model=%s); retrying in %dms: %s",
                        attempt,
                        self.retry_max_attempts,
                        _model,
                        wait_ms,
                        _extract_error_detail(exc),
                    )
                    self._emit_retry_event(attempt, wait_ms, reason)
                    time.sleep(wait_ms / 1000)
                    attempt += 1
                    continue
                logger.warning(
                    "LLM call failed after %d attempt(s) (model=%s): %s",
                    attempt,
                    _model,
                    _extract_error_detail(exc),
                    exc_info=exc,
                )
                if retriable:
                    self._record_failure(admission=admission)
                else:
                    # A non-retriable error is not a provider-fault signal: release
                    # the probe so later requests can probe again (local hardening —
                    # upstream only releases on the cancellation path).
                    self._release_half_open_probe(admission=admission)
                self._stamp_stop_reason(request)
                return self._build_user_fallback_message(exc, reason)

    @override
    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelCallResult:
        _model = _resolve_model_name(request)
        admission = _CircuitAdmission()
        if self._check_circuit(probe_token=admission):
            self._stamp_stop_reason(request)
            return self._build_circuit_breaker_fallback_message()

        attempt = 1
        try:
            while True:
                try:
                    response = await handler(request)
                    self._record_success(admission=admission)
                    return response
                except GraphBubbleUp:
                    # Preserve LangGraph control-flow signals (interrupt/pause/resume).
                    self._release_half_open_probe(admission=admission)
                    raise
                except ToolProtocolError:
                    # 同步路径同款：协议终断穿透，不转兜底（2026-10-15 评审二轮）。
                    self._release_half_open_probe(admission=admission)
                    raise
                except Exception as exc:
                    retriable, reason = self._classify_error(exc)
                    if retriable and attempt < self.retry_max_attempts:
                        wait_ms = self._build_retry_delay_ms(attempt, exc)
                        logger.warning(
                            "Transient LLM error on attempt %d/%d (model=%s); retrying in %dms: %s",
                            attempt,
                            self.retry_max_attempts,
                            _model,
                            wait_ms,
                            _extract_error_detail(exc),
                        )
                        self._emit_retry_event(attempt, wait_ms, reason)
                        await asyncio.sleep(wait_ms / 1000)
                        attempt += 1
                        continue
                    logger.warning(
                        "LLM call failed after %d attempt(s) (model=%s): %s",
                        attempt,
                        _model,
                        _extract_error_detail(exc),
                        exc_info=exc,
                    )
                    if retriable:
                        self._record_failure(admission=admission)
                    else:
                        # A non-retriable error is not a provider-fault signal:
                        # release the probe so later requests can probe again
                        # (local hardening — upstream only releases on the
                        # cancellation path).
                        self._release_half_open_probe(admission=admission)
                    self._stamp_stop_reason(request)
                    return self._build_user_fallback_message(exc, reason)
        except asyncio.CancelledError:
            # 取消可能发生在 admission/provider 调用/retry 事件/backoff——非 provider 故障
            self._release_half_open_probe(admission=admission)
            raise


def _matches_any(detail: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in detail for pattern in patterns)


def _extract_error_code(exc: BaseException) -> Any:
    for attr in ("code", "error_code"):
        value = getattr(exc, attr, None)
        if value not in (None, ""):
            return value

    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            for key in ("code", "type"):
                value = error.get(key)
                if value not in (None, ""):
                    return value
    return None


def _extract_status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


def _extract_retry_after_ms(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None

    raw = None
    header_name = ""
    for key in ("retry-after-ms", "Retry-After-Ms", "retry-after", "Retry-After"):
        header_name = key
        if hasattr(headers, "get"):
            raw = headers.get(key)
        if raw:
            break
    if not raw:
        return None

    try:
        multiplier = 1 if "ms" in header_name.lower() else 1000
        return max(0, int(float(raw) * multiplier))
    except (TypeError, ValueError):
        try:
            target = parsedate_to_datetime(str(raw))
            delta = target.timestamp() - time.time()
            return max(0, int(delta * 1000))
        except (TypeError, ValueError, OverflowError):
            return None


def _extract_error_detail(exc: BaseException) -> str:
    detail = str(exc).strip()
    if detail:
        return detail
    message = getattr(exc, "message", None)
    if isinstance(message, str) and message.strip():
        return message.strip()
    return exc.__class__.__name__


def _resolve_model_name(request: ModelRequest) -> str:
    """Best-effort model name for per-model log aggregation.

    ModelRequest.model is the model instance: langchain models expose
    ``model_name`` (some only ``model``); test fakes may expose either.
    Falls back to the class name when neither attribute carries a truthy
    value (and to ``NoneType`` when the request carries no model at all,
    e.g. bare SimpleNamespace fakes).
    """
    model = getattr(request, "model", None)
    return getattr(model, "model_name", None) or getattr(model, "model", None) or type(model).__name__
