"""ppt-master seed agent 结构与播种行为测试（恢复计划 Task 8）。"""

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = REPO_ROOT / "backend" / "seed" / "agents" / "ppt-master"


def test_seed_dir_structure():
    assert (SEED_DIR / "SOUL.md").is_file()
    assert (SEED_DIR / "config.yaml").is_file()


def test_seed_config_fields():
    cfg = yaml.safe_load((SEED_DIR / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["name"] == "ppt-master"
    assert cfg["category"] == "public"
    assert cfg["skills"] == ["ppt-master"]


def test_referenced_skills_exist():
    cfg = yaml.safe_load((SEED_DIR / "config.yaml").read_text(encoding="utf-8"))
    for skill in cfg["skills"]:
        assert (REPO_ROOT / "skills" / "public" / skill / "SKILL.md").is_file(), f"skill {skill} 缺失"


def test_soul_references_vendored_skill():
    soul = (SEED_DIR / "SOUL.md").read_text(encoding="utf-8")
    assert "skills/public/ppt-master/SKILL.md" in soul
    assert "data-element-id" in soul  # D3 铁律 #6 仍在


def test_soul_aligned_to_630_semantics():
    """2026-09-11 升级 6.3.0：SOUL 必须对齐路由制/两段式确认/新禁用项。"""
    soul = (SEED_DIR / "SOUL.md").read_text(encoding="utf-8")
    # 6.3.0 版本声明 + 路由制
    assert "6.3.0" in soul
    assert "routing.md" in soul
    # 两段式确认取代八项确认
    assert "Stage1" in soul and "Stage2" in soul
    assert "八项" not in soul
    # 6 条 D3 铁律语义仍在（关键 token）
    assert "逐页手写" in soul and "spec_lock" in soul
    assert "禁子代理" in soul or "禁止" in soul and "子代理" in soul
    # P11/P12/P13 禁用面
    assert "confirm_ui" in soul
    assert "svg_editor" in soul
    assert "edge-tts" in soul
    # 每 5 页重读锚定（6.3.0 新纪律）
    assert "每 5 页" in soul


def test_seed_agents_plants_new_agent_without_version_bump(tmp_path, monkeypatch):
    """新 agent（运行时目录缺失或无邮票）被播种；其他 agent 不受影响。"""
    import app.gateway.seed as seed_mod

    agents_dir = tmp_path / "agents"
    agents_dir.mkdir()

    class _Paths:
        pass

    paths = _Paths()
    paths.agents_dir = agents_dir
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)

    seeded = seed_mod.seed_agents()
    assert seeded >= 1
    target = agents_dir / "ppt-master"
    assert (target / "SOUL.md").is_file()
    assert (target / ".seed_version").read_text().strip() == seed_mod.SEED_VERSION


def test_seed_agents_backs_up_existing_dir(tmp_path, monkeypatch):
    """已有运行时目录（如本地遗留）→ 备份为 .bak 再播种，memory.json 不丢。"""
    import app.gateway.seed as seed_mod

    agents_dir = tmp_path / "agents"
    legacy = agents_dir / "ppt-master"
    legacy.mkdir(parents=True)
    (legacy / "memory.json").write_text("{}", encoding="utf-8")

    class _Paths:
        pass

    paths = _Paths()
    paths.agents_dir = agents_dir
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)

    seed_mod.seed_agents()
    backups = list(agents_dir.glob("ppt-master.bak.*"))
    assert len(backups) == 1
    assert (backups[0] / "memory.json").is_file()  # 旧记忆保留在备份里
