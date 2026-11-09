import threading
from unittest.mock import MagicMock, patch

from deerflow.agents.memory.queue import ConversationContext, MemoryUpdateQueue
from deerflow.config.memory_config import MemoryConfig


def _memory_config(**overrides: object) -> MemoryConfig:
    config = MemoryConfig()
    for key, value in overrides.items():
        setattr(config, key, value)
    return config


def test_queue_add_preserves_existing_correction_flag_for_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["first"], correction_detected=True)
        queue.add(thread_id="thread-1", messages=["second"], correction_detected=False)

    assert len(queue._queue) == 1
    assert queue._queue[0].messages == ["second"]
    assert queue._queue[0].correction_detected is True


def test_process_queue_forwards_correction_flag_to_updater() -> None:
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
            correction_detected=True,
        )
    ]
    mock_updater = MagicMock()
    mock_updater.update_memory.return_value = True

    with patch("deerflow.agents.memory.updater.MemoryUpdater", return_value=mock_updater):
        queue._process_queue()

    mock_updater.update_memory.assert_called_once_with(
        messages=["conversation"],
        thread_id="thread-1",
        agent_name="lead_agent",
        correction_detected=True,
        reinforcement_detected=False,
        user_id=None,
    )


def test_queue_add_preserves_existing_reinforcement_flag_for_same_thread() -> None:
    queue = MemoryUpdateQueue()

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        patch.object(queue, "_reset_timer"),
    ):
        queue.add(thread_id="thread-1", messages=["first"], reinforcement_detected=True)
        queue.add(thread_id="thread-1", messages=["second"], reinforcement_detected=False)

    assert len(queue._queue) == 1
    assert queue._queue[0].messages == ["second"]
    assert queue._queue[0].reinforcement_detected is True


def test_process_queue_forwards_reinforcement_flag_to_updater() -> None:
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
            reinforcement_detected=True,
        )
    ]
    mock_updater = MagicMock()
    mock_updater.update_memory.return_value = True

    with patch("deerflow.agents.memory.updater.MemoryUpdater", return_value=mock_updater):
        queue._process_queue()

    mock_updater.update_memory.assert_called_once_with(
        messages=["conversation"],
        thread_id="thread-1",
        agent_name="lead_agent",
        correction_detected=False,
        reinforcement_detected=True,
        user_id=None,
    )


# ── 复审二轮（2026-09-13）：Timer 补丁隔离 ─────────────────────────────────


from collections.abc import Iterator  # noqa: E402
from contextlib import contextmanager  # noqa: E402


@contextmanager
def _timer_recorder() -> Iterator[tuple[list[tuple[float, object]], list[MagicMock]]]:
    """threading.Timer 补丁是全局的（queue.threading 即 stdlib 模块）——共享
    return_value 的 MagicMock 会把其他仍存活线程的 Timer 构造也算进来，全量套件
    内偶发 called-twice 假红。改为每次构造返回独立 mock，按「回调身份是本队列的
    _process_queue」过滤出本队列的调度再断言（断言强度不降：仍是恰好一次、
    delay、daemon、start 四条）。"""
    calls: list[tuple[float, object]] = []
    timers: list[MagicMock] = []

    def _factory(delay: float, callback: object, *args: object, **kwargs: object) -> MagicMock:
        calls.append((delay, callback))
        timer = MagicMock()
        timers.append(timer)
        return timer

    with patch("deerflow.agents.memory.queue.threading.Timer", side_effect=_factory):
        yield calls, timers


def _own_timer(calls: list[tuple[float, object]], timers: list[MagicMock], queue: MemoryUpdateQueue) -> tuple[float, MagicMock]:
    # bound method 每次属性访问都是新对象——按等值（__self__+__func__）而非身份过滤
    own = [(calls[i][0], timers[i]) for i, (_, cb) in enumerate(calls) if cb == queue._process_queue]
    assert len(own) == 1, f"本队列应恰好调度一次，实际 {[d for d, _ in own]}（全部 Timer 调用：{calls!r}）"
    return own[0]


def test_flush_nowait_cancels_existing_timer_and_starts_immediate_timer() -> None:
    queue = MemoryUpdateQueue()
    existing_timer = MagicMock()
    queue._timer = existing_timer

    with _timer_recorder() as (calls, timers):
        queue.flush_nowait()

    existing_timer.cancel.assert_called_once_with()
    delay, created_timer = _own_timer(calls, timers, queue)
    assert delay == 0
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()
    assert queue._timer is created_timer


def test_add_nowait_cancels_existing_timer_and_starts_immediate_timer() -> None:
    queue = MemoryUpdateQueue()
    existing_timer = MagicMock()
    queue._timer = existing_timer

    with (
        patch("deerflow.agents.memory.queue.get_memory_config", return_value=_memory_config(enabled=True)),
        _timer_recorder() as (calls, timers),
    ):
        queue.add_nowait(thread_id="thread-1", messages=["conversation"], agent_name="lead-agent")

    existing_timer.cancel.assert_called_once_with()
    delay, created_timer = _own_timer(calls, timers, queue)
    assert delay == 0
    assert queue.pending_count == 1
    assert queue._queue[0].agent_name == "lead-agent"
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()


def test_flush_nowait_is_non_blocking() -> None:
    queue = MemoryUpdateQueue()
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()

    def _slow_process_queue() -> None:
        started.set()
        release.wait(timeout=5)
        finished.set()

    queue._process_queue = _slow_process_queue

    queue.flush_nowait()

    # Pure event synchronization (no wall-clock budgets — flaky under loaded
    # CI): the timer callback has begun (started) but is still parked on
    # `release` (finished unset), which proves flush_nowait returned without
    # waiting for _process_queue to complete.
    assert started.wait(timeout=5) is True
    assert not finished.is_set()
    release.set()
    assert finished.wait(timeout=5) is True


# ── B3（2026-10-07 反馈核实批）：busy 时 defer 单次补跑，替代 0 延时 Timer 紧转 ──


def test_process_queue_defers_single_rerun_when_already_processing() -> None:
    """worker 忙时不再反复重排 0 延时 Timer（紧转）——置 defer 旗标即返回，
    由活跃 worker 的 finally 单次补跑（上游 defer 形态）。"""
    queue = MemoryUpdateQueue()
    queue._processing = True

    with _timer_recorder() as (calls, timers):
        queue._process_queue()

    own = [delay for (delay, cb) in calls if cb == queue._process_queue]
    assert own == [], f"忙时不应再调度 Timer，实际调度了 {own!r}"
    assert queue._reprocess_pending is True
    assert queue._processing is True


def test_finishing_worker_reschedules_once_after_deferred_arrival() -> None:
    """活跃 worker 结束时（finally）：defer 旗标已置且队列有剩余工作 → 恰好
    补排一次 0 延时 Timer，旗标清零，_processing 复位。"""
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
        )
    ]
    queue._reprocess_pending = True  # 模拟处理期间另一调用者已置 defer
    mock_updater = MagicMock()
    mock_updater.update_memory.return_value = True

    def _refill_mid_processing(**kwargs):
        # 模拟活跃 worker 处理期间新上下文入队（defer 旗标对应的剩余工作）
        queue._queue.append(
            ConversationContext(
                thread_id="thread-2",
                messages=["late"],
                agent_name="lead_agent",
            )
        )
        return True

    mock_updater.update_memory.side_effect = _refill_mid_processing

    with (
        patch("deerflow.agents.memory.updater.MemoryUpdater", return_value=mock_updater),
        _timer_recorder() as (calls, timers),
    ):
        queue._process_queue()

    delay, created_timer = _own_timer(calls, timers, queue)
    assert delay == 0
    assert created_timer.daemon is True
    created_timer.start.assert_called_once_with()
    assert queue._reprocess_pending is False
    assert queue._processing is False


def test_finishing_worker_clears_stale_pending_without_work() -> None:
    """defer 旗标已置但队列已空 → 不补排，只清旗标（无谓的空转调度）。"""
    queue = MemoryUpdateQueue()
    queue._queue = [
        ConversationContext(
            thread_id="thread-1",
            messages=["conversation"],
            agent_name="lead_agent",
        )
    ]
    queue._reprocess_pending = True
    mock_updater = MagicMock()
    mock_updater.update_memory.return_value = True

    with (
        patch("deerflow.agents.memory.updater.MemoryUpdater", return_value=mock_updater),
        _timer_recorder() as (calls, timers),
    ):
        queue._process_queue()

    own = [delay for (delay, cb) in calls if cb == queue._process_queue]
    assert own == []
    assert queue._reprocess_pending is False
    assert queue._processing is False
