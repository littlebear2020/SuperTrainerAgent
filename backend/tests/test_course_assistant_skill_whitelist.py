"""Guard the course-assistant skill whitelist (2026-08-13 regression fix, T3/C-05).

The runtime agent config lives in ``backend/.deer-flow/agents/course-assistant/config.yaml``
(gitignored runtime data; server-side edit documented in
``docs/company-server-deployment-guide.md`` K13). With ``skills: null`` the loader
injects ALL 121 skills (~43KB prompt) and the course-assistant planning stage
stalls. The canonical whitelist is mirrored here so a deleted/renamed skill
cannot silently turn a whitelist entry into a dead no-op (the loader drops
unknown names without error).
"""

from __future__ import annotations

from pathlib import Path

import yaml

# Canonical whitelist — must stay in sync with:
#   * backend/.deer-flow/agents/course-assistant/config.yaml (runtime)
#   * docs/company-server-deployment-guide.md K13 (server-side edit)
# Mapping (SOUL workflow -> skill):
#   course-video -> auto-video-flow + video-generation
#   course-ppt   -> ppt-master
#   creative-studio -> course-poster + knowledge-card-auto-generator + video-clip
#   （2026-09-09 五壳合并：原 card-poster/knowledge-extraction/video-clip/case-article
#   四条工作流改指 creative-studio；白名单本身未动——6 技能全部仍存活）
COURSE_ASSISTANT_SKILLS = [
    "auto-video-flow",
    "video-generation",
    "ppt-master",
    "course-poster",
    "knowledge-card-auto-generator",
    "video-clip",
]


def _load_all_skill_names() -> set[str]:
    from deerflow.skills.loader import load_skills

    return {skill.name for skill in load_skills()}


def test_every_whitelist_entry_has_a_skill():
    """Each whitelist name must resolve to an existing SKILL.md frontmatter name."""
    names = _load_all_skill_names()
    missing = [name for name in COURSE_ASSISTANT_SKILLS if name not in names]
    assert not missing, f"course-assistant whitelist has dead entries: {missing}"


def test_prompt_section_injects_only_the_whitelist():
    from deerflow.agents.lead_agent.prompt import get_skills_prompt_section, warm_enabled_skills_cache

    assert warm_enabled_skills_cache(), "enabled skills cache did not warm up in time"
    section = get_skills_prompt_section(set(COURSE_ASSISTANT_SKILLS))
    for name in COURSE_ASSISTANT_SKILLS:
        assert name in section
    # Spot-check that unrelated skills are not injected (121 -> 6 fix).
    for outsider in ("deep-research", "od-brandkit", "consulting-analysis"):
        assert outsider not in section


def test_runtime_config_matches_canonical_whitelist():
    """When the local runtime config exists, it must equal the canonical list."""
    runtime_config = Path(__file__).resolve().parent.parent / ".deer-flow" / "agents" / "course-assistant" / "config.yaml"
    if not runtime_config.exists():
        import pytest

        pytest.skip("runtime agent config not present (gitignored local data)")
    cfg = yaml.safe_load(runtime_config.read_text(encoding="utf-8"))
    assert cfg["skills"] == COURSE_ASSISTANT_SKILLS
