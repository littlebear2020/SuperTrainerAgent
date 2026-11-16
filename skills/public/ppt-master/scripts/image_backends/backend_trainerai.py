"""TrainerAI image-gen adapter — routes to 360 Seedream by default.

Policy (SKILL.md override 第 2 条 / TRAINERAI_PATCHES P3): ppt-master image
generation MUST reuse TrainerAI's image channel, NOT its bundled multi-provider
.env. Since 2026-11-16 the only TrainerAI image channel is 360 Seedream
(``bytedance/doubao-seedream-5.0-pro`` via ``IMAGE_BACKEND=qihu360``), which
also supports reference-image edits. Other upstream backends stay in the
vendored tree but are not selected here.

Degradation endpoint stays `manual` (render the image_prompts.md sidecar and ask
the user to supply images) — NEVER the upstream `host-native` Path B, which
assumes a Codex/Claude Code host image tool that TrainerAI does not have.

Priority: only ``QIHU_API_KEY`` participates.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# (env key, IMAGE_BACKEND value, backend module name, label)
_CANDIDATES: list[tuple[str, str, str, str]] = [
    ("QIHU_API_KEY", "qihu360", "backend_qihu360", "360 Seedream (TrainerAI primary)"),
]


def resolve_backend() -> tuple[str, str, str]:
    """Return (IMAGE_BACKEND value, backend module name, label) for QIHU."""
    for env_key, backend, module, label in _CANDIDATES:
        if (os.getenv(env_key) or "").strip():
            logger.info("TrainerAI image backend -> %s (via %s)", backend, env_key)
            return backend, module, label
    raise RuntimeError(
        "No TrainerAI image provider key found. Set QIHU_API_KEY. "
        "(ppt-master bundled multi-provider .env is NOT used in TrainerAI.)"
    )


if __name__ == "__main__":
    # 供 agent 在不确定用哪个 IMAGE_BACKEND 时直接运行查询（SKILL.md override 第 2 条 / TRAINERAI_PATCHES P3）
    backend, module, label = resolve_backend()
    print(f"IMAGE_BACKEND={backend}  # {label} (module {module})")
