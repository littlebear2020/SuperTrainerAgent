"""平台级资产注册表存储（文件 JSON，type 键区分形态）。

布局（{base_dir} = .deer-flow）：
    assets/
    ├── registry/{user_id}/{asset_id}.json   # 记录
    └── files/{asset_id}/                    # source.pptx + logo.<ext> + trainer_brand.json

与 CourseProjectStore 同款文件 CRUD 风格（原子写、user_id 隔离）。首个类型键
`ppt_template`；未来视频/声音资产以新 type 键扩展（注册表平台级演化通路）。
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

VALID_ASSET_TYPES = ("ppt_template",)
STATUS_VALUES = ("ready", "failed")


class AssetRecord:
    """一条平台资产记录（dict 透传形状，避免 dataclass 与 JSON 漂移）。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    @property
    def id(self) -> str:
        return self._data["id"]

    @property
    def type(self) -> str:
        return self._data.get("type", "")

    @property
    def user_id(self) -> str:
        return self._data.get("user_id", "")

    def to_dict(self) -> dict:
        return dict(self._data)

    @classmethod
    def from_dict(cls, data: dict) -> AssetRecord:
        return cls(data)


class AssetStore:
    """File-based CRUD store for platform assets."""

    def __init__(self, base_dir: str | Path) -> None:
        self._base_dir = Path(base_dir)
        self._registry_dir = self._base_dir / "assets" / "registry"
        self._files_dir = self._base_dir / "assets" / "files"

    # ── 路径 ────────────────────────────────────────────────────────────
    def _user_dir(self, user_id: str) -> Path:
        d = self._registry_dir / user_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _record_path(self, asset_id: str, user_id: str) -> Path:
        return self._user_dir(user_id) / f"{asset_id}.json"

    def files_dir(self, asset_id: str) -> Path:
        return self._files_dir / asset_id

    # ── CRUD ────────────────────────────────────────────────────────────
    def create(self, *, user_id: str, type: str, **fields) -> AssetRecord:
        if type not in VALID_ASSET_TYPES:
            raise ValueError(f"Unknown asset type {type!r}")
        asset_id = f"as_{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        record = {
            "id": asset_id,
            "type": type,
            "user_id": user_id,
            "name": fields.get("name") or "未命名模板",
            "status": fields.get("status") or "ready",
            "source_filename": fields.get("source_filename"),
            "brand": fields.get("brand"),
            "created_at": now,
            "updated_at": now,
        }
        self.files_dir(asset_id).mkdir(parents=True, exist_ok=True)
        self._write(record)
        return AssetRecord(record)

    def get(self, asset_id: str, user_id: str) -> AssetRecord | None:
        path = self._record_path(asset_id, user_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("user_id") != user_id:
            return None
        return AssetRecord(data)

    def list(self, *, user_id: str, type: str | None = None) -> list[AssetRecord]:
        user_dir = self._registry_dir / user_id
        if not user_dir.exists():
            return []
        out: list[AssetRecord] = []
        for f in sorted(user_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            if data.get("user_id") != user_id:
                continue
            if type and data.get("type") != type:
                continue
            out.append(AssetRecord(data))
        out.sort(key=lambda r: r.to_dict().get("created_at", ""), reverse=True)
        return out

    def update(self, asset_id: str, user_id: str, **fields) -> AssetRecord:
        rec = self.get(asset_id, user_id)
        if rec is None:
            raise ValueError(f"Asset {asset_id} not found")
        data = rec.to_dict()
        for key in ("name", "status", "brand"):
            if key in fields and fields[key] is not None:
                data[key] = fields[key]
        data["updated_at"] = datetime.now(UTC).isoformat()
        self._write(data)
        return AssetRecord(data)

    def delete(self, asset_id: str, user_id: str) -> None:
        path = self._record_path(asset_id, user_id)
        if path.exists():
            path.unlink()
        files = self.files_dir(asset_id)
        if files.exists():
            import shutil

            shutil.rmtree(files, ignore_errors=True)

    def _write(self, record: dict) -> None:
        path = self._record_path(record["id"], record["user_id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)
