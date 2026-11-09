"""Production Worker — polls unified_tasks for composing tasks, runs ffmpeg pipeline."""

import __future__

import json
import logging
import subprocess
import os
import re
import shutil
import signal
import sqlite3
import sys
import threading
import time
import traceback
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

logger = logging.getLogger(__name__)

WORKER_ID = os.environ.get("HOSTNAME", "worker-0")
DB_PATH = os.environ.get("DB_PATH", "/app/data/deerflow.db")
STORAGE_DIR = os.environ.get("STORAGE_DIR", "/app/backend/.deer-flow/voice-twins")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "2"))
LEASE_DURATION = int(os.environ.get("LEASE_DURATION", "1800"))  # 30 min
# Task 25：租约过期重排队的次数上限。unified_tasks 无 max_retries 列，故用常量。
MAX_LEASE_RETRIES = int(os.environ.get("MAX_LEASE_RETRIES", "3"))
MAX_CONCURRENT_TASKS = int(os.environ.get("MAX_CONCURRENT_TASKS", "2"))
# 健康/指标 HTTP 端口。本机双池并存（ffmpeg 池 8040 + remotion 池 8041）时需
# 覆盖；容器内单池保持默认 8040（compose healthcheck 依赖）。
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8040"))

# ── Remotion 双池（2026-09-08 remotion-course 计划 T3）─────────────────────
# WORKER_POOL: "ffmpeg"（默认，存量行为不变）| "remotion"（独立渲染池）。
# 两池共用 unified_tasks 表与 CAS 认领，认领谓词按 task_type 隔离（见
# _pool_task_type_predicate）——旧 worker 无差别抢锁、未知类型直接标 failed
# 是 M1 最大风险点，发布顺序红线：先部署两池 worker（带谓词 + registry 注册），
# 后放量 gateway 提交 remotion_course。
WORKER_POOL = os.environ.get("WORKER_POOL", "ffmpeg")
# remotion 池的 still 预览槽数（T7；测试机/本机降级用，与测试计划 §2 命名对齐）
REMOTION_STILL_SLOTS = int(os.environ.get("REMOTION_STILL_SLOTS", "1"))
# remotion 池认领的任务类型（remotion_still = T7 静帧预览任务）
REMOTION_TASK_TYPES = ("remotion_course", "remotion_still")


def _ffmpeg_pool_whitelist() -> tuple[str, ...]:
    """ffmpeg 池认领白名单 = registry PIPELINES 全部键的 task_type 集，扣除
    remotion 池类型（跨 domain：含 voice_production/ppt_to_video——按 domain=
    'video_gen' 过滤会把 PPT 任务挤出白名单，永久无人认领）。

    代码生成、禁止手写——手写白名单曾漏 (video_gen, mixed) 即为此类事故实证。
    守卫测试 test_pool_claim_isolation.py 断言与 PIPELINES 全键一致。
    """
    from pipelines.registry import PIPELINES

    return tuple(sorted({tt for (_domain, tt) in PIPELINES if tt not in REMOTION_TASK_TYPES}))


def _pool_task_type_predicate() -> tuple[str, list[str]]:
    """认领 SQL 的 task_type 谓词（peek 与 CAS UPDATE 两处共用同一份，防漂移）。

    - remotion 池：task_type IN ('remotion_course', 'remotion_still')
    - ffmpeg 池：task_type IN (<registry 派生白名单>)
    """
    if WORKER_POOL == "remotion":
        return f"task_type IN ({','.join('?' * len(REMOTION_TASK_TYPES))})", list(REMOTION_TASK_TYPES)
    whitelist = _ffmpeg_pool_whitelist()
    placeholders = ",".join("?" * len(whitelist))
    return f"task_type IN ({placeholders})", list(whitelist)


def _render_slot_task_types() -> list[str] | None:
    """T-A1（2026-09-09 R1）：渲染槽认领类型（主循环渲染分支专用）。

    remotion 池收窄为 ('remotion_course',)——修前渲染槽用池默认谓词（含
    remotion_still）会领走 still 任务，而 _run_task 按 task_type 推断释放
    still 槽 → 渲染许可永久泄漏、单渲染槽下 remotion_course 饿死。still 任务
    只从 still 槽进（_claim_one_task 的 still 分支）。ffmpeg 池无 still 概念，
    返回 None = 保持池默认谓词，存量行为不变。
    """
    if WORKER_POOL == "remotion":
        return ["remotion_course"]
    return None


def _lease_for_payload(payload_str: str | None) -> int:
    """claim/renew 共用的租约计算，保证两处永不漂移。

    remotion 分支（T3）：读**视频总帧数**（props.scenes 的 durationInFrames 之和
    + intro/outro），不是 payload.frames 数组长度（那是分镜帧数，个位到几十）——
    照抄会把长片租约算小一个数量级。初值 1800 + 总帧数×0.5s，上限 7200s。

    >30 帧按帧数动态延长（长片 xfade 合成慢），否则用静态基础租约；
    payload 解析失败退化为 0 帧 → 基础租约。
    """
    try:
        payload = json.loads(payload_str or "{}")
        if payload.get("task_kind") == "still":
            # T7：静帧预览是秒级任务——固定短租约 600s（租约过期重排语义照常兜底）
            return min(600, LEASE_DURATION)
        if payload.get("pipeline_version") == "remotion_course":
            return remotion_lease(_remotion_total_frames(payload))
        n_frames = len(payload.get("frames", []))
    except (json.JSONDecodeError, TypeError):
        n_frames = 0
    return effective_lease(n_frames) if n_frames > 30 else LEASE_DURATION


def _remotion_total_frames(payload: dict) -> int:
    """remotion 视频总帧数 = Σ scenes.durationInFrames + intro/outro 帧数。

    props 由 gateway 构建（T4）随 payload 下发（payload["remotion_props"]）；
    缺失/畸形一律 0 → remotion_lease 落到 1800s 基础租约（心跳续租兜底不变）。
    """
    props = payload.get("remotion_props") or {}
    total = 0
    for scene in props.get("scenes") or []:
        try:
            total += max(1, round(float(scene.get("durationInFrames", 0))))
        except (TypeError, ValueError):
            continue
    brand = props.get("brand") or {}
    for cap in ("intro", "outro"):
        try:
            total += max(0, round(float((brand.get(cap) or {}).get("durationInFrames", 0))))
        except (TypeError, ValueError):
            pass
    return total


def remotion_lease(total_frames: int) -> int:
    """Remotion 渲染租约：初值 1800 + 总帧数×0.5s，上限 7200s（T3 拍板公式）。

    Chrome 逐帧渲染远慢于 ffmpeg 拼接；60s 成片（1800 帧）→ 2700s。
    """
    return min(1800 + int(total_frames * 0.5), 7200)


def effective_lease(n_frames: int) -> int:
    """Calculate dynamic lease duration based on frame count.

    Long videos (200+ frames) need more time for ffmpeg xfade concat.
    Cap at 2 hours to prevent zombie tasks.
    """
    return min(1800 + n_frames * 10, 7200)

# ── In-process metrics (Prometheus-compatible) ──────────────────────────

DEFAULT_BUCKETS = (5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)


def _format_labels(labels: dict[str, str]) -> str:
    """Format labels dict into Prometheus label string with proper quoting."""
    if not labels:
        return ""
    pairs = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + pairs + "}"


_metrics_lock = threading.Lock()
_metrics_counters: dict[str, float] = {}
_metrics_histograms: dict[str, dict] = {}


def _validate_deliverable(output_path: Path) -> None:
    """交付闸（H5 / Task 11）：产物必须真的是个能看的视频，否则抛错走 failed。

    修前成功路径只有 `prior["file_size"] = output_path.stat().st_size` —— **记录**
    大小但**从不校验**。0 字节、截断、无视频轨的产物全部标 ready/progress=100，
    与完美渲染不可区分；用户点开是个打不开的文件。

    三道检查：存在 → 非 0 字节 → ffprobe 探到视频流且 duration > 0。
    ffprobe 不可用（精简镜像）时只做前两道，不因缺工具把好任务打死。
    """
    if not output_path.exists():
        raise RuntimeError(f"交付物不存在（missing output）: {output_path}")
    size = output_path.stat().st_size
    if size == 0:
        raise RuntimeError(f"交付物 0 字节（empty output）: {output_path}")

    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_type", "-show_entries", "format=duration",
             "-of", "default=nw=1", str(output_path)],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        logger.warning("ffprobe unavailable; deliverable check limited to size for %s", output_path)
        return

    if proc.returncode != 0 or "codec_type=video" not in proc.stdout:
        raise RuntimeError(
            f"交付物无视频轨（no video stream）: {output_path} "
            f"size={size}B ffprobe={proc.stderr.strip()[-200:]}"
        )
    duration: float | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("duration="):
            try:
                duration = float(line.split("=", 1)[1])
            except ValueError:
                # P2-16：部分容器/流 ffprobe 报 duration=N/A。None=「未知」，
                # 与 ffprobe 不可用同哲学——不因缺信息误杀好任务；明确探到
                # 0（截断产物）才拒。
                duration = None
    if duration is not None and duration <= 0:
        raise RuntimeError(f"交付物 duration 非正（invalid duration={duration}）: {output_path}")
    if duration is None:
        logger.warning("ffprobe 未给出有效 duration，跳过时长校验: %s", output_path)


def _metrics_inc(name: str, value: float = 1.0, labels: str = "") -> None:
    key = f"{name}{labels}"
    with _metrics_lock:
        _metrics_counters[key] = _metrics_counters.get(key, 0.0) + value


def _metrics_observe(name: str, value: float, labels: str = "") -> None:
    key = f"{name}{labels}"
    with _metrics_lock:
        if key not in _metrics_histograms:
            _metrics_histograms[key] = {"count": 0, "sum": 0.0, "buckets": {b: 0 for b in DEFAULT_BUCKETS}}
        d = _metrics_histograms[key]
        d["count"] += 1
        d["sum"] += value
        for b in DEFAULT_BUCKETS:
            if value <= b:
                d["buckets"][b] += 1

# Domain → storage subdirectory mapping
_DOMAIN_STORAGE = {
    "voice_production": STORAGE_DIR,
    "video_gen": os.environ.get("VIDEO_GEN_STORAGE_DIR", os.path.join(os.path.dirname(STORAGE_DIR), "video-gen")),
}

# Path traversal validation pattern
_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_id(value: str, name: str) -> str:
    if not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid {name}: {value!r}")
    return value


def db_connect() -> sqlite3.Connection:
    """Connect to shared SQLite DB with WAL mode and busy timeout."""
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _lease_remaining_seconds(task_id: str) -> float | None:
    """渲染期剩余租约秒数（lease_expires_at − now）；行缺失/无租约返 None。

    供 compose 管线核算阻塞 subprocess 的 timeout 用（租约不变量：timeout ≤
    剩余租约−300）。值经 compose 调用链传入 pipelines——pipelines 禁读 env/DB。

    评审 Minor #3：本查询是渲染前的**辅助**读取，任何瞬时 DB 错误（locked/坏值）
    都回落到 None（管线退回旧行为 300s 超时），不得让任务直接 failed。
    """
    try:
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT lease_expires_at FROM unified_tasks WHERE id = ? AND claimed_by = ?",
                (task_id, WORKER_ID),
            ).fetchone()
            if not row or not row["lease_expires_at"]:
                return None
            expires = datetime.fromisoformat(row["lease_expires_at"])
            return max((expires - datetime.now(UTC)).total_seconds(), 0.0)
        finally:
            conn.close()
    except (sqlite3.Error, ValueError):
        logger.warning("lease_remaining lookup failed for %s — falling back to None", task_id)
        return None


def db_claim_task(task_types: list[str] | None = None) -> dict | None:
    """Atomically claim one composing task with claimed_by=NULL.

    Uses dynamic lease based on frame count from payload.

    T3 双池认领隔离：peek 与 CAS UPDATE 都带同一份 task_type 谓词
    （task_types=None 时取本池默认谓词 _pool_task_type_predicate()）——
    只在 peek 处过滤而 UPDATE 不滤会有竞态窗口（另一池在两步之间抢走）。
    """
    predicate, pool_types = _pool_task_type_predicate()
    allowed = tuple(task_types) if task_types is not None else tuple(pool_types)
    placeholders = ",".join("?" * len(allowed)) or "''"
    now = datetime.now(UTC)
    conn = db_connect()
    try:
        # First, peek at the next task to calculate dynamic lease
        peek = conn.execute(
            f"""
            SELECT id, payload FROM unified_tasks
            WHERE status = 'composing' AND claimed_by IS NULL
              AND task_type IN ({placeholders})
            ORDER BY priority DESC, created_at ASC
            LIMIT 1
            """,
            allowed,
        ).fetchone()
        if not peek:
            return None

        # Calculate dynamic lease from frame count (与 renew_lease 同公式)
        lease = _lease_for_payload(peek["payload"])
        expires = now + timedelta(seconds=lease)

        row = conn.execute(
            f"""
            UPDATE unified_tasks
            SET claimed_by = ?, claimed_at = ?, lease_expires_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'composing' AND claimed_by IS NULL
              AND task_type IN ({placeholders})
            RETURNING *
            """,
            (WORKER_ID, now.isoformat(), expires.isoformat(), now.isoformat(), peek["id"], *allowed),
        ).fetchone()
        conn.commit()
        if row:
            task = dict(row)
            _validate_id(task["id"], "task_id")
            _validate_id(task["user_id"], "user_id")
            return task
        return None
    finally:
        conn.close()


def db_update(task_id: str, **kwargs) -> None:
    conn = db_connect()
    try:
        now = datetime.now(UTC).isoformat()
        sets = ", ".join(f"{k} = ?" for k in kwargs)
        conn.execute(
            f"UPDATE unified_tasks SET {sets}, updated_at = ? WHERE id = ?",
            list(kwargs.values()) + [now, task_id],
        )
        conn.commit()
    finally:
        conn.close()


def db_update_if_owned(task_id: str, claimed_by: str, **kwargs) -> int:
    """带 claimed_by 守卫的写入；返回受影响行数（0 = 已被他人接管）。

    P1-1: 对照 renew_lease--租约过期 + recover_stale_tasks 重排 + 他人 claim 后，
    本 worker 的迟到写入会抢走对方 claim、抹掉租约、覆盖 result（实测复现）。

    claimed_by 在 kwargs 中时作为列更新值（如 success_kwargs 里的 claimed_by=None
    表示释放租约），而非守卫参数--守卫始终用第二个位置参数。
    """
    new_claimed_by = kwargs.pop("claimed_by", None)
    conn = db_connect()
    try:
        now = datetime.now(UTC).isoformat()
        sets_parts = [f"{k} = ?" for k in kwargs]
        sets_parts.append("claimed_by = ?")
        sets_parts.append("updated_at = ?")
        values = list(kwargs.values()) + [new_claimed_by, now, task_id, claimed_by]
        cur = conn.execute(
            f"UPDATE unified_tasks SET {', '.join(sets_parts)} WHERE id = ? AND claimed_by = ?",
            values,
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


class TaskCancelled(Exception):
    """任务在渲染途中被用户取消/删除（Task 22）。

    与普通异常区分：不写 failed、不写 error_message —— 用户主动取消不是故障，
    界面上出现红色报错反而是噪音。
    """


class _SuccessWritebackFailed(Exception):
    """渲染成功但状态回写失败：产物有效，禁止清理中间件。"""


class _OwnershipLost(Exception):
    """成功回写时发现任务已被他人接管：丢弃结果，不碰任务行。

    2026-08-08 P0-2：勿复用 TaskCancelled——其外层分支会 db_update(claimed_by=None)，
    接管场景下会清掉新持有者的 claim（把别人的任务释放成孤儿）。
    也勿让此异常落进 except Exception：修前 TaskCancelled 被重试循环的
    except Exception 吞掉，空转 3 次后抛 _SuccessWritebackFailed → 误标 failed。
    """


# T12：QCRepairNeeded 定义在 pipelines/_qc_exception.py（共享模块）——worker.py 以
# `python worker.py` 启动时 __main__ 与 import worker 是两个实例，类定义留本文件
# 会让 compose_remotion 的 from worker import 接不到同一个类（E2E 实锤修复）。
from pipelines._qc_exception import QCRepairNeeded  # noqa: E402 — 兼容既有引用


_CANCELLED_STATUSES = frozenset({"cancelled", "canceled"})


def is_task_cancelled(task_id: str) -> bool:
    """回查任务是否已被取消或删除（Task 22）。

    两种都算取消：
    - status 被置为 cancelled（显式 POST /tasks/{id}/cancel）
    - 行已不存在（DELETE /tasks/{id}）—— 修前 worker 对此毫无察觉，
      ffmpeg / 供应商调用照跑到底，配额照烧，最后还可能把 status 写回 ready

    DB 读失败时保守返回 False：宁可多跑一会儿，也不因一次 sqlite 抖动
    误杀正在正常渲染的任务。
    """
    try:
        conn = db_connect()
        try:
            row = conn.execute(
                "SELECT status, claimed_by FROM unified_tasks WHERE id = ?", (task_id,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        logger.debug("cancel check failed for %s; assuming not cancelled", task_id, exc_info=True)
        return False
    if row is None:
        return True  # 行没了 = 已删除
    if (row["status"] or "").lower() in _CANCELLED_STATUSES:
        return True
    # 审查发现 4：任务已被**别的** worker 认领 → 本地这份渲染该停，否则双处理。
    # claimed_by 为 NULL（刚被回收、尚无人认领）不算——留给 claim 流程定归属，
    # 避免自己刚被回收就误判自杀。
    owner = row["claimed_by"]
    return bool(owner) and owner != WORKER_ID


def clamp_progress(current: int | None, incoming: int) -> int:
    """高水位钳制：进度只能前进，不得倒退。

    2026-07-29：Gateway 交棒写 progress=95，Worker 首个检查点写 progress_cb(5)
    导致 95→5 倒退。Gateway 侧的 ProgressUpdater 有同类钳制（service.py:491-496），
    但只在 Gateway 进程内。此处在 Worker 侧补齐，使行为对称。
    100 永远可写（终态不受钳制）。
    """
    if current is None:
        return incoming
    if incoming == 100:
        return 100
    return max(current, incoming)


def _cleanup_task_dir(task_dir: Path) -> None:
    """Remove intermediate files/dirs from a failed or stale task."""
    for subdir in ("audio", "slides", "segments", "_tmp_pdf", "source", "tmp"):
        d = task_dir / subdir
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    for f in ("subtitles.json", "filelist.txt"):
        p = task_dir / f
        if p.exists():
            p.unlink()


def _write_failure_context(
    task_dir: Path,
    exc_info,
    *,
    task_id: str,
    domain: str,
    task_type: str,
    elapsed: float,
) -> None:
    """Best-effort diagnostic snapshot for a failed task.

    Writes ``_failure_context.txt`` into the task dir with the error, the
    pre-cleanup file list, and a traceback tail. Intentionally additive: it
    never changes what ``_cleanup_task_dir`` removes (and ``_failure_context.txt``
    is not in that list, so it survives cleanup). Must never raise — a broken
    snapshot cannot be allowed to derail the failure path.
    """
    try:
        exc_type, exc_value, _ = exc_info
        present = (
            sorted(p.name for p in task_dir.iterdir() if p.is_file())
            if task_dir.exists()
            else []
        )
        tb_tail = "".join(traceback.format_exception(*exc_info))[-4000:]
        file_lines = [f"  {n}" for n in present] if present else ["  (none)"]
        lines = [
            f"task_id: {task_id}",
            f"domain: {domain}",
            f"task_type: {task_type}",
            f"elapsed_s: {elapsed:.1f}",
            f"error: {(exc_type.__name__ if exc_type else 'Unknown')}: {exc_value}",
            "",
            "task_dir files (pre-cleanup):",
            *file_lines,
            "",
            "traceback (tail):",
            tb_tail,
        ]
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "_failure_context.txt").write_text("\n".join(lines), encoding="utf-8")
    except Exception:
        logger.debug("Failed to write failure context", exc_info=True)


def process_task(task: dict) -> None:
    from pipelines.registry import UnsupportedTaskType, resolve_pipeline

    task_id = task["id"]
    user_id = task["user_id"]
    domain = task["domain"]
    task_type = task["task_type"]
    payload = json.loads(task.get("payload") or "{}")

    task_dir = Path(_DOMAIN_STORAGE.get(domain, STORAGE_DIR)) / user_id / "tasks" / task_id
    if not task_dir.exists():
        raise RuntimeError(f"Task directory not found: {task_dir}")

    options = payload.get("options", {})
    if isinstance(options, str):
        options = json.loads(options)

    progress_cb = _make_progress_cb(task_id)

    try:
        fn = resolve_pipeline(domain, task_type, payload)
    except UnsupportedTaskType as e:
        db_update(task_id, status="failed", error_message=str(e)[:500],
                  claimed_by=None, lease_expires_at=None)
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="unsupported"}}')
        return

    start = time.monotonic()
    labels_str = f'{{domain="{domain}",task_type="{task_type}",worker_id="{WORKER_ID}"}}'
    try:
        output_path = fn(task_dir, payload, options, progress_cb, lease_remaining=_lease_remaining_seconds(task_id))
        # B4: read-merge-write result (was full overwrite — clobbered dirty_frames
        # and any other fields placed in result by the Gateway).
        conn = db_connect()
        try:
            row = conn.execute("SELECT result FROM unified_tasks WHERE id = ?", (task_id,)).fetchone()
            prior = json.loads(row["result"] if row and row["result"] else "{}")
        finally:
            conn.close()
        # H5 (Task 11): 先校验交付物再标 ready —— 0 字节 / 无视频轨的产物不得冒充成功
        # T7：remotion_still 产物是 PNG（无视频轨）——still 模式只查存在 + 非零字节
        if task_type == "remotion_still":
            if not output_path.exists() or output_path.stat().st_size == 0:
                raise RuntimeError(f"静帧预览产物无效: {output_path}")
        else:
            _validate_deliverable(output_path)
        prior["output_path"] = str(output_path)
        prior["file_size"] = output_path.stat().st_size
        thumb = task_dir / "thumbnail.jpg"
        if thumb.exists():
            prior["thumbnail_path"] = str(thumb)
        success_kwargs = dict(
            status="ready", progress=100,
            result=json.dumps(prior, ensure_ascii=False),
            claimed_by=None, lease_expires_at=None,
            # error_message 有 9 处写入、此前 0 处清空：一次重试/重启留下的
            # "Retry 1/3" 会跟着成功任务一路残留，前端无条件渲染红色错误框。
            error_message=None,
        )
        # P2-4: 落库 title/thumbnail_url（列表请求不再需读磁盘 sidecar）
        p = json.loads(payload) if isinstance(payload, str) else (payload or {})
        # 2026-08-08 P2-3：payload 显式 script=None 时 str(None)="None" 会成 title
        title = p.get("topic") or (str(p.get("script") or "")[:50] or None)
        if title:
            success_kwargs["title"] = title[:500]
        if thumb.exists():
            success_kwargs["thumbnail_url"] = f"/api/video/tasks/{task_id}/thumbnail"
        # R5: clear dirty_frames only on a successful recompose — failed/retried
        # runs preserve the pending-edit list so the user can retry. For
        # non-recompose tasks dirty_frames is already "[]" (no-op).
        if payload.get("recompose_only"):
            success_kwargs["dirty_frames"] = "[]"
        # P0-1: 成功回写必须重试--产物已生成，因一次 DB 锁就标 failed 是错的。
        # 重试耗尽也不清产物：output.mp4 已合法，用户应能被人工恢复。
        # P1-1: claimed_by 从 kwargs 弹出--db_update_if_owned 的第二个参数是守卫值，
        # 函数内部会设 claimed_by=NULL 释放租约。
        success_kwargs.pop("claimed_by", None)
        for _attempt in range(3):
            try:
                n = db_update_if_owned(task_id, WORKER_ID, **success_kwargs)
                if n == 0:
                    # 已被他人接管：丢弃自己的结果，不写 failed、不清产物
                    logger.warning("Task %s no longer owned by %s; discarding result", task_id, WORKER_ID)
                    raise _OwnershipLost(task_id)
                break
            except _OwnershipLost:
                # P0-2：必须透传——被下面的 except Exception 吞掉会空转 3 次后
                # 误抛 _SuccessWritebackFailed → 接管场景任务被误标 failed。
                raise
            except Exception:
                if _attempt == 2:
                    logger.error(
                        "Task %s SUCCEEDED but db_update failed 3x; video exists at %s/output.mp4",
                        task_id, task_dir,
                    )
                    raise _SuccessWritebackFailed(
                        f"渲染成功但状态回写失败，视频已生成于 {task_dir}/output.mp4，请联系管理员恢复"
                    )
                time.sleep(1)
        elapsed = time.monotonic() - start
        logger.info("Task complete: task=%s size=%d elapsed=%.1fs", task_id, prior["file_size"], elapsed)
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="completed"}}')
        _metrics_observe("worker_task_duration_seconds", elapsed, labels=labels_str)
    except _OwnershipLost:
        # P0-2: 渲染成功但任务已被他人接管——丢弃结果、不写 failed、不清产物、
        # 不碰任务行（新持有者正在跑/已写自己的结果）。
        logger.info("Task %s ownership lost after render; keeping artifacts, no DB write", task_id)
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="ownership_lost"}}')
        return
    except TaskCancelled:
        # Task 22: 用户主动取消/删除——不是故障。不写 failed（界面上出现红色报错
        # 是噪音），只释放 claim/lease 让任务不再被认领。若行已被 DELETE 删掉，
        # db_update 是无副作用的 no-op。
        # P0-3（2026-08-08）：换 db_update_if_owned——无守卫的 db_update 在接管
        # 场景会把新持有者的 claim 置 NULL，对方下次 renew_lease 0 行即自杀。
        elapsed = time.monotonic() - start
        logger.info("Task cancelled mid-render: task=%s elapsed=%.1fs", task_id, elapsed)
        db_update_if_owned(task_id, WORKER_ID, lease_expires_at=None)
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="cancelled"}}')
        _metrics_observe("worker_task_duration_seconds", elapsed, labels=labels_str)
        # 审查发现 7：**不**清理中间产物。cancel_task 明确承诺「保留任务行与产物
        # 目录」——那正是它区别于 delete_task 的地方（用户要的是「先停下别烧配额」，
        # 不是「连已生成的部��一起抹掉」）。删掉 audio/segments 会让用户既无法
        # 检查已生成的部分、也无法续做。失败路径（下方 except Exception）保留
        # 清理是对的：那是真故障，中间产物无价值。
        return
    except _SuccessWritebackFailed:
        # 产物有效，不清中间件（重渲要用），交给 _run_task 写 failed + 中文提示
        raise
    except QCRepairNeeded as e:
        # D19：QC 异常——不标 ready、不清产物目录（重生素材要用），
        # 任务置 qc_repair 并释放认领，等 gateway watcher 走 dirty_frames 重生回路。
        elapsed = time.monotonic() - start
        logger.warning("Task %s QC issues -> qc_repair: %s (elapsed=%.1fs)", task_id, e.qc_issues, elapsed)
        conn = db_connect()
        try:
            row = conn.execute("SELECT result FROM unified_tasks WHERE id = ?", (task_id,)).fetchone()
            prior = json.loads(row["result"] if row and row["result"] else "{}")
        finally:
            conn.close()
        prior["qc_issues"] = e.qc_issues
        # db_update_if_owned 第二个位置参数即 claimed_by（守卫值）——释放认领
        # 由其内部置 NULL（同成功路径 success_kwargs.pop("claimed_by") 的理由）
        db_update_if_owned(
            task_id, WORKER_ID,
            status="qc_repair",
            progress=96,
            result=json.dumps(prior, ensure_ascii=False),
            lease_expires_at=None,
        )
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="qc_repair"}}')
        _metrics_observe("worker_task_duration_seconds", elapsed, labels=labels_str)
        return
    except Exception:
        elapsed = time.monotonic() - start
        _metrics_inc("worker_task_total", labels=f'{{domain="{domain}",task_type="{task_type}",status="failed"}}')
        _metrics_observe("worker_task_duration_seconds", elapsed, labels=labels_str)
        _write_failure_context(
            task_dir,
            sys.exc_info(),
            task_id=task_id,
            domain=domain,
            task_type=task_type,
            elapsed=elapsed,
        )
        _cleanup_task_dir(task_dir)
        raise


def _make_progress_cb(task_id: str):
    """构造渲染进度回调。

    提到模块级只为可单测（Task 25）；行为与内联版本一致，另加租约心跳。
    """

    def progress_cb(pct: int) -> None:
        # Task 22: 取消检查点。progress_cb 是贯穿渲染全程的唯一回调（管线每帧调
        # 一次），在此回查等于逐帧可中断，无需改造各条管线。抛 TaskCancelled 由
        # 下方 except 分支处理：不写 ready、不写 failed。
        if is_task_cancelled(task_id):
            raise TaskCancelled(task_id)
        # Task 25：同一个回调顺带续租——它本就贯穿渲染全程且每帧触发，是现成的
        # 心跳点，无需给各条管线加参数。
        renew_lease(task_id)
        # 2026-07-29：高水位钳制——防 Gateway 交棒写的 95 被 Worker 第一个 5% 覆盖倒退
        conn = db_connect()
        try:
            row = conn.execute("SELECT progress FROM unified_tasks WHERE id = ?", (task_id,)).fetchone()
            current = row["progress"] if row else None
        finally:
            conn.close()
        # 无条件写：db_update 同时刷 updated_at，而 reaper 的谓词 A 正是
        # 「updated_at 超过 stall_timeout 没动」→ 健康任务被判 stalled 杀掉。
        # 交棒后 worker 发的进度几乎全被钳住（gateway 已写 95，worker 从 5 起算），
        # 若「钳住就跳过写入」，整个渲染主体期间零 DB 写入 = updated_at 冻结。
        # Gateway 侧的 ProgressUpdater 同样是「钳值但仍写行」（service.py 注释明确
        # 说明这点保证 stall 检测不受影响），此处与之对齐。
        db_update(task_id, progress=clamp_progress(current, pct))

    return progress_cb


def renew_lease(task_id: str) -> None:
    """Task 25：渲染期心跳续租。

    修前全仓只在 claim 时写 `lease_expires_at`、终态时清，渲染期零续租。
    加上 `recover_stale_tasks` 见到过期租约就判 failed，且 3 个 replica 各自
    都在跑回收循环 —— 一个**正在正常渲染**的长任务会被自己或兄弟 replica
    打成「Worker restarted during processing」。续租把「租约过期」重新变回
    它本来的语义：持有者已死，而不是任务跑得久。

    审查发现 4：**必须校验 claimed_by**。原先「刻意不校验」的理由只覆盖 SIGTERM
    路径，漏掉了同一批引入的 recover_stale_tasks 重排队路径：
        A 某帧耗时 > 租约 → 过期 → recover 置 claimed_by=NULL → B 认领 →
        A 的帧结束后 progress_cb 触发续租 → 覆盖 B 持有的行 → 双处理、产物互相覆盖。
    加 `AND claimed_by = WORKER_ID` 后，0 行影响即说明自己已不是持有者 →
    抛 TaskCancelled 让本地渲染在下个检查点停下（B 会独立跑完）。

    claimed_by 为 NULL（刚被回收、尚无人认领）时同样是 0 行影响 → 也停。此时
    继续渲染没有意义：产物归属未定，且 B 随时会认领并重跑。

    审查发现 8：续租时长必须复用 claim 的动态公式。原先写死 LEASE_DURATION，
    200 帧任务 claim 时拿到 ≈63min，第一次心跳就被塌缩回 30min —— 动态租约
    设计被架空，任何卡顿/慢帧都失去缓冲。故先读 payload 走 `_lease_for_payload`
    （与 claim 同一函数，杜绝两处漂移）。
    """
    conn = db_connect()
    try:
        row = conn.execute(
            "SELECT payload FROM unified_tasks WHERE id = ?", (task_id,)
        ).fetchone()
        lease = _lease_for_payload(row["payload"] if row else None)
        expires = (datetime.now(UTC) + timedelta(seconds=lease)).isoformat()
        cur = conn.execute(
            "UPDATE unified_tasks SET lease_expires_at = ? WHERE id = ? AND claimed_by = ?",
            (expires, task_id, WORKER_ID),
        )
        conn.commit()
        if cur.rowcount == 0:
            logger.warning(
                "lease renewal for %s affected 0 rows — task no longer owned by %s; stopping",
                task_id, WORKER_ID,
            )
            raise TaskCancelled(task_id)
    finally:
        conn.close()


def recover_stale_tasks() -> None:
    """Reset composing tasks whose lease has expired — regardless of which worker claimed them.

    P0-4: lease 过期即持有者死亡信号。修前只认 claimed_by=WORKER_ID——docker recreate
    必换 HOSTNAME，旧 claimed 行永无人认领（db_claim_task 要求 claimed_by IS NULL，
    gateway reaper 不碰 COMPOSING）→ 任务永久卡 95%，唯一出口是删任务。
    未过期 lease 不动（持有者可能仍活着）。
    """
    now = datetime.now(UTC).isoformat()
    conn = db_connect()
    try:
        # Task 25：未超 max_retries 的先**重排队**（放开 claimed_by 让任何 replica
        # 可再认领）并计 retry_count；修前一律直接判 failed，等于「租约一过期就
        # 丢任务」。retry_count 是必需的——否则任务会在过期/重排之间无限循环。
        # 上限用模块常量而非表列：production 的 unified_tasks（TaskRow）只有
        # retry_count，**没有** max_retries 列。写 SQL 引用它会 OperationalError，
        # 整个回收循环静默失效。
        requeued = conn.execute(
            """
            UPDATE unified_tasks
            SET claimed_by = NULL, lease_expires_at = NULL,
                retry_count = COALESCE(retry_count, 0) + 1, updated_at = ?
            WHERE status = 'composing' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
              AND COALESCE(retry_count, 0) < ?
            """,
            (now, now, MAX_LEASE_RETRIES),
        )
        # 重试耗尽的才判 failed。
        failed = conn.execute(
            """
            UPDATE unified_tasks
            SET status = 'failed',
                error_message = 'Worker restarted during processing; max retries exhausted',
                claimed_by = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE status = 'composing' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?
              AND COALESCE(retry_count, 0) >= ?
            """,
            (now, now, MAX_LEASE_RETRIES),
        )
        conn.commit()
        if requeued.rowcount:
            logger.warning("Re-queued %d composing task(s) with expired leases", requeued.rowcount)
        if failed.rowcount:
            logger.error("Failed %d composing task(s): expired lease and retries exhausted", failed.rowcount)
    finally:
        conn.close()


def release_my_leases() -> None:
    """Release all leases held by this worker (for graceful shutdown)."""
    conn = db_connect()
    try:
        conn.execute(
            """
            UPDATE unified_tasks
            SET claimed_by = NULL, lease_expires_at = NULL,
                status = 'composing'
            WHERE claimed_by = ? AND status = 'composing'
            """,
            (WORKER_ID,),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info("Released all leases for worker %s", WORKER_ID)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            active = MAX_CONCURRENT_TASKS - _concurrency._value  # type: ignore[attr-defined]
            self.wfile.write(json.dumps({
                "status": "ok",
                "worker_id": WORKER_ID,
                "active_tasks": active,
                "max_concurrent": MAX_CONCURRENT_TASKS,
                # T3 双池：remotion 池额外宣告 remotion_course 能力（gateway
                # worker_probe 区分两池——ffmpeg 池不宣告，防止 gateway 把
                # remotion 任务交棒给没有 Chrome 的 ffmpeg worker）。
                "worker_pool": WORKER_POOL,
                "capabilities": (
                    ["v1", "v2_mixed", "v3_segmented", "remotion_course"]
                    if WORKER_POOL == "remotion"
                    else ["v1", "v2_mixed", "v3_segmented"]
                ),
            }).encode())
        elif self.path == "/metrics":
            self._metrics()
        else:
            self.send_response(404)
            self.end_headers()

    def _metrics(self):
        active = MAX_CONCURRENT_TASKS - _concurrency._value  # type: ignore[attr-defined]
        lines = [
            "# HELP worker_active_tasks Number of tasks currently being processed",
            "# TYPE worker_active_tasks gauge",
            f"worker_active_tasks{{worker_id=\"{WORKER_ID}\"}} {active}",
            "# HELP worker_max_concurrent Maximum concurrent tasks this worker can handle",
            "# TYPE worker_max_concurrent gauge",
            f"worker_max_concurrent{{worker_id=\"{WORKER_ID}\"}} {MAX_CONCURRENT_TASKS}",
            "# HELP worker_task_total Total tasks processed by this worker",
            "# TYPE worker_task_total counter",
        ]
        with _metrics_lock:
            for key, value in sorted(_metrics_counters.items()):
                # key is "worker_task_total{labels}"
                if key.startswith("worker_task_total"):
                    labels_part = key[len("worker_task_total"):]
                    lines.append(f"worker_task_total{labels_part} {value}")

        lines.append("# HELP worker_task_duration_seconds Task processing duration")
        lines.append("# TYPE worker_task_duration_seconds histogram")
        with _metrics_lock:
            for key, data in sorted(_metrics_histograms.items()):
                if not key.startswith("worker_task_duration_seconds"):
                    continue
                labels_part = key[len("worker_task_duration_seconds"):]
                # Re-format labels with proper escaping via _format_labels
                inner = labels_part.strip("{}")
                label_pairs = {}
                if inner:
                    for pair in inner.split(","):
                        k, v = pair.split("=", 1)
                        label_pairs[k.strip()] = v.strip('"')
                for b in DEFAULT_BUCKETS:
                    b_labels = {**label_pairs, "le": str(b)}
                    b_label_str = _format_labels(b_labels)
                    count = data["buckets"].get(b, 0)
                    lines.append("worker_task_duration_seconds_bucket" + b_label_str + " " + str(count))
                inf_labels = _format_labels({**label_pairs, "le": "+Inf"})
                lines.append("worker_task_duration_seconds_bucket" + inf_labels + " " + str(data["count"]))
                lines.append("worker_task_duration_seconds_count" + labels_part + " " + str(data["count"]))
                lines.append("worker_task_duration_seconds_sum" + labels_part + " " + f"{data['sum']:.3f}")

        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
        self.end_headers()
        self.wfile.write("\n".join(lines).encode())

    def log_message(self, *args):
        pass  # suppress access logs


# Graceful shutdown
_shutdown = threading.Event()
# T7（2026-09-08 remotion-course）：remotion 池 still 预览槽（独立于渲染槽计数）。
# MAX_CONCURRENT_TASKS 语义更新为"渲染槽数"；still 槽 = REMOTION_STILL_SLOTS
# （默认 1；测试机/本机降级可调）。ffmpeg 池不创建（零成本）。
_still_concurrency = threading.Semaphore(REMOTION_STILL_SLOTS) if WORKER_POOL == "remotion" else None

# Task 25 / W-7：draining 与 shutdown 分离。draining 一置起主循环立刻停止
# **认领**新任务，但仍要等在跑线程收尾；shutdown 才是「可以退出了」。
_draining = threading.Event()
_concurrency = threading.Semaphore(MAX_CONCURRENT_TASKS)

# 在跑的任务线程登记表——SIGTERM 时要 join 它们
_running_threads: list[threading.Thread] = []
_running_lock = threading.Lock()

# join 超时：给在跑任务留出收尾时间。超时后仍会释放租约并退出（容器有 SIGKILL
# 兜底），但此时至少已尽力避免双认领。
SHUTDOWN_JOIN_TIMEOUT = float(os.environ.get("SHUTDOWN_JOIN_TIMEOUT", "120"))


def _register_running_thread(t: threading.Thread) -> None:
    with _running_lock:
        _running_threads[:] = [x for x in _running_threads if x.is_alive()]
        _running_threads.append(t)


def _sigterm_handler(signum, frame):
    """W-7：只置标志位——停认领 + 让主循环退出。join/释放租约在 main() 里做。

    审查发现 11：此前 join(timeout=120s) 直接在信号处理函数里跑，而 Docker
    默认 stop grace 只有 10s（compose 未设 stop_grace_period）→ 10s 一到
    SIGKILL，`release_my_leases()` 根本不执行，任务只能等租约过期被
    recover_stale_tasks 兜底（动态租约最长 2h，等不起）。信号处理器必须
    尽快返回；真正的 drain 挪到 main() 退出循环后，配合 compose 的
    `stop_grace_period` 给足 120s+ 余量。

    修前收到 SIGTERM 立刻 `release_my_leases()` 把任务重置为可认领的
    `composing`，但**不 join** 在跑的 daemon 线程。旧线程继续渲染、另一个
    replica 立刻认领同一任务 → 双处理、产物互相覆盖。项目
    `docker-compose-deploy.yaml` 是 `replicas: 3`，每次 `up -d` 重部署都给
    3 个 replica 发 SIGTERM，这不是理论风险。
    """
    logger.info("Received SIGTERM, draining: stop claiming, wait for running tasks...")
    _draining.set()
    _shutdown.set()


def _drain_running_tasks() -> None:
    """join 在跑线程 → 全部结束（或超时）后才释放租约。main() 退出循环后调用。"""
    with _running_lock:
        alive = [t for t in _running_threads if t.is_alive()]
    for t in alive:
        t.join(timeout=SHUTDOWN_JOIN_TIMEOUT)
        if t.is_alive():
            logger.warning("Task thread %s did not finish within %.0fs; releasing lease anyway", t.name, SHUTDOWN_JOIN_TIMEOUT)

    # 只有在跑线程都结束（或超时）后才放开租约，避免与兄弟 replica 双认领
    release_my_leases()
    logger.info("Drain complete, shutting down")


def _run_task(task: dict, *, slot_pre_acquired: bool = False, slot: str | None = None) -> None:
    """Run a single task with concurrency semaphore.

    R3 v2（2026-08-31）：主循环 claim 前已 acquire(blocking=False) 占槽并把许可
    传进来（slot_pre_acquired=True），线程内不得再阻塞 acquire——否则回到「claim 后
    阻塞、租约从 claim 起零续租」的原病灶。保留无槽路径仅为兼容潜在旧调用方。

    T-A1（2026-09-09 R1）：释放按入口预占的槽位（slot 参数，由 _claim_one_task
    返回）——修前按 task_type 推断，渲染槽误领 still 任务后释放的是 still 槽，
    _concurrency 许可永久泄漏。slot=None 的旧调用方维持按类型推断的兼容路径。
    """
    if slot == "render":
        sem = _concurrency
    elif slot == "still" and _still_concurrency is not None:
        sem = _still_concurrency
    else:
        is_still = task.get("task_type") == "remotion_still"
        sem = _still_concurrency if (is_still and _still_concurrency is not None) else _concurrency
    if not slot_pre_acquired:
        sem.acquire()
    try:
        process_task(task)
    except Exception as exc:
        logger.exception("Task failed: %s", task["id"])
        # P0-3（2026-08-08）：带守卫回写——租约过期被 recover 重排 + 他人 claim 后，
        # 本 worker 的迟到失败回写会把别人的任务打成 failed（db_update_if_owned
        # 注释记录该场景实测复现）。n==0 = 任务已归他人，本 worker 无权再写。
        n = db_update_if_owned(task["id"], WORKER_ID, status="failed",
                               error_message=str(exc)[:500], lease_expires_at=None)
        if n == 0:
            logger.warning("Task %s failed but ownership lost; skipping failed writeback", task["id"])
    finally:
        sem.release()


def _claim_one_task() -> tuple[dict | None, str | None]:
    """T-A1（2026-09-09 R1/v4 评审）：单轮认领——渲染槽与 still 槽**独立双 if**。

    修前主循环是 if(渲染槽)…elif(still槽)：渲染槽空闲（acquire 成功）但 claim
    落空时（无渲染任务），elif 同轮永不执行 → still 任务在空闲期彻底饿死。
    现在渲染槽 claim 落空即归还许可、同轮回落试 still 槽，两槽互不挡道。

    返回 (task, slot)：slot 是入口预占的槽位名，_run_task 按它配对释放信号量
    （渲染槽谓词已收窄为 remotion_course，still 只从 still 槽进）。
    claim 抛异常同样归还许可（泄漏会让 worker 永久哑火——R3 既有约束保持）。
    """
    if not _draining.is_set() and _concurrency.acquire(blocking=False):
        try:
            task = db_claim_task(task_types=_render_slot_task_types())
        except Exception:
            logger.exception("db_claim_task failed")
            task = None
        if task is not None:
            return task, "render"
        _concurrency.release()
    if (
        not _draining.is_set()
        and _still_concurrency is not None
        and _still_concurrency.acquire(blocking=False)
    ):
        # T7：still 预览槽（认领谓词收窄到 remotion_still，成片渲染优先认领
        # 不被预览任务挤占；短租约 600s）
        try:
            task = db_claim_task(task_types=["remotion_still"])
        except Exception:
            logger.exception("db_claim_task (still) failed")
            task = None
        if task is not None:
            return task, "still"
        _still_concurrency.release()
    return None, None


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    logger.info("Production Worker starting: worker_id=%s db=%s storage=%s", WORKER_ID, DB_PATH, STORAGE_DIR)

    # Startup check: shared storage accessible
    for label, path_str in _DOMAIN_STORAGE.items():
        test_path = Path(path_str)
        if not test_path.exists():
            logger.warning("Storage directory for %s not found: %s (will create on demand)", label, path_str)
            test_path.mkdir(parents=True, exist_ok=True)

    # Register SIGTERM handler for graceful shutdown
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    recover_stale_tasks()
    last_recover = time.monotonic()
    RECOVER_INTERVAL = 60.0  # P0-4: 周期回收（修前仅启动一次，启动后过期的 lease 无人收）

    # Start health endpoint in background thread
    httpd = HTTPServer(("0.0.0.0", HEALTH_PORT), HealthHandler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    while not _shutdown.is_set():
        try:
            # Task 25：draining 期间不再认领新任务（SIGTERM 已到，只等在跑的收尾）
            # R3（2026-08-30；v2 2026-08-31 原子化）：claim 前先 acquire(blocking=False)
            # 真正扣减槽位——修前 claim 不看槽位，一个副本把全部积压 hoard 成阻塞在
            # acquire() 上的线程（租约零续租、recover 重排、重试耗尽假失败）；v1 的
            # `_value > 0` 预检是跨线程 check-then-act（真 acquire 在子线程），窗口内
            # 仍会超领 1~2 个任务回到同病灶，且依赖 CPython 私有属性。claim 落空立即
            # 归还槽位；claim 抛异常也必须归还（许可泄漏会让 worker 永久哑火）。
            # T-A1（2026-09-09）：认领逻辑抽 _claim_one_task()（独立双 if + 渲染槽
            # claim 落空同轮回落 still）——if/elif 结构下渲染槽空闲期 elif 永不执行，
            # still 任务饿死；渲染槽谓词同步收窄为 remotion_course。
            task, slot = _claim_one_task()
            if task:
                logger.info("Claimed task: %s domain=%s type=%s slot=%s", task["id"], task["domain"], task["task_type"], slot)
                t = threading.Thread(target=_run_task, args=(task,), kwargs={"slot_pre_acquired": True, "slot": slot}, daemon=True)
                _register_running_thread(t)
                t.start()
            else:
                if time.monotonic() - last_recover > RECOVER_INTERVAL:
                    recover_stale_tasks()
                    last_recover = time.monotonic()
                _shutdown.wait(POLL_INTERVAL)
        except Exception:
            logger.exception("Worker loop error")
            _shutdown.wait(POLL_INTERVAL)

    # 退出循环后才 drain：join 在跑任务 + 释放租约。在信号处理器里做会被
    # Docker 默认 10s stop grace SIGKILL（compose 已配 stop_grace_period 兜底）。
    _drain_running_tasks()
    logger.info("Worker %s shut down gracefully", WORKER_ID)


if __name__ == "__main__":
    main()
