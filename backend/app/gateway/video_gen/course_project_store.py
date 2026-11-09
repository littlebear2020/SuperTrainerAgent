"""File-based JSON storage for CourseProject entities.

Stores projects at {base_dir}/course_projects/{user_id}/{project_id}.json
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

from .course_models import CourseProject


class CourseProjectStore:
    """File-based CRUD store for CourseProject."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)

    def _user_dir(self, user_id: str) -> Path:
        d = self._base_dir / "course_projects" / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _project_path(self, project_id: str, user_id: str) -> Path:
        return self._user_dir(user_id) / f"{project_id}.json"

    def create(self, user_id: str, **kwargs) -> CourseProject:
        project_id = f"cp_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        proj = CourseProject(
            id=project_id,
            user_id=user_id,
            created_at=now,
            updated_at=now,
            **{k: v for k, v in kwargs.items() if k in CourseProject.__dataclass_fields__ and k not in ("id", "user_id", "created_at", "updated_at")},
        )
        self._write(proj)
        return proj

    def get(self, project_id: str, user_id: str) -> CourseProject | None:
        path = self._project_path(project_id, user_id)
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        proj = CourseProject.from_dict(data)
        self._persist_backfilled_ids(proj)
        return proj

    def list(self, user_id: str) -> list[CourseProject]:
        user_dir = self._user_dir(user_id)
        projects = []
        for f in sorted(user_dir.glob("*.json")):
            data = json.loads(f.read_text(encoding="utf-8"))
            proj = CourseProject.from_dict(data)
            self._persist_backfilled_ids(proj)
            projects.append(proj)
        return projects

    def _persist_backfilled_ids(self, proj: CourseProject) -> None:
        """B1 修复：存量 sheet/prop 无 id 时 from_dict 随机回填——读到即落盘，
        保证 id 寻址（serve/delete/replace_id）跨请求稳定（首次读后固化）。"""
        if getattr(proj, "_ids_backfilled", False):
            self._write(proj)

    def update(self, project_id: str, user_id: str, **kwargs) -> CourseProject:
        proj = self.get(project_id, user_id)
        if proj is None:
            raise ValueError(f"Project {project_id} not found")
        for k, v in kwargs.items():
            if k in CourseProject.__dataclass_fields__ and k not in ("id", "user_id", "created_at"):
                setattr(proj, k, v)
        proj.updated_at = datetime.now(UTC).isoformat()
        self._write(proj)
        return proj

    def delete(self, project_id: str, user_id: str) -> None:
        path = self._project_path(project_id, user_id)
        path.unlink(missing_ok=True)

    def _write(self, proj: CourseProject) -> None:
        path = self._project_path(proj.id, proj.user_id)
        path.write_text(json.dumps(proj.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
