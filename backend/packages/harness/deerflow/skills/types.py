from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

SKILL_MD_FILE = "SKILL.md"


class SkillCategory(StrEnum):
    """Source category for a skill.

    P1-S3（2026-09-30 上游移植）：``PUBLIC``/``CUSTOM`` 语义与本仓既有
    ``category: str`` 字段兼容（StrEnum 是 str 子类，存量比较不受影响）；
    ``LEGACY`` = 用户隔离迁移前的全局 custom 技能，只读兜底可见。

    - ``PUBLIC``: built-in skill bundled with the platform, read-only.
    - ``CUSTOM``: user-authored skill that can be edited or deleted.
    - ``INTEGRATION``: managed third-party integration skill, read-only.
    - ``LEGACY``: global custom skill from before user-isolation migration,
      presented as read-only (visible but not editable/deletable). These
      skills are mounted at ``/mnt/skills/legacy/<name>/`` in the sandbox.
    """

    PUBLIC = "public"
    CUSTOM = "custom"
    INTEGRATION = "integration"
    LEGACY = "legacy"


@dataclass(frozen=True)
class SecretRequirement:
    """A request-scoped secret a skill declares it needs (issue #3861).

    ``name`` is both the key looked up in the request's ``context.secrets`` and
    the environment variable name injected into the skill's sandbox subprocess
    when the skill is activated. Declared via the ``required-secrets``
    frontmatter field (P1-S2 port, 2026-09-30).
    """

    name: str
    optional: bool = False


@dataclass
class Skill:
    """Represents a skill with its metadata and file path"""

    name: str
    description: str
    license: str | None
    skill_dir: Path
    skill_file: Path
    relative_path: Path  # Relative path from category root to skill directory
    category: str  # 'public' or 'custom'
    enabled: bool = False  # Whether this skill is enabled
    created_by: str | None = None  # user_id of creator; None for public/legacy skills
    required_secrets: tuple[SecretRequirement, ...] = field(default_factory=tuple)

    @property
    def skill_path(self) -> str:
        """Returns the relative path from the category root (skills/{category}) to this skill's directory"""
        path = self.relative_path.as_posix()
        return "" if path == "." else path

    def get_container_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        Get the full path to this skill in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill directory
        """
        category_base = f"{container_base_path}/{self.category}"
        skill_path = self.skill_path
        if skill_path:
            return f"{category_base}/{skill_path}"
        return category_base

    def get_container_file_path(self, container_base_path: str = "/mnt/skills") -> str:
        """
        Get the full path to this skill's main file (SKILL.md) in the container.

        Args:
            container_base_path: Base path where skills are mounted in the container

        Returns:
            Full container path to the skill's SKILL.md file
        """
        return f"{self.get_container_path(container_base_path)}/SKILL.md"

    def __repr__(self) -> str:
        return f"Skill(name={self.name!r}, description={self.description!r}, category={self.category!r})"
