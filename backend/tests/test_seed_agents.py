"""Tests for the agent seeding mechanism."""

from __future__ import annotations

from pathlib import Path

from app.gateway.seed import SEED_DIR, SEED_VERSION, seed_agents


def test_seed_dir_exists():
    """The seed directory must exist with at least one agent."""
    assert SEED_DIR.is_dir(), f"Seed directory not found: {SEED_DIR}"
    agents = [d for d in SEED_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists()]
    assert len(agents) > 0, "No seed agents found"


def test_seed_agents_copies_when_target_missing(tmp_path: Path, monkeypatch):
    """seed_agents copies agent dirs that don't exist in the target."""
    # Create a fake seed dir with one agent
    fake_seed = tmp_path / "seed" / "agents" / "test-agent"
    fake_seed.mkdir(parents=True)
    (fake_seed / "config.yaml").write_text("name: test-agent\ndescription: test\n", encoding="utf-8")
    (fake_seed / "SOUL.md").write_text("# Test Agent\n", encoding="utf-8")

    # Create a fake agents target dir
    agents_target = tmp_path / "agents"
    agents_target.mkdir()

    # Monkey-patch SEED_DIR and get_paths
    monkeypatch.setattr("app.gateway.seed.SEED_DIR", fake_seed.parent)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"agents_dir": agents_target})(),
    )

    seeded = seed_agents()
    assert seeded == 1
    assert (agents_target / "test-agent" / "config.yaml").exists()
    assert (agents_target / "test-agent" / "SOUL.md").exists()
    # P2-14: a version stamp is written so future seed content changes reseed.
    assert (agents_target / "test-agent" / ".seed_version").read_text(encoding="utf-8").strip() == SEED_VERSION


def test_seed_agents_skips_when_version_matches(tmp_path: Path, monkeypatch):
    """A target whose .seed_version matches SEED_VERSION is left untouched."""
    fake_seed = tmp_path / "seed" / "agents" / "test-agent"
    fake_seed.mkdir(parents=True)
    (fake_seed / "config.yaml").write_text("name: test-agent\ndescription: new\n", encoding="utf-8")

    agents_target = tmp_path / "agents" / "test-agent"
    agents_target.mkdir(parents=True)
    (agents_target / "config.yaml").write_text("name: test-agent\ndescription: old\n", encoding="utf-8")
    # Stamp the existing target with the current version → must skip.
    (agents_target / ".seed_version").write_text(SEED_VERSION, encoding="utf-8")

    monkeypatch.setattr("app.gateway.seed.SEED_DIR", fake_seed.parent)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"agents_dir": agents_target.parent})(),
    )

    seeded = seed_agents()
    assert seeded == 0
    # Original file untouched
    assert "old" in (agents_target / "config.yaml").read_text()


def test_seed_agents_reseeds_on_version_mismatch(tmp_path: Path, monkeypatch):
    """P2-14: a target whose stamp is stale is reseeded (old backed up)."""
    fake_seed = tmp_path / "seed" / "agents" / "test-agent"
    fake_seed.mkdir(parents=True)
    (fake_seed / "config.yaml").write_text("name: test-agent\ndescription: new\n", encoding="utf-8")
    (fake_seed / "SOUL.md").write_text("# new SOUL\n", encoding="utf-8")

    agents_target = tmp_path / "agents" / "test-agent"
    agents_target.mkdir(parents=True)
    (agents_target / "config.yaml").write_text("name: test-agent\ndescription: old\n", encoding="utf-8")
    (agents_target / "SOUL.md").write_text("# OLD SOUL\n", encoding="utf-8")
    (agents_target / ".seed_version").write_text("0000-00-00", encoding="utf-8")  # stale

    monkeypatch.setattr("app.gateway.seed.SEED_DIR", fake_seed.parent)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"agents_dir": agents_target.parent})(),
    )

    seeded = seed_agents()
    assert seeded == 1
    # New content took effect
    assert (agents_target / "SOUL.md").read_text(encoding="utf-8") == "# new SOUL\n"
    assert (agents_target / ".seed_version").read_text(encoding="utf-8").strip() == SEED_VERSION
    # Old dir was backed up (preserves any local customization)
    backups = list(agents_target.parent.glob("test-agent.bak.*"))
    assert len(backups) == 1
    assert (backups[0] / "SOUL.md").read_text(encoding="utf-8") == "# OLD SOUL\n"


def test_seed_agents_reseeds_legacy_dir_without_stamp(tmp_path: Path, monkeypatch):
    """P2-14: an existing target with no .seed_version (legacy) is reseeded."""
    fake_seed = tmp_path / "seed" / "agents" / "test-agent"
    fake_seed.mkdir(parents=True)
    (fake_seed / "config.yaml").write_text("name: test-agent\ndescription: new\n", encoding="utf-8")

    agents_target = tmp_path / "agents" / "test-agent"
    agents_target.mkdir(parents=True)
    (agents_target / "config.yaml").write_text("name: test-agent\ndescription: legacy\n", encoding="utf-8")
    # No .seed_version → legacy deployment that predates the stamp.

    monkeypatch.setattr("app.gateway.seed.SEED_DIR", fake_seed.parent)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"agents_dir": agents_target.parent})(),
    )

    seeded = seed_agents()
    assert seeded == 1  # legacy dir reseeded so new SOUL/config take effect
    assert (agents_target / ".seed_version").exists()


# ── 2026-07-15 Remotion/ViMax agent split ─────────────────────────────────────


def test_course_video_and_vimax_video_are_seeded():
    """2026-07-15 split: course-video (remotion direct-write) + vimax-video are seed
    agents, so they auto-deploy via seed_agents() and surface on /workspace/agents."""
    agent_names = {d.name for d in SEED_DIR.iterdir() if d.is_dir() and (d / "config.yaml").exists()}
    assert "course-video" in agent_names, "course-video seed agent missing"
    assert "vimax-video" in agent_names, "vimax-video seed agent missing"


def test_course_video_seed_soul_is_direct_write_and_scrubbed():
    """course-video regressed to direct-write (auto-video-flow); SOUL scrubbed of
    stock_clip/Pexels (removed 2026-07-08) and carries the target_duration rule (D1:
    runtime SOUL was missing it). It must NOT instruct the backend video tools
    (plan_storyboard/generate_video are vimax-video's tools now)."""
    soul = (SEED_DIR / "course-video" / "SOUL.md").read_text(encoding="utf-8")
    assert "stock_clip" not in soul
    assert "Pexels" not in soul
    assert "target_duration" in soul
    assert "auto-video-flow" in soul
    assert "plan_storyboard" not in soul
    assert "generate_video" not in soul


def test_vimax_video_seed_uses_backend_engine_tools():
    """vimax-video is the ViMax agent: routes through plan_storyboard→generate_video
    (backend VimaxEngine). category:public so it surfaces on /workspace/agents."""
    import yaml

    cfg = yaml.safe_load((SEED_DIR / "vimax-video" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["name"] == "vimax-video"
    assert cfg.get("category") == "public"
    soul = (SEED_DIR / "vimax-video" / "SOUL.md").read_text(encoding="utf-8")
    assert "plan_storyboard" in soul
    assert "generate_video" in soul


def test_course_video_seed_excludes_video_engine_tool_group():
    """B3: course-video (direct-write) must NOT mount the video_engine tool group
    (generate_video/plan_storyboard/check_video_status are vimax-video's tools).
    Hard exclusion via a tool_groups allowlist — defense-in-depth on top of the SOUL
    guidance, so a stochastic LLM cannot route a remotion request through the backend
    video tools (which would hit the removed engine path / legacy template path)."""
    import yaml

    cfg = yaml.safe_load((SEED_DIR / "course-video" / "config.yaml").read_text(encoding="utf-8"))
    tg = cfg.get("tool_groups")
    assert tg is not None, "course-video must pin tool_groups (not null=all) to exclude video_engine"
    assert "video_engine" not in tg
    # still has the tools auto-video-flow direct-write needs (bash + file + web for deep-research)
    for needed in ("bash", "file:read", "file:write"):
        assert needed in tg, f"course-video tool_groups missing {needed} (needed for direct-write)"


def test_vimax_video_seed_retains_video_engine_tool_group():
    """B3: vimax-video keeps the video_engine tools (plan_storyboard/generate_video/
    check_video_status) — tool_groups: null (all) or explicitly including video_engine."""
    import yaml

    cfg = yaml.safe_load((SEED_DIR / "vimax-video" / "config.yaml").read_text(encoding="utf-8"))
    tg = cfg.get("tool_groups")
    assert tg is None or "video_engine" in tg


# ── 2026-07-21 vimax 对话流修复（确认门 + 职责收敛 + todo 纪律）───────────────


def test_vimax_video_soul_requires_ask_clarification_confirmation_gate():
    """2026-07-21: 蓝图确认门必须走 ask_clarification——它是唯一被 ClarificationMiddleware
    拦截并 goto=END 的"暂停等用户"通道。旧 SOUL 只要求 present_files 展示+纯文本等待，
    纯文本回复（无 tool_calls）+ 未完成 todo 会被 TodoMiddleware 防退出逻辑打回
    （jump_to model + "only respond when all items are done"），逼 agent 跳过用户确认
    直接调 generate_video。SOUL 必须明文：展示蓝图后用 ask_clarification 请求确认。"""
    soul = (SEED_DIR / "vimax-video" / "SOUL.md").read_text(encoding="utf-8")
    assert "ask_clarification" in soul, "SOUL must route the confirmation gate through ask_clarification"
    # 确认门措辞必须与"蓝图确认"绑定：ask_clarification 与"蓝图"须出现在邻近语境
    # （旧 SOUL 的 ask_clarification 只用于收集旋钮和泛化的"每步确认"，蓝图确认步骤
    # 走的是 present_files+纯文本等待，同行无 ask_clarification 与"蓝图"共现）
    import re

    assert re.search(r"ask_clarification[^\n]{0,40}蓝图|蓝图[^\n]{0,40}ask_clarification", soul), "SOUL must require ask_clarification for the blueprint confirmation gate specifically"


def test_vimax_video_soul_converges_duties_and_ends_turn_after_submit():
    """2026-07-21 职责收敛：规划→确认→提交→(被问时)查状态→(完成后)展示成片。
    generate_video 是 fire-and-forget，进度由前端 SSE 展示；SOUL 必须明文 agent
    提交渲染后结束本轮、不主动轮询——否则 agent 会写"监控进度直至完成"这类当轮
    物理上不可能完成的 todo（渲染 15-30 分钟），导致 completion reminder 刷屏 +
    逼 agent 反复轮询 check_video_status。"""
    soul = (SEED_DIR / "vimax-video" / "SOUL.md").read_text(encoding="utf-8")
    assert "check_video_status" in soul, "SOUL must spell out when check_video_status may be used"
    assert "write_todos" in soul, "SOUL must spell out the cross-turn todo discipline"


def test_vimax_video_soul_presents_blueprint_content_not_counts():
    """2026-07-22 确认门黑盒修复：SOUL 步骤 3 必须要求把 plan_storyboard 返回的
    blueprint/story 内容贴给用户（旧文案'present_files 展示分镜蓝图'不可执行——
    storyboard.json 在 AioSandbox 外，present_files 硬拒沙箱外路径）。"""
    soul = (SEED_DIR / "vimax-video" / "SOUL.md").read_text(encoding="utf-8")
    assert "blueprint" in soul
    assert "present_files 展示分镜蓝图" not in soul


def test_non_video_seed_agents_do_not_mount_video_engine_tools():
    """2026-07-22 爆炸半径收窄：tool_groups: null = 挂载全部 config 工具
    （packages/harness/deerflow/tools/tools.py:59），导致 co-writer/design-specialist/ppt-master 都挂上了
    plan_storyboard/generate_video/check_video_status——vimax 专属确认门纪律会
    steer 无关 agent，且任何 agent 都能触发渲染。这些 agent 必须显式列出
    不含 video_engine 的 tool_groups（与 course-video 同款）。
    2026-09-09：design-specialist 退役（合并入 creative-studio），守护集换成
    creative-studio。"""
    import yaml

    for agent in ("co-writer", "ppt-master", "creative-studio"):
        cfg = yaml.safe_load((SEED_DIR / agent / "config.yaml").read_text(encoding="utf-8"))
        tg = cfg.get("tool_groups")
        assert tg is not None, f"{agent}: tool_groups null 会挂载全部工具（含 video_engine）"
        assert "video_engine" not in tg, f"{agent}: must not mount video_engine"


# ---------------------------------------------------------------------------
# SEC-C2/C3 (security plan 2026-09-02 B2 step 3): null tool_groups 显式化
# ---------------------------------------------------------------------------

# 2026-09-11 course-ppt 完全退役（ppt-master 6.3.0 批次，quick-generate E2E 过门后）
SEC_NULL_EXPLICIT_AGENTS = ("course-assistant",)


def test_all_seed_agents_declare_explicit_tool_groups():
    """No seed agent may ship tool_groups: null — null means "mount every
    config tool" (incl. bash / video_engine), which is the SEC-C3 surface."""
    import yaml

    for agent_dir in sorted(p for p in SEED_DIR.iterdir() if (p / "config.yaml").exists()):
        cfg = yaml.safe_load((agent_dir / "config.yaml").read_text(encoding="utf-8"))
        assert cfg.get("tool_groups") is not None, f"{agent_dir.name}: tool_groups null = 挂载全部工具（SEC-C3 面）"


def test_vimax_video_mounts_video_engine_only():
    """vimax-video 依赖 video_engine 组（generate_video/plan_storyboard/
    check_video_status），且 SOUL 明文不用沙箱文件工具——显式组恰为
    ['video_engine']，不再隐式携带 bash/file:write。"""
    import yaml

    cfg = yaml.safe_load((SEED_DIR / "vimax-video" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["tool_groups"] == ["video_engine"]


def test_skill_seed_agents_mount_sandbox_trio_only():
    """技能执行型 agent（2026-09-11 course-ppt 退役后仅剩 course-assistant）的
    技能全部经沙箱脚本执行（HTML/canvas/docx/ffmpeg——config 的 web 组当前为
    空），显式组 = ['file:read', 'file:write', 'bash']，不携带 video_engine；
    creative-studio 因含 web 组由 test_creative_studio_pins_merge_contract 单独钉。"""
    import yaml

    for agent in SEC_NULL_EXPLICIT_AGENTS:
        cfg = yaml.safe_load((SEED_DIR / agent / "config.yaml").read_text(encoding="utf-8"))
        assert cfg.get("tool_groups") == ["file:read", "file:write", "bash"], f"{agent}: expected sandbox trio, got {cfg.get('tool_groups')}"
        assert (SEED_DIR / agent / "SOUL.md").exists(), f"{agent}: SOUL.md missing from seed"


# 2026-09-09 创意工作合并：钉死 creative-studio 的合并契约（计划 2.1 表），防漂移
CREATIVE_STUDIO_SKILLS = [
    "trainerai-imagine",
    "trainerai-diagram",
    "trainerai-image-cards",
    "trainerai-infographic",
    "trainerai-comic",
    "trainerai-article-visuals",
    "knowledge-card-auto-generator",
    "course-poster",
    "canvas-design",
    "deep-research",
    "data-analysis",
    "consulting-analysis",
    "chart-visualization",
    "docx",
    "pdf",
    "xlsx",
    "video-clip",
    "video-dl",
    "video-frames",
]


def test_creative_studio_pins_merge_contract():
    """creative-studio = 五壳合并产物：tool_groups = web + 沙箱三件套（并集，
    无 video_engine），skills = 计划 2.1 的 19 个白名单。"""
    import yaml

    cfg = yaml.safe_load((SEED_DIR / "creative-studio" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg.get("tool_groups") == ["web", "file:read", "file:write", "bash"]
    assert cfg.get("skills") == CREATIVE_STUDIO_SKILLS
    assert (SEED_DIR / "creative-studio" / "SOUL.md").exists()


def test_reseed_preserves_runtime_memory_json(tmp_path: Path, monkeypatch):
    """Reseed replaces the agent dir, but the runtime-accumulated global
    memory.json (agent_memory_file) must survive into the fresh copy —
    otherwise every SEED_VERSION bump silently resets agent memory."""
    fake_seed = tmp_path / "seed" / "agents" / "mem-agent"
    fake_seed.mkdir(parents=True)
    (fake_seed / "config.yaml").write_text("name: mem-agent\ndescription: test\n", encoding="utf-8")
    (fake_seed / "SOUL.md").write_text("# Test\n", encoding="utf-8")

    agents_target = tmp_path / "agents" / "mem-agent"
    agents_target.mkdir(parents=True)
    (agents_target / "config.yaml").write_text("name: mem-agent\ndescription: old\n", encoding="utf-8")
    (agents_target / ".seed_version").write_text("stale-version", encoding="utf-8")
    (agents_target / "memory.json").write_text('{"facts": ["learned"]}', encoding="utf-8")

    monkeypatch.setattr("app.gateway.seed.SEED_DIR", fake_seed.parent)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"agents_dir": agents_target.parent})(),
    )

    seeded = seed_agents()

    assert seeded == 1
    assert (agents_target / "config.yaml").read_text(encoding="utf-8") == "name: mem-agent\ndescription: test\n"
    assert (agents_target / "memory.json").read_text(encoding="utf-8") == '{"facts": ["learned"]}'
