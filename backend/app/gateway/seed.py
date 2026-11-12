"""Seed pre-built agents from backend/seed/agents/ into the runtime .deer-flow/agents/ directory.

This ensures that agents created by the development team (e.g. course-assistant)
are available on fresh deployments without requiring manual API calls.

Version-stamped (P2-14): each seeded agent dir carries a ``.seed_version`` file
recording the ``SEED_VERSION`` it was written with. On startup, an existing
target whose stamp matches is left untouched; a stale/missing stamp (legacy
deployment, or seed content changed) triggers a reseed — the old dir is backed
up to ``<name>.bak.<ts>`` first so local customizations are never lost.
"""

import logging
import shutil
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SEED_DIR = Path(__file__).resolve().parent.parent.parent / "seed" / "agents"

# Bump when seed content changes (SOUL.md, config.yaml, new agents). Existing
# deployments reseed on the next startup so the new content takes effect.
# 2026-09-02: SEC-C2/C3 batch B2 — six runtime-only agents (card-poster/
# case-article/course-assistant/course-ppt/knowledge-extraction/video-clip，
# 2026-09-09 前的旧清单) brought under seed management with explicit
# sandbox-trio tool_groups; vimax-video narrowed to [video_engine].
# 2026-09-09: 创意工作合并——card-poster/case-article/design-specialist/
# knowledge-extraction/video-clip 五壳退役，新建 creative-studio（模式路由）；
# co-writer/course-video 先后加入 seed。注意：seed 只增不删——退役壳的运行时
# 目录不会随 reseed 消失，需手动清理（见计划文档第十节部署注意 2）。
# 2026-09-11: ppt-master 6.3.0 升级——course-ppt 退役由 ppt-master 承接；
# 现行 seed/agents = co-writer/course-assistant/course-video/creative-studio/
# ppt-master/vimax-video 六个。
# 2026-09-12: 缺陷批条目 26——course-assistant SOUL 路由表 course-ppt →
# ppt-master（course-ppt 实体 09-11 已删，运行态副本陈旧须 reseed）。
# 2026-09-22: course-assistant SOUL 工作原则新增两条——续作盘点（续作时先读
# 系统注入的 <workspace_files> 块盘点已有产物，复用而非重做）与确认阶段最小
# 侦察（用户确认前不做 OCR/抽帧/全量解析/试合成等重操作）。
# 2026-09-23: 稳定性批 D2——course-assistant 委派表述改为 general-purpose
# （自定义子代理 researcher/video-processor/slide-builder 退役：技能白名单被
# 父级交集截断半坏 + researcher 工具白名单缺检索；SOUL 新副本须 reseed 生效，
# memory.json 由 _RUNTIME_PRESERVED_FILES 保护）。
# 2026-11-12: 新增 podcast-studio（四模式来源/确认/固定音色 MP3）；标准全量
# reseed 保留六个旧 Agent 备份与既有 memory.json，旧 Agent seed 内容不变。
SEED_VERSION = "2026-11-12"

_SEED_VERSION_FILENAME = ".seed_version"

# Runtime-accumulated data that must survive a reseed (the docstring promise
# "local customizations are never lost" — moving the old dir to .bak archives
# them, but agent memory must stay ACTIVE or every version bump resets it).
_RUNTIME_PRESERVED_FILES = ("memory.json",)


def _runtime_seed_version(target: Path) -> str | None:
    stamp = target / _SEED_VERSION_FILENAME
    if not stamp.exists():
        return None
    return stamp.read_text(encoding="utf-8").strip()


def seed_agents() -> int:
    """Copy seed agent directories into the runtime agents directory.

    Returns the number of agents seeded/reseeded (0 when all are up to date).
    """
    if not SEED_DIR.is_dir():
        logger.debug("No seed/agents/ directory found, skipping seed")
        return 0

    from deerflow.config.paths import get_paths

    agents_dir = get_paths().agents_dir
    seeded = 0

    for seed_entry in sorted(SEED_DIR.iterdir()):
        if not seed_entry.is_dir():
            continue
        if not (seed_entry / "config.yaml").exists():
            continue

        target = agents_dir / seed_entry.name
        existing_version = _runtime_seed_version(target)

        if existing_version == SEED_VERSION:
            logger.debug(
                "Agent '%s' up to date (%s), skipping seed",
                seed_entry.name,
                SEED_VERSION,
            )
            continue

        # Version mismatch (or new target, or legacy dir without a stamp) →
        # (re)seed. Back up an existing dir first so local customizations survive.
        backup: Path | None = None
        if target.exists():
            backup = target.with_name(f"{target.name}.bak.{int(time.time())}")
            shutil.move(str(target), str(backup))
            logger.info(
                "Agent '%s' version %s → %s; backed up old dir to %s",
                seed_entry.name,
                existing_version,
                SEED_VERSION,
                backup,
            )

        shutil.copytree(seed_entry, target)
        # Carry runtime-accumulated data (agent global memory) across the
        # reseed — the backup keeps an archive, but the live agent must not
        # silently forget everything on a seed content bump. Only meaningful
        # when the target existed (a reseed, not a first-time seed).
        if backup is not None:
            for filename in _RUNTIME_PRESERVED_FILES:
                runtime_file = backup / filename
                if runtime_file.exists():
                    shutil.copy2(runtime_file, target / filename)
                    logger.info("Preserved runtime %s for agent '%s' across reseed", filename, seed_entry.name)
        (target / _SEED_VERSION_FILENAME).write_text(SEED_VERSION, encoding="utf-8")
        seeded += 1
        logger.info("Seeded agent '%s' → %s", seed_entry.name, target)

    return seeded
