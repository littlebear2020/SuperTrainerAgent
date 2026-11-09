"""Remotion rendering via SandboxProvider.

Runs entirely in Gateway. Uses the existing Sandbox abstraction:
  - write_file() to deploy composition JSON + props
  - execute_command() to run npx remotion render
  - All sync sandbox calls are wrapped in asyncio.to_thread() to avoid blocking the event loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import TYPE_CHECKING

from app.gateway._shared.task_framework.metrics import metrics
from app.gateway._shared.task_framework.service import ProgressUpdater

from ..storyboard import Storyboard
from .remotion_props import build_remotion_props
from .remotion_template import deploy_template, upload_audio_files, upload_bgm

if TYPE_CHECKING:
    from deerflow.sandbox.sandbox import Sandbox

logger = logging.getLogger(__name__)


def _decode_sandbox_output(b64: str, output_path: Path) -> int:
    """解码 sandbox 取回的 base64 → 校验 → 落盘。返回字节数（Task 11）。

    修前两处都是 ``output_path.write_bytes(base64.b64decode(b64))`` 后直接
    ``stat().st_size`` 记录、**从不校验**。sandbox 里渲染失败或 base64 命令本身
    出错时会返回空串/错误文本（如 "bash: base64: command not found"），解码出
    0 字节或垃圾仍照常 set_result 标成功 —— 用户拿到打不开的文件。

    校验失败时**不留半成品文件**：下游只看路径存在性，残留文件会被当成有效产物。
    """
    import base64
    import binascii

    raw = (b64 or "").strip()
    if not raw:
        raise RuntimeError(f"Remotion sandbox 取回为空（0 字节 / empty base64）: {output_path}；sandbox 内渲染可能失败或 base64 命令不可用")
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError) as exc:
        raise RuntimeError(f"Remotion sandbox 取回不是合法 base64: {exc}; 前 120 字符={raw[:120]!r}") from exc

    if not data:
        # 只拦 0 字节。不设「最小合理体积」阈值——那是本计划范围外的猜测，且既有
        # 测试用 12B 的合法 mp4 头做 fixture，凭空设 1KB 门槛会误杀。真正的
        # 「能不能看」由 worker 侧 _validate_deliverable 的 ffprobe 校验兜住。
        raise RuntimeError(f"Remotion 产物解码后 0 字节（empty output）: {output_path}; 前 120 字符={raw[:120]!r}")

    output_path.write_bytes(data)
    return len(data)


# ── Input validation (security) ───────────────────────────────────────────

_SAFE_COMPOSITION_RE = re.compile(r"^[A-Za-z0-9_\-]+$")
_SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9_\-./]+$")


def _validate_remotion_id(value: str, field: str) -> None:
    """Reject strings containing shell metacharacters or empty values."""
    if not value or not _SAFE_COMPOSITION_RE.match(value):
        raise ValueError(f"invalid {field}: '{value}' — must be alphanumeric/hyphen/underscore only")


def _validate_entry_point(value: str) -> None:
    """Reject entry_point with shell metacharacters."""
    if not value or not _SAFE_PATH_RE.match(value):
        raise ValueError(f"invalid entry_point: '{value}' — must be a safe relative path (alphanumeric, /, ., -, _)")


# Pre-installed Chrome Headless Shell path inside the sandbox Docker image
# (see docker/sandbox/Dockerfile). When present, pass --browser-executable so
# Remotion reuses it instead of downloading Chrome into a non-writable cache.
_CHROME_PATH = "/opt/chrome-headless-shell/chrome-headless-shell"


def _browser_executable_flag(sandbox: Sandbox) -> str:
    """Return '--browser-executable=...' if the pre-installed Chrome exists and is executable, else ''.

    Remotion's ``npx remotion render`` auto-downloads Chrome Headless Shell when no
    browser is registered. In the sandbox the default download/cache dir resolves
    under a non-writable /mnt/... path → EACCES. Pointing Remotion at the
    pre-installed Chrome (built into the image) bypasses the download entirely.
    """
    try:
        out = sandbox.execute_command(f"test -x {_CHROME_PATH} && echo OK || echo MISSING")
        if "OK" in out:
            return f"--browser-executable={_CHROME_PATH}"
    except Exception:
        logger.debug("Chrome executable probe failed", exc_info=True)
    return ""


async def _sandbox_write(sandbox: Sandbox, path: str, content: str) -> None:
    await asyncio.to_thread(sandbox.write_file, path, content)


async def _sandbox_exec(sandbox: Sandbox, cmd: str) -> str:
    return await asyncio.to_thread(sandbox.execute_command, cmd)


async def _sandbox_list(sandbox: Sandbox, path: str, max_depth: int = 2) -> list[str]:
    return await asyncio.to_thread(sandbox.list_dir, path, max_depth)


async def run_remotion(
    payload: dict,
    *,
    task_dir: Path,
    sandbox: Sandbox,
    progress: ProgressUpdater,
) -> None:
    """Render a Remotion composition in the sandbox.

    Steps:
      1. Write composition config and props to sandbox workspace
      2. Execute ``npx remotion render`` in sandbox
      3. Copy output mp4 from sandbox to task_dir
      4. Record result via progress.set_result()

    Raises:
        RuntimeError: If rendering fails.
        ValueError: If required payload fields are missing.
    """
    composition = payload.get("composition", "")
    props = payload.get("props", {})
    entry_point = payload.get("entry_point", "src/index.tsx")

    _validate_remotion_id(composition, "composition")
    _validate_entry_point(entry_point)

    start = time.monotonic()

    await progress(progress=10)

    # Always use workspace (writable); symlink node_modules + fix rspack binding
    from .remotion_template import _DOCKER_TEMPLATE, _WORKSPACE, _ensure_rspack_binding, _is_docker_sandbox

    render_dir = _WORKSPACE

    env_prefix = ""
    if _is_docker_sandbox(sandbox):
        sandbox.execute_command(f"ln -sfn {_DOCKER_TEMPLATE}/node_modules {render_dir}/node_modules")
        env_prefix = _ensure_rspack_binding(sandbox)

    # 1. Write props.json to sandbox
    props_json = json.dumps(props, ensure_ascii=False, indent=2)
    await _sandbox_write(sandbox, f"{render_dir}/props.json", props_json)

    # F: render 前检查 CJK 字体（缺失写 warning sidecar，不阻断）
    from .remotion_template import _warn_if_no_cjk_font

    _warn_if_no_cjk_font(task_dir, sandbox=sandbox)

    await progress(progress=20)

    # 2. Build and execute remotion render command
    # --browser-executable: reuse the image's pre-installed Chrome instead of
    # downloading into a non-writable cache (which causes EACCES in the sandbox).
    browser_flag = await asyncio.to_thread(_browser_executable_flag, sandbox)
    cmd = f"cd {render_dir} && {env_prefix}npx remotion render {entry_point} {composition} {render_dir}/out.mp4 --props={render_dir}/props.json --codec=h264 --image-format=jpeg --crf=18 {browser_flag}".strip()

    await progress(progress=30)
    logger.info("Remotion render starting: composition=%s render_dir=%s", composition, render_dir)

    output = await _sandbox_exec(sandbox, cmd)

    await progress(progress=85)

    # 3. Check if output was produced
    listing = await _sandbox_list(sandbox, render_dir, max_depth=1)
    if "out.mp4" not in listing:
        raise RuntimeError(f"Remotion render did not produce output.mp4. Sandbox output (last 1000 chars): {output[-1000:] if output else '(empty)'}")

    # 4. Copy output from sandbox virtual path to local task_dir
    output_path = task_dir / "output.mp4"

    from deerflow.sandbox.local.local_sandbox import LocalSandbox

    if isinstance(sandbox, LocalSandbox):
        # For local sandbox, we can directly access the file
        import shutil

        physical_path = sandbox.resolve_virtual_path(f"{render_dir}/out.mp4")
        src = Path(physical_path)
        if src.exists():
            shutil.copy2(src, output_path)
        else:
            raise RuntimeError(f"Remotion output not found at {physical_path}")
    else:
        # For Docker/remote sandbox, fetch via base64
        b64 = await _sandbox_exec(sandbox, f"base64 {render_dir}/out.mp4")
        _decode_sandbox_output(b64, output_path)

    file_size = output_path.stat().st_size
    await progress(progress=95)
    await progress.set_result(
        output_path=str(output_path),
        file_size=file_size,
    )

    logger.info("Remotion render complete: output=%s size=%d", output_path, file_size)

    # Record remotion-specific rendering duration (prepare phase total tracked by VideoGenService)
    elapsed = time.monotonic() - start
    metrics.observe("video_gen_task_duration_seconds", elapsed, labels={"type": "remotion", "phase": "remotion"}, help_text="Video gen task duration in seconds")

    return None


async def run_remotion_storyboard(
    storyboard: Storyboard,
    *,
    task_dir: Path,
    sandbox: Sandbox,
    progress: ProgressUpdater,
    subtitle_path: str | None = None,
    template_id: str | None = None,
) -> None:
    """Render a data-driven Remotion video from a Storyboard.

    Steps:
      1. Deploy template to sandbox workspace
      2. Upload audio files
      3. Build & write props.json
      4. Execute npx remotion render
      5. Copy output mp4 from sandbox to task_dir

    Returns None (Gateway-complete path).
    """
    cfg = storyboard.config
    fps = cfg.fps
    width = cfg.width
    height = cfg.height
    total_frames = round(storyboard.total_duration * fps)
    audio_dir = task_dir / "audio"

    # 1. Deploy template (sync function → run in thread to not block event loop)
    await progress(progress=10)
    style = cfg.style or "default"
    composition_id, render_dir, env_prefix = await asyncio.to_thread(deploy_template, sandbox, style, fps, width, height, total_frames, template_id)

    # F: render 前检查 CJK 字体（缺失写 warning sidecar，不阻断）
    from .remotion_template import _warn_if_no_cjk_font

    _warn_if_no_cjk_font(task_dir, sandbox=sandbox)

    # 2. Upload audio files
    await progress(progress=20)
    if audio_dir.exists():
        await asyncio.to_thread(upload_audio_files, sandbox, audio_dir, render_dir)

    # 2b. Upload BGM if configured
    if cfg.bgm_path:
        await asyncio.to_thread(upload_bgm, sandbox, cfg.bgm_path, render_dir)

    # 3. Build & write props.json
    await progress(progress=30)
    props = build_remotion_props(storyboard, subtitle_path)
    await _sandbox_write(sandbox, f"{render_dir}/props.json", json.dumps(props, ensure_ascii=False))

    # 4. Execute remotion render (env_prefix may include NAPI_RS_NATIVE_LIBRARY_PATH for rspack fix)
    # --browser-executable: reuse the image's pre-installed Chrome instead of
    # downloading into a non-writable cache (which causes EACCES in the sandbox).
    browser_flag = await asyncio.to_thread(_browser_executable_flag, sandbox)
    cmd = f"cd {render_dir} && {env_prefix}npx remotion render src/index.ts {composition_id} {render_dir}/out.mp4 --props={render_dir}/props.json --codec=h264 --crf=18 {browser_flag}".strip()
    output = await _sandbox_exec(sandbox, cmd)

    await progress(progress=85)

    # 5. Check output
    listing = await _sandbox_list(sandbox, render_dir, max_depth=1)
    if "out.mp4" not in listing:
        raise RuntimeError(f"Remotion render did not produce output. Sandbox output (last 1000 chars): {output[-1000:] if output else '(empty)'}")

    # 6. Copy output
    output_path = task_dir / "output.mp4"

    from deerflow.sandbox.local.local_sandbox import LocalSandbox

    if isinstance(sandbox, LocalSandbox):
        import shutil

        physical_path = sandbox.resolve_virtual_path(f"{render_dir}/out.mp4")
        src = Path(physical_path)
        if src.exists():
            shutil.copy2(src, output_path)
        else:
            raise RuntimeError(f"Remotion output not found at {physical_path}")
    else:
        b64 = await _sandbox_exec(sandbox, f"base64 {render_dir}/out.mp4")
        _decode_sandbox_output(b64, output_path)

    file_size = output_path.stat().st_size
    await progress(progress=95)
    await progress.set_result(
        output_path=str(output_path),
        file_size=file_size,
    )

    return None
