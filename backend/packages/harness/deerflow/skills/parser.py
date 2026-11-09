import logging
import re
from pathlib import Path

import yaml

from .types import SecretRequirement, Skill

logger = logging.getLogger(__name__)

_ENV_VAR_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def parse_required_secrets(raw: object, skill_file: Path) -> tuple[SecretRequirement, ...]:
    """Parse the optional required-secrets frontmatter field (issue #3861).

    Accepts a YAML sequence whose items are either a string (the secret / env
    variable name) or a mapping (``{name, optional}``). Returns an empty tuple
    when the field is omitted. Entries whose name is missing or is not a valid
    environment-variable name are dropped with a warning, so one malformed
    declaration does not invalidate the whole skill. Raises ValueError only when
    the field is present but is not a list.
    """
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ValueError(f"required-secrets in {skill_file} must be a list")

    secrets: list[SecretRequirement] = []
    seen: set[str] = set()
    for item in raw:
        if isinstance(item, str):
            name, optional = item.strip(), False
        elif isinstance(item, dict):
            name = str(item.get("name") or "").strip()
            raw_optional = item.get("optional", False)
            if isinstance(raw_optional, bool):
                optional = raw_optional
            else:
                logger.warning(
                    "Treating non-boolean optional value of type %s for required-secrets entry %r as required in %s",
                    type(raw_optional).__name__,
                    name,
                    skill_file,
                )
                optional = False
        else:
            logger.warning("Ignoring malformed required-secrets entry in %s: %r", skill_file, item)
            continue

        if not _ENV_VAR_NAME_RE.match(name):
            logger.warning("Ignoring required-secrets entry with invalid env var name in %s: %r", skill_file, name)
            continue
        if name in seen:
            continue
        seen.add(name)
        secrets.append(SecretRequirement(name=name, optional=optional))
    return tuple(secrets)


def parse_skill_file(skill_file: Path, category: str, relative_path: Path | None = None) -> Skill | None:
    """Parse a SKILL.md file and extract metadata.

    Args:
        skill_file: Path to the SKILL.md file.
        category: Category of the skill ('public' or 'custom').
        relative_path: Relative path from the category root to the skill
            directory.  Defaults to the skill directory name when omitted.

    Returns:
        Skill object if parsing succeeds, None otherwise.
    """
    if not skill_file.exists() or skill_file.name != "SKILL.md":
        return None

    try:
        content = skill_file.read_text(encoding="utf-8")

        # Extract YAML front-matter block between leading ``---`` fences.
        front_matter_match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
        if not front_matter_match:
            return None

        front_matter_text = front_matter_match.group(1)

        try:
            metadata = yaml.safe_load(front_matter_text)
        except yaml.YAMLError as exc:
            logger.error("Invalid YAML front-matter in %s: %s", skill_file, exc)
            return None

        if not isinstance(metadata, dict):
            logger.error("Front-matter in %s is not a YAML mapping", skill_file)
            return None

        # Extract required fields.  Both must be non-empty strings.
        name = metadata.get("name")
        description = metadata.get("description")

        if not name or not isinstance(name, str):
            return None
        if not description or not isinstance(description, str):
            return None

        # Normalise: strip surrounding whitespace that YAML may preserve.
        name = name.strip()
        description = description.strip()

        if not name or not description:
            return None

        license_text = metadata.get("license")
        if license_text is not None:
            license_text = str(license_text).strip() or None

        created_by = metadata.get("created_by")
        if created_by is not None:
            created_by = str(created_by).strip() or None

        try:
            required_secrets = parse_required_secrets(metadata.get("required-secrets"), skill_file)
        except ValueError as exc:
            logger.error("Invalid required-secrets in %s: %s", skill_file, exc)
            return None

        return Skill(
            name=name,
            description=description,
            license=license_text,
            skill_dir=skill_file.parent,
            skill_file=skill_file,
            relative_path=relative_path or Path(skill_file.parent.name),
            category=category,
            enabled=True,  # Actual state comes from the extensions config file.
            created_by=created_by,
            required_secrets=required_secrets,
        )

    except Exception:
        logger.exception("Unexpected error parsing skill file %s", skill_file)
        return None
