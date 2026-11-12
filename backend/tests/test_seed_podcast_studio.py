import shutil

import yaml

from app.gateway.seed import SEED_DIR, seed_agents


def test_podcast_seed_config_contract():
    agent_dir = SEED_DIR / "podcast-studio"
    config = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))
    assert config["name"] == "podcast-studio"
    assert config["category"] == "public"
    assert config["model"] is None
    assert config["tool_groups"] == ["web", "file:read", "file:write", "bash"]
    assert config["skills"] == ["podcast-studio", "deep-research"]
    assert "video_engine" not in config["tool_groups"]


def test_podcast_soul_routes_four_modes_and_preserves_approval_boundary():
    soul = (SEED_DIR / "podcast-studio" / "SOUL.md").read_text(encoding="utf-8")
    for profile in ["read_original", "news_single", "interview_two", "debate_two"]:
        assert f"[模式: {profile}]" in soul
    assert "原文" in soul and "不改写" in soul
    assert "ask_clarification" in soul
    assert "zh_male_m191_uranus_bigtts" in soul
    assert "zh_female_vv_uranus_bigtts" in soul
    assert "不换" in soul and "引擎" in soul
    assert "不是" in soul and "强审批" in soul
    assert "podcast_studio.py render" in soul
    assert "/mnt/user-data/outputs/" in soul
    assert "present_files" in soul


def test_reseed_six_existing_plus_one_preserves_memory_and_is_idempotent(tmp_path, monkeypatch):
    seed_copy = tmp_path / "seed" / "agents"
    shutil.copytree(SEED_DIR, seed_copy)
    runtime = tmp_path / "runtime" / "agents"
    runtime.mkdir(parents=True)
    old_names = ["co-writer", "course-assistant", "course-video", "creative-studio", "ppt-master", "vimax-video"]
    for name in old_names:
        target = runtime / name
        target.mkdir()
        (target / "config.yaml").write_text(f"name: {name}\n", encoding="utf-8")
        (target / ".seed_version").write_text("2026-09-23", encoding="utf-8")
    (runtime / "co-writer" / "memory.json").write_text('{"facts":["keep"]}', encoding="utf-8")

    monkeypatch.setattr("app.gateway.seed.SEED_DIR", seed_copy)
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: type("P", (), {"agents_dir": runtime})())

    assert seed_agents() == 7
    assert (runtime / "podcast-studio" / "config.yaml").is_file()
    assert (runtime / "co-writer" / "memory.json").read_text(encoding="utf-8") == '{"facts":["keep"]}'
    backups = [path for path in runtime.iterdir() if ".bak." in path.name]
    assert len(backups) == 6
    assert {path.name.split(".bak.")[0] for path in backups} == set(old_names)
    assert seed_agents() == 0
