"""migrate_retired_agent_memory 混合 target 迁移测试（2026-09-11 review 修复）。

脚本不在包内，按 conftest provisioner_module 同款 importlib 方式加载。

核心回归（review 问题 2）：同一 user 同时持有 creative 壳记忆与 course-ppt
记忆时，单次 run_migration 必须把两组都合并+归档——修前循环头对每个 user 只
取 srcs[0] 的 target，其余 target 的源被过滤排除，既不合并也不归档，重跑才
自愈，而部署 runbook 是一次性执行，漏迁组在 reconciliation 报告里无提示。
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "migrate_retired_agent_memory.py"
_SPEC = importlib.util.spec_from_file_location("migrate_retired_agent_memory_under_test", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
mrm = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mrm)


def _write_memory(path: Path, fact_ids: list[str], last_updated: str = "2026-01-01T00:00:00Z") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "lastUpdated": last_updated,
        "facts": [{"id": fid, "content": f"{fid} 内容", "category": "preference", "confidence": 0.9} for fid in fact_ids],
        "user": {},
        "history": {},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _load_facts(path: Path) -> set[str]:
    return {f["id"] for f in json.loads(path.read_text(encoding="utf-8"))["facts"]}


def test_mixed_target_user_migrates_both_pairs_in_one_run(tmp_path):
    agents = tmp_path / "users" / "u1" / "agents"
    _write_memory(agents / "card-poster" / "memory.json", ["f_poster"])
    _write_memory(agents / "video-clip" / "memory.json", ["f_clip"])
    _write_memory(agents / "course-ppt" / "memory.json", ["f_ppt"])

    sources = mrm.discover_sources(tmp_path)
    for s in sources:
        s.load()
    assert mrm.run_migration(tmp_path, sources) == 0

    # 两组 target 各自完成合并（修前 course-ppt 组本轮被排除）
    assert _load_facts(agents / "creative-studio" / "memory.json") == {"f_poster", "f_clip"}
    assert _load_facts(agents / "ppt-master" / "memory.json") == {"f_ppt"}

    # 两组源各自归档（per-pair 后缀）；未归档源会被重跑重复合并，归档是完成凭证
    assert (agents / "card-poster" / "memory.json.pre-creative-merge.bak").is_file()
    assert (agents / "video-clip" / "memory.json.pre-creative-merge.bak").is_file()
    assert (agents / "course-ppt" / "memory.json.pre-course-ppt-retire.bak").is_file()

    # 重跑幂等：全部源已归档 → 跳过，目标文件不再变化
    sources2 = mrm.discover_sources(tmp_path)
    for s in sources2:
        s.load()
    assert mrm.run_migration(tmp_path, sources2) == 0
    assert _load_facts(agents / "creative-studio" / "memory.json") == {"f_poster", "f_clip"}
    assert _load_facts(agents / "ppt-master" / "memory.json") == {"f_ppt"}


def test_mixed_target_multi_user_report_counts_all_pairs(tmp_path, capsys):
    for user in ("u1", "u2"):
        agents = tmp_path / "users" / user / "agents"
        _write_memory(agents / "card-poster" / "memory.json", [f"{user}_poster"])
        _write_memory(agents / "course-ppt" / "memory.json", [f"{user}_ppt"])

    sources = mrm.discover_sources(tmp_path)
    for s in sources:
        s.load()
    assert mrm.run_migration(tmp_path, sources) == 0

    out = capsys.readouterr().out
    # 每组 (user, target) 一条报告：2 用户 × 2 target = 4 条
    assert "target files written: 4" in out
    # 两个 target 路径都出现在报告里（修前 course-ppt 组完全不可见）
    assert "creative-studio/memory.json" in out
    assert "ppt-master/memory.json" in out
    # 全部 4 个源都归档
    assert "archives created this run: 4" in out
