#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Media usage ledger emitter shared by ppt-master's TrainerAI backends (stdlib only).

TrainerAI patch P35（2026-11-23 首页六卡媒体计量计划 PR 3）：``tts_backends/backend_trainerai.py``
与 ``image_backends/backend_qihu360.py`` 在供应商**明确成功**后各追加一行 JSON 到线程工作区
账本 ``/mnt/user-data/workspace/.usage/media-usage.jsonl``（生产仅本地 Aio 容器沙箱：该目录
即宿主 ``sandbox_work_dir``）。gateway 在 run 结束后读取账本尾部并落 ``external_api_calls``。

发射规则（与 skills/**/scripts/_doubao_tts.py 五份镜像完全一致）：

- 永不抛错、不写 stdout/stderr——计量失败不得影响制作；
- 目录取 ``DEER_FLOW_MEDIA_LEDGER_DIR``（仅测试用覆盖），缺省 ``/mnt/user-data/workspace/.usage``；
- 单行 ≤4096 B，``event_id`` 为 32 位随机 hex；
- ``mode`` 参数受 umask 影响，每次写入显式 ``chmod`` 目录 0o755、``fchmod`` 文件 0o644
  （失败忽略），保证跨 uid 的 gateway 可读；
- 只用 Python 3.10 可用的 stdlib API（沙箱解释器上限 3.10）。

事件契约（服务端映射 provider/method/status，发射端不得指定；未知字段会被忽略）：

- tts:     ``event_id`` / ``kind="tts"`` / ``with_timestamps`` / ``char_count`` [/ ``elapsed_ms``]
- image:   ``event_id`` / ``kind="image"`` / ``model``（本次实际下发的模型，原样）/
           ``width`` / ``height`` [/ ``elapsed_ms``]
"""

from __future__ import annotations

import json
import os
import time
import uuid

LEDGER_DIR_ENV = "DEER_FLOW_MEDIA_LEDGER_DIR"
LEDGER_DIR_DEFAULT = "/mnt/user-data/workspace/.usage"
LEDGER_FILE = "media-usage.jsonl"


def media_ledger_dir() -> str:
    # os.getenv（而非 os.environ）：与技能扫描器的 env-dump 启发式保持一致。
    return os.getenv(LEDGER_DIR_ENV) or LEDGER_DIR_DEFAULT


def emit_media_usage(event: dict) -> None:
    """Append one JSON line to the thread ledger. Never raises, never prints."""
    try:
        ledger_dir = media_ledger_dir()
        os.makedirs(ledger_dir, exist_ok=True)
        try:
            os.chmod(ledger_dir, 0o755)
        except OSError:
            pass
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
        fd = os.open(os.path.join(ledger_dir, LEDGER_FILE), os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
        try:
            try:
                os.fchmod(fd, 0o644)
            except OSError:
                pass
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass  # 计量失败不得影响制作


def emit_tts_usage(text: str, *, with_timestamps: bool, started: float) -> None:
    # 事件字典的构造（len(text)、算术）也在 try 内：「永不抛错」覆盖整个调用。
    try:
        emit_media_usage(
            {
                "event_id": uuid.uuid4().hex,
                "kind": "tts",
                "with_timestamps": with_timestamps,
                "char_count": len(text),
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
    except Exception:
        pass  # 计量失败不得影响制作


def emit_image_usage(*, model: str, size: str, started: float) -> None:
    """``size`` is the ``"WxH"`` string actually sent; if it cannot be parsed the event is
    still emitted (without width/height) — the paid call happened regardless."""
    try:
        dimensions: dict = {}
        try:
            width, height = (int(value) for value in size.split("x"))
            dimensions = {"width": width, "height": height}
        except Exception:
            pass
        emit_media_usage(
            {
                "event_id": uuid.uuid4().hex,
                "kind": "image",
                "model": model,
                **dimensions,
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
        )
    except Exception:
        pass  # 计量失败不得影响制作
