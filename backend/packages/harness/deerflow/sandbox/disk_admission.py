"""Disk admission gate (DEF-04 C2, 2026-09-23 批 1).

Render-heavy long tasks fill the host disk (frame caches, downloaded
visuals, ffmpeg intermediates); when free space runs out mid-run the
failure surfaces as opaque ffmpeg/worker errors. This gate makes the
host's free space a first-class admission check at two points:

* run start — enforced only when the sandbox runs in docker/aio mode
  (render load lives there); local-sandbox mode downgrades to a WARN
  (local dev has no render load and manages its own disk);
* sandbox creation (``AioSandboxProvider._create_sandbox``) — naturally
  container-mode-only.

Cost on the happy path: one ``shutil.disk_usage`` call (microseconds)
against the data-dir filesystem — zero allocation, zero state.

Thresholds live in ``config.yaml`` (``disk_min_free_bytes_block`` /
``disk_warn_free_ratio``); production defaults are calibrated for the
render workload (5 GiB block / 15% warn) and the small-disk test server
overrides ``disk_min_free_bytes_block`` to 2 GiB in its own config.yaml.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from langgraph.errors import GraphBubbleUp

logger = logging.getLogger(__name__)

_GIB = 1024**3


class DiskAdmissionBlocked(GraphBubbleUp):
    """Free space below the block threshold — the caller rejects with this message.

    ``GraphBubbleUp`` (2026-09-23 review #3): the default lazy sandbox
    acquires inside tool execution, where ``ToolErrorHandlingMiddleware``'s
    ``except Exception`` used to convert this into an ordinary error
    ToolMessage — no marker, no breaker count, and the agent retried ``bash``
    (re-acquiring, re-failing) until recursion_limit, the exact DEF-04 loop
    this gate exists to break. As a ``GraphBubbleUp`` it rides the same
    passthrough as ``SandboxCircuitBreakerError`` out to the run worker,
    whose generic ``except Exception`` (``GraphBubbleUp`` subclasses
    ``Exception``) stamps the run error and publishes the SSE error event;
    the run-start checkpoint is unaffected (same behavior as before).

    ``str()`` is user-facing Chinese; the run/sandbox creation surfaces it
    verbatim so the operator sees an actionable reason, not an opaque EIO.
    """


def _fmt_gib(num_bytes: float) -> str:
    return f"{num_bytes / _GIB:.1f} GiB"


def check_disk_admission(
    path: Path | str,
    *,
    block_bytes: int,
    warn_ratio: float,
    label: str = "task",
) -> None:
    """One ``shutil.disk_usage`` check: block under ``block_bytes`` free, warn under ``warn_ratio``.

    Never raises anything but :class:`DiskAdmissionBlocked`; a failed probe
    (unresolvable path, permission error) degrades to a debug log — disk
    admission is a guardrail, not a new failure surface.
    """
    try:
        usage = shutil.disk_usage(str(path))
    except Exception:
        logger.debug("Disk admission probe failed for %s (non-fatal)", path, exc_info=True)
        return
    free, total = usage.free, usage.total
    if block_bytes and free < block_bytes:
        raise DiskAdmissionBlocked(f"服务器磁盘可用空间不足（{_fmt_gib(free)} < 准入门槛 {_fmt_gib(block_bytes)}），{label}已拒绝启动：请清理磁盘空间后重试；已开始的任务不受影响。")
    if total and warn_ratio and (free / total) < warn_ratio:
        logger.warning(
            "Disk space low for %s: free=%s / total=%s (%.1f%%) is below the warn ratio %.0f%% (advisory only)",
            label,
            _fmt_gib(free),
            _fmt_gib(total),
            100.0 * free / total,
            100.0 * warn_ratio,
        )


def sandbox_mode_is_containerized(sandbox_use: str | None) -> bool:
    """True when the configured sandbox provider runs containers (docker/aio).

    The run-start admission check is enforced only in containerized mode
    (Q5, 2026-09-23); local sandbox mode downgrades to WARN-only.
    """
    if not sandbox_use:
        return False
    text = sandbox_use.lower()
    return "aio" in text or "docker" in text
