import asyncio
import contextlib
import logging
import traceback

import requests
import tenacity

# ── VENDOR PATCH (TrainerAI, Plan C 2026-10-19)：本地 deadline 精确分类 ──

class LocalStoryboardDeadlineError(TimeoutError):
    """本地分镜 deadline 到期（区别于 provider 自身 TimeoutError）。

    ``pipeline_retryable``：``task_retryable=True/False`` 显式控制；None 保持旧
    行为（单批可重放、拆批不重放）。共享任务框架只读 getattr 标记，不 import
    本类型。
    """

    error_code = "vimax_storyboard_deadline"

    def __init__(self, timeout: float, *, batches_reusable: bool = False, task_retryable: bool | None = None):
        # batches_reusable 仅用于文案/缓存语义；task_retryable 才是任务级重放
        # 契约（None = 旧行为，供 idea2video 等共享 artist 场景显式兼容）。
        self.batches_reusable = batches_reusable
        self.task_retryable = task_retryable
        suffix = "completed batches are cached for reuse" if batches_reusable else "no reusable batch cache (single-batch call)"
        super().__init__(f"vimax_storyboard_deadline: local storyboard deadline of {timeout}s exceeded ({suffix})")

    @property
    def pipeline_retryable(self) -> bool:
        # VENDOR PATCH (TrainerAI, 2026-10-20 P40)：script2video 传 False（2×420s
        # 不再任务级重放）；idea2video 传 True（保持 3×300s + 原任务级重试）；
        # None 保持历史单批 True / 多批 False 语义。
        if self.task_retryable is not None:
            return self.task_retryable
        return not self.batches_reusable


async def await_with_local_deadline(awaitable, timeout: float, *, batches_reusable: bool = False, task_retryable: bool | None = None):
    """await + 本地 deadline，用 done/pending 语义而非异常类型猜测。

    - pending 到期：cancel + drain 底层后抛 :class:`LocalStoryboardDeadlineError`；
    - awaitable 自身异常（含 provider TimeoutError）：原样传播，不改写；
    - 外层取消：CancelledError 原样传播（底层任务清理）。
    """
    task = asyncio.ensure_future(awaitable)
    timer = asyncio.ensure_future(asyncio.sleep(timeout))
    try:
        done, _pending = await asyncio.wait({task, timer}, return_when=asyncio.FIRST_COMPLETED)
    except BaseException:
        timer.cancel()
        if not task.done():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        raise
    timer.cancel()
    if task in done:
        return task.result()
    if not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            # 评审修复（2026-10-19）：drain 窗口内送达的**外层取消**不得被吞——
            # 否则用户取消变 LocalDeadline + tenacity 僵尸重试，最终覆写 cancelled
            # 态。用当前任务（外层）的取消计数区分：>0 = 外层取消，原样传播；
            # ==0 = 内任务自身取消，正常 drain 继续。py3.11+ Task.cancelling()。
            if asyncio.current_task().cancelling() > 0:
                raise
        except BaseException:
            # 内任务清理期的真实异常只进上方 except 的日志链（drain 语义）——
            # 本地 deadline 仍是本分支的权威结论。
            logging.debug("local-deadline drained task raised during cleanup", exc_info=True)
    raise LocalStoryboardDeadlineError(timeout, batches_reusable=batches_reusable, task_retryable=task_retryable) from None

def after_func(retry_state: tenacity.RetryCallState) -> None:
    if retry_state.outcome.failed:
        exc = retry_state.outcome.exception()
        fn_name = getattr(retry_state.fn, "__name__", "async call")
        logging.warning(f"Retrying {fn_name} due to {repr(exc)} (Attempt {retry_state.attempt_number})")
        logging.debug(traceback.format_exception(type(exc), exc, exc.__traceback__))


def is_retryable_download_error(exc: BaseException) -> bool:
    """Network errors and 5xx responses are retryable; other HTTP errors (expired
    or invalid URLs, auth failures) will never succeed and must fail fast."""
    if isinstance(exc, requests.HTTPError):
        response = exc.response
        return response is None or response.status_code >= 500
    return isinstance(exc, requests.RequestException)


download_retry = tenacity.retry(
    stop=tenacity.stop_after_attempt(3),
    wait=tenacity.wait_exponential(multiplier=1, max=10),
    retry=tenacity.retry_if_exception(is_retryable_download_error),
    after=after_func,
    reraise=True,
)
