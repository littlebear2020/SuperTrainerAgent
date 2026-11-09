"""Template registry — scan templates/ directories, load template.json metadata."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class TemplateParam(BaseModel):
    """A single template parameter definition (drives frontend form rendering)."""

    name: str
    type: str  # text, color, boolean, enum, number, image
    label: str = ""
    default: Any = None
    required: bool = False
    # number type
    min: float | None = None
    max: float | None = None
    # enum type
    options: list[str] | None = None


class TemplateMeta(BaseModel):
    """Metadata for a Remotion template, loaded from template.json."""

    id: str
    name: str
    desc: str = ""
    category: str = "general"
    tags: list[str] = []
    preview: str = ""
    supports_video_types: list[str] = ["remotion"]
    params: list[TemplateParam] = []


def _get_templates_root() -> Path:
    """Resolve templates root directory from skills path."""
    from deerflow.config import get_app_config

    config = get_app_config()
    return config.skills.get_skills_path() / "public" / "auto-video-flow" / "templates"


def list_templates() -> list[TemplateMeta]:
    """Scan templates directory and return list of TemplateMeta."""
    root = _get_templates_root()
    if not root.exists():
        return []

    templates: list[TemplateMeta] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        meta_path = child / "template.json"
        if not meta_path.exists():
            continue
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            meta = TemplateMeta(**data)
            templates.append(meta)
        except Exception:
            logger.warning("Failed to parse template.json in %s", child, exc_info=True)

    return templates


def get_template(template_id: str) -> TemplateMeta | None:
    """Get a single template by ID."""
    for t in list_templates():
        if t.id == template_id:
            return t
    return None


def get_template_dir(template_id: str) -> Path | None:
    """Return the filesystem Path to a template directory."""
    root = _get_templates_root()
    # First check if template.json declares this ID
    meta_path = root / template_id / "template.json"
    if meta_path.exists():
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if data.get("id") == template_id:
                return root / template_id
        except Exception:
            pass
    return None
