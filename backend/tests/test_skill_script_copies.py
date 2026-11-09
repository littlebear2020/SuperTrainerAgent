"""T6（2026-09-08 auto-video-flow 优化计划）：_doubao_tts.py 全仓库副本一致性守护。

背景：_doubao_tts.py 是豆包 TTS 的 stdlib 镜像，按设计在每个沙箱技能的 scripts/
目录各放一份**字节相同**的副本（技能在沙箱内以独立进程运行，无法 import 共享模块，
见 tests/test_harness_boundary.py 的边界约束）。副本漂移曾真实发生（加固只改了其中
一份），导致不同技能行为分叉。

本测试 glob 收集全部副本并断言 md5 一致——不硬编码份数（技能会增删），只要求：
1. 至少发现 2 份（防 glob 模式失效导致的假绿）；
2. auto-video-flow 的基准副本必须存在；
3. 所有副本 md5 相同。

显式排除 backend/.deer-flow/**/workspace/**——那是 agent 运行时用技能时复制出去
的产物（SKILL.md 要求"连同 _doubao_tts.py 一起复制"），不属于守护范围。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CANONICAL = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "scripts" / "_doubao_tts.py"


def _discover_copies() -> list[Path]:
    """Glob-collect all in-repo _doubao_tts.py skill copies (workspace artifacts excluded).

    只扫 skills/public 与 skills/custom 两个技能根——从 repo root 全递归会把
    temp/ 等目录下的同名探针产物误纳入断言。
    """
    copies = []
    for root in (REPO_ROOT / "skills" / "public", REPO_ROOT / "skills" / "custom"):
        if not root.is_dir():
            continue
        for path in root.glob("**/scripts/_doubao_tts.py"):
            parts = path.parts
            if ".deer-flow" in parts:
                continue  # agent 运行时复制产物，不属于守护范围
            if any(part == "node_modules" for part in parts):
                continue
            copies.append(path)
    return sorted(copies)


def test_doubao_tts_copies_exist():
    """glob  sanity：至少 2 份副本 + 基准副本存在（防模式失效假绿）。"""
    copies = _discover_copies()
    assert CANONICAL.exists(), f"基准副本缺失: {CANONICAL}"
    assert len(copies) >= 2, f"只发现 {len(copies)} 份 _doubao_tts.py 副本——glob 模式可能失效: {copies}"


def test_doubao_tts_copies_md5_identical():
    """所有副本必须与基准副本 md5 一致（改动只许全量同步，不许单点漂移）。"""
    copies = _discover_copies()
    assert CANONICAL in copies
    reference_md5 = hashlib.md5(CANONICAL.read_bytes()).hexdigest()

    drifted = []
    for path in copies:
        md5 = hashlib.md5(path.read_bytes()).hexdigest()
        if md5 != reference_md5:
            drifted.append(f"  {path.relative_to(REPO_ROOT)} (md5={md5})")

    assert not drifted, f"_doubao_tts.py 副本漂移（基准 {CANONICAL.relative_to(REPO_ROOT)} md5={reference_md5}），请把基准副本同步到以下文件：\n" + "\n".join(drifted)
