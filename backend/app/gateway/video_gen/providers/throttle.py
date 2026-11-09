"""ThrottledProvider — rate-limiting + retry wrapper for external providers.

Wraps any provider with:
  - AsyncLimiter (token bucket) for QPS control
  - Exponential backoff retry on transient errors
  - Circuit breaker for sustained failures
"""

from __future__ import annotations

import asyncio
import logging
import random
import threading
import time
from collections import OrderedDict
from typing import Any, Final

import httpx
from aiolimiter import AsyncLimiter

from app.gateway._shared.task_framework.metrics import metrics
from deerflow.runtime.user_context import get_effective_user_id

from .base import AIVisualImageProvider, AIVisualVideoProvider, ImageRef, VideoRef

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base error for provider failures."""


class RateLimitError(ProviderError):
    """HTTP 429 or provider-specific rate limit."""


class ProviderUnavailableError(ProviderError):
    """Provider is down or circuit breaker is open."""


def _build_extra_from_ref(ref: Any, sink_label: str | None = None) -> dict:
    """Extract C1 metadata fields from an ImageRef/VideoRef.

    Fields are sparse — only included when the Ref actually carries them.
    ``sink_label`` (C1 path A) comes from the registry name and is more
    reliable than the raw_usage sub-dict (which never has ``model``).
    """
    extra: dict[str, Any] = {}
    if sink_label:
        extra["model"] = sink_label
    # Width / height
    width = getattr(ref, "width", None)
    height = getattr(ref, "height", None)
    if width:
        extra["width"] = width
    if height:
        extra["height"] = height
    # Duration (VideoRef only)
    duration = getattr(ref, "duration", None)
    if duration is not None:
        extra["duration"] = duration
    # total_tokens from raw_usage (seedance-ark returns this)
    raw_usage = getattr(ref, "raw_usage", None)
    if isinstance(raw_usage, dict):
        total_tokens = raw_usage.get("total_tokens")
        if total_tokens is not None:
            extra["total_tokens"] = int(total_tokens)
        resolution = raw_usage.get("resolution")
        if resolution:
            extra["resolution"] = resolution
        ratio = raw_usage.get("ratio")
        if ratio:
            extra["ratio"] = ratio
    return extra


def _counts_toward_breaker(exc: BaseException) -> bool:
    """T3（2026-08-17 H3 duration 400 级联教训）：Task 14 不变量收窄。

    非可重试异常仍计数（防慢性故障永不跳闸），但带 4xx status_code 的**确定性
    客户端错误**除外--重试永不成功、换一帧也不影响其它帧，把它计为 provider
    故障等于把请求级 bug 放大成整任务大面积占位。429 仍计（限流=provider 健康
    信号；实际 429 在 provider 层先抛 RateLimitError，此分支为防御纵深）。
    """
    sc = getattr(exc, "status_code", None)
    if isinstance(sc, int) and 400 <= sc < 500 and sc != 429:
        return False
    return True


def _backoff(attempt: int) -> float:
    return (2**attempt) + random.random()


def _make_limiter(qps: float) -> AsyncLimiter:
    """Create an AsyncLimiter from a QPS value.

    AsyncLimiter(max_rate=N) requires acquiring ≤ N tokens per call.
    When QPS < 1.0, we convert to equivalent (1 request per 1/qps seconds)
    to avoid the "Can't acquire more than the maximum capacity" error.
    """
    if qps >= 1.0:
        return AsyncLimiter(max_rate=qps, time_period=1)
    return AsyncLimiter(max_rate=1, time_period=1.0 / qps)


#: Task 24 / F11：每个进程最多保留多少个用户限流桶。超出后按 LRU 淘汰。
#: 桶是纯内存对象（AsyncLimiter），淘汰只丢失该用户的**瞬时**配额状态，
#: 下次调用重建即可，无正确性影响；不设上限则是无界字典 = 内存泄漏。
DEFAULT_MAX_USER_BUCKETS: Final[int] = 64


class _PerUserLimiterMixin:
    """按 ``user_id`` 分桶的限流器持有者（Task 24 / F11）。

    修复前两个 Throttled*Provider 各持**一个** ``self._limiter``，随
    ProviderRegistry 每服务实例建一次 → 进程内全用户共享一个 QPS 桶。
    schema 允许 ``n_scenes ≤ 200``，一个用户提交 200 帧任务即可长时间占满
    共享桶，其余人的生图/生视频全部排在其后。

    用户身份从 ``get_effective_user_id()`` 取（task-local ContextVar，
    无上下文时回落 ``"default"``），因此**不需要**改 visual_sources →
    providers 这条链上任何函数签名。

    注意：**只分限流，不分断路器**。供应商挂掉是全局事实；按用户分断路器会
    让每个用户各自把它打死一遍（10 人 × 5 次失败才跳闸，等于没有断路器）。
    """

    _qps: float
    _limiters: OrderedDict[str, AsyncLimiter]
    _max_user_buckets: int

    def _init_limiters(self, qps: float, max_user_buckets: int) -> None:
        self._qps = qps
        self._limiters = OrderedDict()
        self._max_user_buckets = max(1, max_user_buckets)

    def _limiter_for_current_user(self) -> AsyncLimiter:
        user_id = get_effective_user_id()
        limiter = self._limiters.get(user_id)
        if limiter is None:
            limiter = _make_limiter(self._qps)
            self._limiters[user_id] = limiter
            if len(self._limiters) > self._max_user_buckets:
                self._limiters.popitem(last=False)  # LRU 淘汰最久未用
        else:
            self._limiters.move_to_end(user_id)
        return limiter


def _record_provider_call(
    provider: str,
    method: str,
    elapsed: float,
    status: str,
    *,
    amount: float | None = None,
    sink_provider: str | None = None,
    extra: dict | None = None,
) -> None:
    labels = {"provider": provider, "method": method, "result": status}
    metrics.inc("video_gen_provider_calls_total", labels=labels, help_text="Total provider API calls by result")
    metrics.observe("video_gen_provider_latency_seconds", elapsed, labels={"provider": provider}, help_text="Provider call latency in seconds")
    # F1: write to external_api_calls table (fire-and-forget)
    try:
        import asyncio

        from app.gateway._shared.llm_usage_sink import record_external_api_call

        elapsed_ms = int(elapsed * 1000) if elapsed else None
        try:
            loop = asyncio.get_running_loop()
            # sink_provider: sink 行记录真实 provider 名（Ref.source，如 gpt-image-2），
            # 让 console 的 media_cost_by_provider 真按 provider 聚合；metrics 标签
            # 保持 pipeline 级（ai_image/ai_video）不变，保监控连续性。
            # C1: extra carries duration/width/height/tokens/model for cost estimation.
            loop.create_task(record_external_api_call(provider=sink_provider or provider, method=method, status=status, elapsed_ms=elapsed_ms, amount=amount, extra=extra))
        except RuntimeError:
            pass  # No running loop — metrics still recorded above
    except Exception:
        pass  # Never let tracking break the provider call


# ── Circuit Breaker ──────────────────────────────────────────────────────


# Registry for Prometheus gauge snapshot
_all_breakers: dict[str, CircuitBreaker] = {}


class CircuitBreaker:
    """Lightweight circuit breaker — no external dependency.

    States: closed (normal) → open (failing) → half-open (probing) → closed.

    When open, all calls raise ProviderUnavailableError immediately.
    After ``reset_timeout`` seconds, one probe call is allowed (half-open).
    If the probe succeeds, the breaker closes; if it fails, the open period resets.
    """

    def __init__(self, *, failure_threshold: int = 5, reset_timeout: float = 30.0) -> None:
        self._failure_threshold = failure_threshold
        self._reset_timeout = reset_timeout
        self._failure_count = 0
        self._last_failure_time: float = 0.0
        self._open = False
        # Task 14: 半开探测占用标志。修前 is_open 是**纯读属性**，超时后对每个并发
        # 协程都返回 False —— docstring 承诺「allow one probe」但代码零 gating，
        # 供应商刚缓过来就被 N 路并发再打死。
        self._probe_in_flight = False
        self._lock = threading.Lock()

    @property
    def is_open(self) -> bool:
        """是否应拦截流量。半开窗口下返回 False（放行），但**必须**配合
        :meth:`try_acquire_probe` 才能保证只放一路——单纯读这个属性不构成互斥。
        """
        with self._lock:
            return self._is_open_locked()

    def _is_open_locked(self) -> bool:
        if not self._open:
            return False
        if time.monotonic() - self._last_failure_time >= self._reset_timeout:
            return False  # half-open
        return True

    def try_acquire_probe(self) -> bool:
        """尝试取得「本次调用可以打到供应商」的许可（Task 14）。

        - 断路器闭合：直接放行（不占用探测位）
        - 完全打开（未到 reset_timeout）：拒绝
        - 半开：**只有第一个**调用者拿到许可，其余拒绝；许可由随后的
          ``record_success`` / ``record_failure`` 释放

        调用方拿到 False 时应抛 ``ProviderUnavailableError``，与断路器打开同义。
        """
        with self._lock:
            if not self._open:
                return True
            if self._is_open_locked():
                return False  # 仍在打开窗口内
            # 半开：单路探测
            if self._probe_in_flight:
                return False
            self._probe_in_flight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._failure_count = 0
            self._open = False
            self._probe_in_flight = False

    def record_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.monotonic()
            # 探测失败 → 释放探测位并重新计时（_last_failure_time 已更新，
            # 于是 _is_open_locked 重新返回 True，其余协程继续被拦）
            self._probe_in_flight = False
            if self._failure_count >= self._failure_threshold:
                self._open = True
            logger.warning(
                "Circuit breaker opened after %d failures",
                self._failure_count,
            )


def register_breaker(name: str, breaker: CircuitBreaker) -> None:
    """Register a circuit breaker for Prometheus gauge scraping."""
    _all_breakers[name] = breaker


def snapshot_circuit_gauges() -> None:
    """Update ``video_gen_provider_circuit_open`` gauge for all registered breakers."""
    for name, breaker in _all_breakers.items():
        metrics.set("video_gen_provider_circuit_open", float(breaker.is_open), labels={"provider": name}, help_text="1 if provider circuit breaker is open, 0 if closed")


class ThrottledAIImageProvider(_PerUserLimiterMixin):
    """Throttled + retry + circuit-breaker wrapper for AI image generation."""

    def __init__(
        self,
        inner: AIVisualImageProvider,
        *,
        qps: float = 1.0,
        max_retries: int = 3,
        circuit_failure_threshold: int = 5,
        circuit_reset_timeout: float = 30.0,
        max_user_buckets: int = DEFAULT_MAX_USER_BUCKETS,
        sink_label: str | None = None,
    ) -> None:
        self._inner = inner
        self._sink_label = sink_label
        self._init_limiters(qps, max_user_buckets)
        self._max_retries = max_retries
        self._breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            reset_timeout=circuit_reset_timeout,
        )
        register_breaker("ai_image", self._breaker)

    async def generate_image(self, prompt: str, **kwargs) -> ImageRef:
        # Task 14: 用探测许可而非纯读 is_open —— 半开窗口下只放一路探测，
        # 否则供应商刚缓过来就被 N 路并发再打死。
        if not self._breaker.try_acquire_probe():
            raise ProviderUnavailableError("AI image provider circuit breaker is open")
        start = time.monotonic()
        for attempt in range(self._max_retries + 1):
            try:
                # E10: limiter 只包单次实际请求；退避 sleep 在锁外（不占 QPS 窗口）
                # Task 24: 桶按当前用户取，避免一人大任务占满全组配额
                async with self._limiter_for_current_user():
                    result = await self._inner.generate_image(prompt, **kwargs)
                # B1 修复（2026-08-09 评审）：amount/source 必须从 Ref 透传到 sink——
                # 修前只透到 Ref 就断，media_cost_by_provider 生产恒空（测试全绿假象）。
                _record_provider_call(
                    "ai_image",
                    "generate_image",
                    time.monotonic() - start,
                    "ok",
                    amount=getattr(result, "cost_cny", None),
                    sink_provider=getattr(result, "source", "") or None,
                    extra=_build_extra_from_ref(result, self._sink_label),
                )
                self._breaker.record_success()
                return result
            # Task 23 / F8: ProviderUnavailableError 纳入重试。修前它有独立
            # except 分支 → record_failure 后**立即 raise，零重试**。而
            # seedance-ark 是默认 video provider，供应商一次瞬时 500 就废掉整帧；
            # 回滚路径 qihu 的同类错误反有 3 次重试 —— 默认路径比回滚路径更脆弱。
            # 注意：「断路器已开」那次 raise 在 for 循环之外（上方 try_acquire_probe），
            # 所以放进重试元组不会自旋。
            except (RateLimitError, TimeoutError, ConnectionError, ProviderUnavailableError, httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self._max_retries:
                    status = "unavailable" if isinstance(exc, ProviderUnavailableError) else "error"
                    _record_provider_call("ai_image", "generate_image", time.monotonic() - start, status)
                    if _counts_toward_breaker(exc):
                        self._breaker.record_failure()
                    raise
                delay = _backoff(attempt)
                logger.warning("AI image provider retry %d/%d in %.1fs", attempt + 1, self._max_retries, delay)
                await asyncio.sleep(delay)  # E10: 锁外退避
            except Exception as exc:
                # Task 14: 非「可重试」类异常也必须计失败。修前只对
                # RateLimitError/TimeoutError/ConnectionError/ProviderUnavailableError
                # 调 record_failure —— ValueError / RuntimeError / 裸 5xx 全部逃逸不计，
                # 于是**慢性故障的供应商永不跳闸**，每个任务都去撞一遍。
                # 这里不重试（语义不变：这类错误重试不会变好），只补计数后原样抛出。
                _record_provider_call("ai_image", "generate_image", time.monotonic() - start, "error")
                if _counts_toward_breaker(exc):
                    self._breaker.record_failure()
                raise
        raise ProviderError("unreachable")


class ThrottledAIVideoProvider(_PerUserLimiterMixin):
    """Throttled + retry + circuit-breaker wrapper for AI video generation."""

    def __init__(
        self,
        inner: AIVisualVideoProvider,
        *,
        qps: float = 0.5,
        max_retries: int = 5,
        circuit_failure_threshold: int = 5,
        circuit_reset_timeout: float = 30.0,
        max_user_buckets: int = DEFAULT_MAX_USER_BUCKETS,
        sink_label: str | None = None,
    ) -> None:
        self._inner = inner
        self._sink_label = sink_label
        self._init_limiters(qps, max_user_buckets)
        self._max_retries = max_retries
        self._breaker = CircuitBreaker(
            failure_threshold=circuit_failure_threshold,
            reset_timeout=circuit_reset_timeout,
        )
        register_breaker("ai_video", self._breaker)

    @property
    def supports_image_urls(self) -> bool:
        """R3: 透传被包裹 provider 的多参考图能力（未声明→False）。"""
        return getattr(self._inner, "supports_image_urls", False)

    @property
    def max_video_duration(self) -> float:
        """P0-2: 透传内层 provider 的单镜时长上限（seedance-ark=15，qihu 默认 8）。"""
        return getattr(self._inner, "max_video_duration", 8.0)

    @property
    def min_video_duration(self) -> float:
        """Task 3: 透传内层 provider 的单镜时长下限（seedance-ark=4）。未声明→0.0。

        2026-08-03 修 BUG-B：漏透传本属性时，``ai_video.fill()`` 的
        ``getattr(provider, "min_video_duration", 0.0)`` 在**生产包裹链**上恒取到
        兜底 0.0（生产 provider 一律经 Throttle→Fallback 包裹），栅格对齐整体失效。
        原单测用裸 MagicMock 直接声明这些属性，绕过包裹层 → 假绿。
        """
        return float(getattr(self._inner, "min_video_duration", 0.0) or 0.0)

    @property
    def integer_seconds_only(self) -> bool:
        """Task 3: 透传内层 provider 是否只接受整数秒（seedance-ark=True）。未声明→False。

        同上（BUG-B）：漏透传会让 8.9s 旁白直发浮点时长，落到 Ark 的
        ``int(round(8.9))``；而 Python ``round()`` 是 banker's rounding，
        ``round(8.5)=8`` —— 方向与设计的「向上取整（宁长勿短）」相反。
        """
        return bool(getattr(self._inner, "integer_seconds_only", False))

    @property
    def supports_tail_frame(self) -> bool:
        """Phase 2: 透传内层 provider 是否支持 return_last_frame（seedance-ark=True）。未声明→False。

        同 BUG-B：漏透传会让续写编排在生产包裹链上恒取 False，官方尾帧 i2v 永远不触发。
        """
        return bool(getattr(self._inner, "supports_tail_frame", False))

    @property
    def multi_image_truncated(self) -> bool:
        """审查偏离项 3（2026-08-19）：透传内层 provider 的多图截断打标。未声明→False。

        同 BUG-B 教训：无透传 property 时 ai_video 侧 getattr 在生产包裹链上恒取
        False，H3/happyhorse 丢参考图对用户完全不可见。
        """
        return bool(getattr(self._inner, "multi_image_truncated", False))

    def reset_multi_image_truncated(self) -> None:
        """转发清零到内层（provider 单例跨任务；ai_video render 开始调用）。"""
        reset = getattr(self._inner, "reset_multi_image_truncated", None)
        if callable(reset):
            reset()

    async def generate_video(self, prompt: str, *, duration: float = 30.0, **kwargs) -> VideoRef:
        # Task 14: 用探测许可而非纯读 is_open —— 半开窗口下只放一路探测，
        # 否则供应商刚缓过来就被 N 路并发再打死。
        if not self._breaker.try_acquire_probe():
            raise ProviderUnavailableError("AI video provider circuit breaker is open")
        start = time.monotonic()
        # E2 (Task 15): 提交与轮询解耦，避免重试重复扣费。
        #
        # 修前把 (submit + poll) 当整体重试：**轮询**阶段超时（供应商慢/卡住）→ 外层
        # 重试 → **重新 submit** → 供应商开出新任务，旧任务照跑照计费。一次逻辑镜头
        # 可扣 2-3 次；叠加 ViMax pipeline 级重试（max_retries=2）可达 6 次，且无幂等键。
        #
        # 现在：提交成功即记住远端 task_id；后续重试只重连轮询。仅当提交本身失败
        # （供应商侧未开出任务，不存在重复计费）才允许重新提交。
        # provider 未暴露 submit/poll 时回落合体入口（qihu 等向后兼容）。
        split = hasattr(self._inner, "submit_video") and hasattr(self._inner, "poll_video")
        remote_task_id: str | None = None

        for attempt in range(self._max_retries + 1):
            try:
                # E10: limiter 只包单次实际请求；退避 sleep 在锁外（不占 QPS 窗口）
                # Task 24: 桶按当前用户取，避免一人大任务占满全组配额
                if split:
                    if remote_task_id is None:
                        async with self._limiter_for_current_user():
                            remote_task_id = await self._inner.submit_video(prompt, duration=duration, **kwargs)
                    result = await self._inner.poll_video(remote_task_id, duration=duration)
                else:
                    async with self._limiter_for_current_user():
                        result = await self._inner.generate_video(prompt, duration=duration, **kwargs)
                # B1 修复（2026-08-09 评审）：同 image 路径——amount/source 透传到 sink。
                _record_provider_call(
                    "ai_video",
                    "generate_video",
                    time.monotonic() - start,
                    "ok",
                    amount=getattr(result, "cost_cny", None),
                    sink_provider=getattr(result, "source", "") or None,
                    extra=_build_extra_from_ref(result, self._sink_label),
                )
                self._breaker.record_success()
                return result
            # Task 23 / F8: 同 generate_image —— ProviderUnavailableError 纳入重试。
            # seedance-ark 提交期 5xx 抛的正是这个类型，修前零重试。
            # 注意 E2 的 remote_task_id 语义在此仍然成立：提交成功后重试只重连轮询，
            # 不会因为多了这条重试路径而重复扣费。
            except (RateLimitError, TimeoutError, ConnectionError, ProviderUnavailableError, httpx.TimeoutException, httpx.TransportError) as exc:
                if attempt == self._max_retries:
                    status = "unavailable" if isinstance(exc, ProviderUnavailableError) else "error"
                    _record_provider_call("ai_video", "generate_video", time.monotonic() - start, status)
                    if _counts_toward_breaker(exc):
                        self._breaker.record_failure()
                    raise
                delay = _backoff(attempt)
                logger.warning("AI video provider retry %d/%d in %.1fs", attempt + 1, self._max_retries, delay)
                await asyncio.sleep(delay)  # E10: 锁外退避
            except Exception as exc:
                # Task 14: 非「可重试」类异常也必须计失败。修前只对
                # RateLimitError/TimeoutError/ConnectionError/ProviderUnavailableError
                # 调 record_failure —— ValueError / RuntimeError / 裸 5xx 全部逃逸不计，
                # 于是**慢性故障的供应商永不跳闸**，每个任务都去撞一遍。
                # 这里不重试（语义不变：这类错误重试不会变好），只补计数后原样抛出。
                _record_provider_call("ai_video", "generate_video", time.monotonic() - start, "error")
                if _counts_toward_breaker(exc):
                    self._breaker.record_failure()
                raise
        raise ProviderError("unreachable")
