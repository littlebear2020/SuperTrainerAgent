"""Remotion template deployment — deploy remotion-base to sandbox workspace.

Handles:
- Style → template directory mapping (STYLE_MAP)
- Root.tsx generation with dynamic fps/width/height/total_frames
- Template file deployment to sandbox via write_file
- node_modules: Docker → reuse /opt/remotion-template; Local → symlink from source
- Audio file upload (LocalSandbox: shutil.copy, Docker: upload_file_stream)

⚠️ 2026-08-03 Task 7：本模块在 engine_arch="multi_engine"（现行）下**不可达**。
remotion 走 chat 交接（前端 remotion-chat-handoff.ts → course-video agent 直接写
Remotion 代码），POST /api/video/create 的 video_type Literal 不含 remotion
（``schemas.CreateVideoRequest``）→ 422。仅 2026-07-15 之前创建的 legacy 草稿
可能触达 ``_render_remotion``。
特别注意 ``STYLE_MAP``：原始反馈「风格下拉对 remotion 不生效」的修复点**不在这里**
（这里到不了），而在前端门控（Task 6 的 ``style-visibility.ts``）。
审查此处前请先确认调用路径真实存在——2026-08-03 的一次审计因误判此为活代码
而浪费了一轮。
"""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Style → template mapping
# ---------------------------------------------------------------------------

STYLE_MAP: dict[str, str] = {
    "default": "remotion-base",
    "professional": "remotion-base",
}

_COMPOSITION_ID = "DataDrivenVideo"

# Docker: pre-built template with working node_modules (including native bindings)
_DOCKER_TEMPLATE = "/opt/remotion-template"

# Local dev workspace
_WORKSPACE = "/mnt/user-data/workspace"


# ---------------------------------------------------------------------------
# Root.tsx template
# ---------------------------------------------------------------------------

# NOTE: Uses str.format(). Literal JSX curly braces must be doubled {{ }}.
# Only {fps}/{width}/{height}/{total_frames} are placeholders (single braces).
_ROOT_TSX_TEMPLATE = """import "./index.css";
import {{ Composition }} from "remotion";
import {{ DataDrivenVideo }} from "./Composition";

export const RemotionRoot: React.FC = () => {{
  return (
    <>
      <Composition
        id="DataDrivenVideo"
        component={{DataDrivenVideo}}
        durationInFrames={{{total_frames}}}
        fps={{{fps}}}
        width={{{width}}}
        height={{{height}}}
      />
    </>
  );
}};
"""


def _generate_root_tsx(fps: int, width: int, height: int, total_frames: int) -> str:
    """Generate Root.tsx content with dynamic composition parameters."""
    return _ROOT_TSX_TEMPLATE.format(
        fps=fps,
        width=width,
        height=height,
        total_frames=total_frames,
    )


# ---------------------------------------------------------------------------
# Template path resolution
# ---------------------------------------------------------------------------


def _get_skills_path() -> Path:
    """Resolve the skills directory path."""
    from deerflow.config import get_app_config

    config = get_app_config()
    return config.skills.get_skills_path()


def _get_base_template_path() -> Path:
    """Return the remotion-base template path (shared scaffolding)."""
    return _get_skills_path() / "public" / "auto-video-flow" / "templates" / "remotion-base"


def check_cjk_font_in_template() -> dict:
    """Task 3n: 检查 remotion 模板是否含 CJK 字体（中文不渲染为方块）。

    扫描模板目录的字体文件 + @font-face 引用。部署时还需在容器内 fc-list :lang=zh 验证。
    返回 {has_cjk_font, font_files, note}。
    """
    base = _get_base_template_path()
    font_files = []
    if base.exists():
        font_files = list(base.rglob("*.ttf")) + list(base.rglob("*.otf")) + list(base.rglob("*.woff2"))
    # 启发式：文件名含 cjk/noto/sc/hans 视为 CJK 字体
    cjk_names = ("cjk", "noto", "sc", "hans", "hei", "song", "kai", "ming")
    has_cjk = any(any(n in f.name.lower() for n in cjk_names) for f in font_files)
    return {
        "has_cjk_font": has_cjk,
        "font_count": len(font_files),
        "note": ("模板含 CJK 字体文件" if has_cjk else "模板无 CJK 字体文件——需 bake fonts-noto-cjk 进 remotion 镜像/模板，否则中文渲染为方块"),
    }


def check_cjk_font(sandbox) -> dict:
    """A2: 在渲染沙箱内 fc-list :lang=zh 探测 CJK 字体（模板目录无字体文件，旧扫模板是 false proxy）。

    sandbox.execute_command 跑 fc-list；returncode==0 且 stdout 非空→has_cjk_font True；
    returncode==0 且 stdout 空→真无 CJK→False（写 sidecar）；returncode!=0 或异常→fc-list
    不可用→不确定→不误报（返 True，避免 Docker 镜像有 fonts-noto-cjk 却每次误报）。
    """
    if sandbox is None:
        return {"has_cjk_font": True, "note": "no sandbox context; assuming CJK fonts (avoid false alarm)"}
    try:
        res = sandbox.execute_command("fc-list :lang=zh")
        out = getattr(res, "stdout", "") or ""
        rc = getattr(res, "returncode", 0)
        if rc != 0:
            return {"has_cjk_font": True, "note": f"fc-list unavailable (rc={rc}); assuming CJK fonts (avoid false alarm)"}
        has = bool(out.strip())
        return {
            "has_cjk_font": has,
            "note": ("CJK fonts present" if has else "沙箱无 CJK 字体——中文将渲染为方块（bake fonts-noto-cjk 进 remotion/sandbox 镜像）"),
        }
    except Exception:
        logger.debug("fc-list probe failed; assuming CJK fonts present (avoid false alarm) — 若中文仍渲染成方块，手动跑 `fc-list :lang=zh` 排查", exc_info=True)
        return {"has_cjk_font": True, "note": "fc-list probe failed; assuming CJK fonts (avoid false alarm)"}


def _warn_if_no_cjk_font(task_dir: Path, *, sandbox=None) -> None:
    """F: render 前检查 CJK 字体；缺失写 sidecar（不阻断）。A2: 改沙箱内 fc-list 真探测（旧扫模板=永久误报）。"""
    try:
        report = check_cjk_font(sandbox)
    except Exception:
        logger.warning("CJK font check failed", exc_info=True)
        return
    if not report.get("has_cjk_font"):
        note = report.get("note", "无 CJK 字体——中文将渲染为方块")
        logger.warning("remotion CJK font missing: %s", note)
        try:
            (Path(task_dir) / "remotion_cjk_warning.json").write_text(json.dumps({"warning": note}, ensure_ascii=False), encoding="utf-8")
        except OSError:
            logger.warning("failed to write remotion_cjk_warning.json", exc_info=True)


def get_template_path(style: str, template_id: str | None = None) -> Path:
    """Return template source directory.

    If template_id is given and resolves to a directory, use that.
    Otherwise fall back to STYLE_MAP → remotion-base.
    """
    if template_id:
        from ..templates.schema import get_template_dir

        tdir = get_template_dir(template_id)
        if tdir:
            return tdir
    template_name = STYLE_MAP.get(style, STYLE_MAP["default"])
    return _get_skills_path() / "public" / "auto-video-flow" / "templates" / template_name


# ---------------------------------------------------------------------------
# Docker detection
# ---------------------------------------------------------------------------


def _is_docker_sandbox(sandbox) -> bool:
    """Check if sandbox has pre-built /opt/remotion-template (Docker mode)."""
    try:
        result = sandbox.execute_command(f"test -d {_DOCKER_TEMPLATE}/node_modules && echo OK || echo MISSING")
        return "OK" in result
    except Exception:
        return False


def _ensure_rspack_binding(sandbox) -> str:
    """Verify @rspack/binding works; if not, install to /tmp and return env prefix.

    Returns an env prefix string for the render command, e.g.:
      ''  — binding already works, no fix needed
      'NAPI_RS_NATIVE_LIBRARY_PATH=/tmp/rspack-binding/.../rspack.linux-arm64-gnu.node '

    Uses NAPI_RS_NATIVE_LIBRARY_PATH env var which binding.js checks FIRST
    (before any node_modules resolution), bypassing all symlink issues.
    See binding.js line 69: require(process.env.NAPI_RS_NATIVE_LIBRARY_PATH)
    """
    # Step 1: Quick check — does it already work?
    check = sandbox.execute_command(f"cd {_DOCKER_TEMPLATE} && node -e \"try{{require('@rspack/binding');console.log('RSPACK_OK')}}catch(e){{console.log('RSPACK_FAIL')}}\" 2>&1")
    if "RSPACK_OK" in check:
        return ""

    logger.warning("@rspack/binding not loadable in sandbox, installing to /tmp")

    # Step 2: Install platform-specific binding to /tmp (always writable)
    install = sandbox.execute_command(
        "set -e && "
        "rm -rf /tmp/rspack-binding && "
        "mkdir -p /tmp/rspack-binding && cd /tmp/rspack-binding && "
        'echo \'{"name":"rspack-fix","private":true}\' > package.json && '
        "ARCH=$(uname -m) && "
        'if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then '
        '  PKG="@rspack/binding-linux-arm64-gnu"; '
        "else "
        '  PKG="@rspack/binding-linux-x64-gnu"; '
        "fi && "
        'echo "Installing $PKG ..." && '
        'npm install --no-audit --no-fund "$PKG" 2>&1 && '
        'echo "INSTALL_DONE"'
    )
    logger.info("rspack /tmp install: %s", install)

    if "INSTALL_DONE" not in install:
        raise RuntimeError(
            f"npm install of @rspack/binding to /tmp failed. Output: {install}. Sandbox may not have internet access. Rebuild sandbox image: docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile ."
        )

    # Step 3: Find the .node file
    find_result = sandbox.execute_command("find /tmp/rspack-binding/node_modules/@rspack -name '*.node' 2>/dev/null")
    native_lib_path = find_result.strip().split("\n")[0].strip()
    if not native_lib_path:
        raise RuntimeError(f"@rspack/binding .node file not found after install. find output: {find_result}")
    logger.info("rspack native lib: %s", native_lib_path)

    # Step 4: Verify — env var AFTER cd &&, not before (shell scoping!)
    verify = sandbox.execute_command(f"cd {_DOCKER_TEMPLATE} && NAPI_RS_NATIVE_LIBRARY_PATH=\"{native_lib_path}\" node -e \"try{{require('@rspack/binding');console.log('RSPACK_OK')}}catch(e){{console.log('FAIL:'+e.message)}}\" 2>&1")
    logger.info("rspack verify with env var: %s", verify)

    if "RSPACK_OK" in verify:
        logger.info("@rspack/binding fixed via NAPI_RS_NATIVE_LIBRARY_PATH=%s", native_lib_path)
        return f'NAPI_RS_NATIVE_LIBRARY_PATH="{native_lib_path}" '

    raise RuntimeError(f"@rspack/binding still fails with NAPI_RS_NATIVE_LIBRARY_PATH={native_lib_path}. Verify output: {verify}")


# ---------------------------------------------------------------------------
# Template deployment
# ---------------------------------------------------------------------------

# Files to copy from template (source_name → sandbox_relative_path)
_TEMPLATE_FILES = [
    ("src/index.ts", "src/index.ts"),
    ("src/index.css", "src/index.css"),
    ("package.json", "package.json"),
    ("tsconfig.json", "tsconfig.json"),
    ("remotion.config.ts", "remotion.config.ts"),
]

# The data-driven composition file (source) → sandbox Composition.tsx
_DATA_DRIVEN_COMPOSITION_SRC = "src/DataDrivenComposition.tsx"
_COMPOSITION_DST = "src/Composition.tsx"


def deploy_template(
    sandbox,
    style: str,
    fps: int,
    width: int,
    height: int,
    total_frames: int,
    template_id: str | None = None,
) -> tuple[str, str, str]:
    """Deploy template to sandbox workspace.

    Files are always written to /mnt/user-data/workspace/ (writable).
    node_modules is symlinked from /opt/remotion-template (Docker) or
    template source dir (local).

    For Docker mode, if @rspack/binding native module is broken, it is
    installed to /tmp and referenced via NAPI_RS_NATIVE_LIBRARY_PATH env var.
    This bypasses node_modules resolution entirely (binding.js checks this
    env var first).

    Returns (composition_id, render_dir, env_prefix) where:
      - render_dir: directory to run npx remotion render from
      - env_prefix: env vars to prepend to render command (e.g. 'NAPI_RS_NATIVE_LIBRARY_PATH=... ')
    """
    template_dir = get_template_path(style, template_id=template_id)
    target = _WORKSPACE
    use_docker = _is_docker_sandbox(sandbox)
    logger.info("deploy_template: target=%s docker_mode=%s", target, use_docker)

    base_template = _get_base_template_path()
    needs_base_overlay = template_dir != base_template
    base_dir = base_template if needs_base_overlay else None

    # 1. Copy scaffolding files to workspace
    for src_rel, dst_rel in _TEMPLATE_FILES:
        src = template_dir / src_rel
        if not src.exists() and base_dir:
            src = base_dir / src_rel
        if src.exists():
            content = src.read_text(encoding="utf-8")
            sandbox.write_file(f"{target}/{dst_rel}", content)

    # 2. Copy DataDrivenComposition.tsx → src/Composition.tsx
    ddc_src = template_dir / _DATA_DRIVEN_COMPOSITION_SRC
    if not ddc_src.exists() and base_dir:
        ddc_src = base_dir / _DATA_DRIVEN_COMPOSITION_SRC
    if ddc_src.exists():
        composition_content = ddc_src.read_text(encoding="utf-8")
        sandbox.write_file(f"{target}/{_COMPOSITION_DST}", composition_content)

    # 3. Generate and write Root.tsx
    root_content = _generate_root_tsx(fps, width, height, total_frames)
    sandbox.write_file(f"{target}/src/Root.tsx", root_content)

    # 4. Ensure node_modules are available
    env_prefix = ""
    if use_docker:
        # Symlink node_modules from pre-built /opt/remotion-template
        sandbox.execute_command(f"ln -sfn {_DOCKER_TEMPLATE}/node_modules {target}/node_modules")
        # Verify/fix @rspack/binding — returns env prefix if fix was needed
        env_prefix = _ensure_rspack_binding(sandbox)
    else:
        # Local dev: symlink node_modules from template source dir
        nm_path = base_template / "node_modules"
        if not nm_path.exists():
            logger.info("One-time npm install in template source: %s", base_template)
            import subprocess

            subprocess.run(["npm", "install", "--prefer-offline"], cwd=str(base_template), check=True, timeout=300)
            # Local dev only: ensure Chrome Headless Shell is cached so the first
            # render does not attempt a download. Docker path uses --browser-executable instead.
            subprocess.run(
                ["npx", "remotion", "browser", "ensure"],
                cwd=str(base_template),
                timeout=300,
                check=False,
            )
        # Quote the path — project dir may contain spaces (e.g. "trainer ai")
        sandbox.execute_command(f'ln -sfn "{nm_path}" {target}/node_modules')

    # 5. Create public/audio directory
    sandbox.execute_command(f"mkdir -p {target}/public/audio")

    return _COMPOSITION_ID, target, env_prefix


# ---------------------------------------------------------------------------
# Audio file upload
# ---------------------------------------------------------------------------


def _is_local_sandbox(sandbox) -> bool:
    """Check if sandbox is a LocalSandbox."""
    from deerflow.sandbox.local.local_sandbox import LocalSandbox

    return isinstance(sandbox, LocalSandbox)


def upload_audio_files(sandbox, audio_dir: Path, workspace: str = _WORKSPACE) -> None:
    """Upload per-frame MP3 files to sandbox public/audio/.

    LocalSandbox: resolve_virtual_path + shutil.copy (direct filesystem access).
    Docker/other sandbox: sandbox.upload_file_stream().
    """
    if not audio_dir.exists():
        return

    mp3_files = sorted(audio_dir.glob("frame_*.mp3"))
    if not mp3_files:
        return

    if _is_local_sandbox(sandbox):
        for mp3 in mp3_files:
            dest = sandbox.resolve_virtual_path(f"{workspace}/public/audio/{mp3.name}")
            Path(dest).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(mp3), dest)
    else:
        for mp3 in mp3_files:
            sandbox.upload_file_stream(
                f"{workspace}/public/audio/{mp3.name}",
                str(mp3),
            )


def upload_bgm(sandbox, bgm_path: str, workspace: str = _WORKSPACE) -> None:
    """Upload BGM file to sandbox public/bgm.mp3."""
    src = Path(bgm_path)
    if not src.exists():
        return

    if _is_local_sandbox(sandbox):
        dest = sandbox.resolve_virtual_path(f"{workspace}/public/bgm.mp3")
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(src), dest)
    else:
        sandbox.upload_file_stream(
            f"{workspace}/public/bgm.mp3",
            str(src),
        )
