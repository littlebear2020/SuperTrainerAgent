"""TrainerAI image-gen adapter — routes to ppt-master's existing backends by
available TrainerAI provider key.

Policy (SKILL.md override 第 2 条 / TRAINERAI_PATCHES P3): ppt-master image generation MUST reuse
TrainerAI's image providers, NOT its bundled multi-provider .env. This module
maps TrainerAI's available env keys to the right built-in backend +
IMAGE_BACKEND value, so the sandbox uses TrainerAI credentials transparently.

6.3.0 note: the upstream registry now has 15+ per-file backends
(backend_volcengine / backend_qwen / backend_zhipu all still exist), so this
adapter stays a thin selection policy. TrainerAI "auto" policy (P3): when
every candidate key is missing, the degradation endpoint is `manual` (render
the image_prompts.md sidecar and ask the user to supply images) — NEVER the
upstream `host-native` Path B, which assumes a Codex/Claude Code host image
tool that TrainerAI does not have.

Priority (first available key wins; ARK first since the 2026-07-12 ByteDance
Ark integration made seedream/Ark TrainerAI's primary image channel):
  ARK_API_KEY        -> backend_volcengine (Seedream via Ark — primary)
  DASHSCOPE_API_KEY  -> backend_qwen       (DashScope Qwen image)
  ZHIPU_API_KEY      -> backend_zhipu      (Zhipu GLM-Image)
  VOLCENGINE_API_KEY -> backend_volcengine (Volcengine Seedream, legacy key)

(360/qihu image has no ppt-master backend; not wired here.)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# (env key, IMAGE_BACKEND value, backend module name, label)
_CANDIDATES: list[tuple[str, str, str, str]] = [
    ("ARK_API_KEY", "volcengine", "backend_volcengine", "Volcengine Seedream via Ark (TrainerAI primary)"),
    ("DASHSCOPE_API_KEY", "qwen", "backend_qwen", "DashScope Qwen image"),
    ("ZHIPU_API_KEY", "zhipu", "backend_zhipu", "Zhipu GLM-Image"),
    ("VOLCENGINE_API_KEY", "volcengine", "backend_volcengine", "Volcengine Seedream"),
]


def resolve_backend() -> tuple[str, str, str]:
    """Return (IMAGE_BACKEND value, backend module name, label) for the first
    available TrainerAI provider key. Raises RuntimeError if none set."""
    for env_key, backend, module, label in _CANDIDATES:
        if (os.environ.get(env_key) or "").strip():
            logger.info("TrainerAI image backend -> %s (via %s)", backend, env_key)
            return backend, module, label
    raise RuntimeError(
        "No TrainerAI image provider key found. Set one of: "
        + ", ".join(c[0] for c in _CANDIDATES)
        + ". (ppt-master bundled multi-provider .env is NOT used in TrainerAI.)"
    )


if __name__ == "__main__":
    # 供 agent 在不确定用哪个 IMAGE_BACKEND 时直接运行查询（SKILL.md override 第 2 条 / TRAINERAI_PATCHES P3）
    backend, module, label = resolve_backend()
    print(f"IMAGE_BACKEND={backend}  # {label} (module {module})")
