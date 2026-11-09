"""Skill frontmatter validation utilities.

Pure-logic validation of SKILL.md frontmatter — no FastAPI or HTTP dependencies.
"""

import re
from pathlib import Path

import yaml

# Allowed properties in SKILL.md frontmatter.
#
# "allowed-tools" is deliberately NOT accepted. It reads like a tool-permission
# control, but nothing in the pipeline ever consumed it: skills/types.py has no
# such field and parser.py never reads the key, so a skill declaring it silently
# received the full toolset (including bash) while appearing confined. Rejecting
# the key surfaces that mismatch at validation time instead of granting a false
# sense of restriction. （2026-09-09 前另有 skill-creator/quick_validate.py 的
# 打包侧副本需保持同步；该技能已随技能库清理删除，install 侧为唯一实现。）
# "required-secrets" (P1-S2, 2026-09-30 上游移植, issue #3861) 声明技能需要的
# 请求级密钥名——与 allowed-tools 相反，它有真实消费面：skills/types.py 的
# SecretRequirement / parser.py 的 parse_required_secrets 解析进 Skill.required_secrets，
# 带外注入链（config.context.secrets）按此白名单放行。
ALLOWED_FRONTMATTER_PROPERTIES = {"name", "description", "license", "metadata", "compatibility", "version", "author", "created_by", "required-secrets"}


def _validate_skill_frontmatter(skill_dir: Path) -> tuple[bool, str, str | None]:
    """Validate a skill directory's SKILL.md frontmatter.

    Args:
        skill_dir: Path to the skill directory containing SKILL.md.

    Returns:
        Tuple of (is_valid, message, skill_name).
    """
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return False, "SKILL.md not found", None

    content = skill_md.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return False, "No YAML frontmatter found", None

    # Extract frontmatter
    match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
    if not match:
        return False, "Invalid frontmatter format", None

    frontmatter_text = match.group(1)

    # Parse YAML frontmatter
    try:
        frontmatter = yaml.safe_load(frontmatter_text)
        if not isinstance(frontmatter, dict):
            return False, "Frontmatter must be a YAML dictionary", None
    except yaml.YAMLError as e:
        return False, f"Invalid YAML in frontmatter: {e}", None

    # Check for unexpected properties
    unexpected_keys = set(frontmatter.keys()) - ALLOWED_FRONTMATTER_PROPERTIES
    if unexpected_keys:
        return False, f"Unexpected key(s) in SKILL.md frontmatter: {', '.join(sorted(unexpected_keys))}", None

    # Check required fields
    if "name" not in frontmatter:
        return False, "Missing 'name' in frontmatter", None
    if "description" not in frontmatter:
        return False, "Missing 'description' in frontmatter", None

    # Validate name
    name = frontmatter.get("name", "")
    if not isinstance(name, str):
        return False, f"Name must be a string, got {type(name).__name__}", None
    name = name.strip()
    if not name:
        return False, "Name cannot be empty", None

    # Check naming convention (hyphen-case: lowercase with hyphens)
    if not re.match(r"^[a-z0-9-]+$", name):
        return False, f"Name '{name}' should be hyphen-case (lowercase letters, digits, and hyphens only)", None
    if name.startswith("-") or name.endswith("-") or "--" in name:
        return False, f"Name '{name}' cannot start/end with hyphen or contain consecutive hyphens", None
    if len(name) > 64:
        return False, f"Name is too long ({len(name)} characters). Maximum is 64 characters.", None

    # Validate description
    description = frontmatter.get("description", "")
    if not isinstance(description, str):
        return False, f"Description must be a string, got {type(description).__name__}", None
    description = description.strip()
    if description:
        if "<" in description or ">" in description:
            return False, "Description cannot contain angle brackets (< or >)", None
        if len(description) > 1024:
            return False, f"Description is too long ({len(description)} characters). Maximum is 1024 characters.", None

    return True, "Skill is valid!", name
