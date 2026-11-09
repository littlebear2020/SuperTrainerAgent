"""内置 BGM 曲库完整性与公开授权测试。"""

import json
from pathlib import Path

BGM_DIR = Path(__file__).parent.parent / "app" / "gateway" / "video_gen" / "resources" / "bgm"


def _tracks():
    return json.loads((BGM_DIR / "index.json").read_text(encoding="utf-8"))["tracks"]


def test_bgm_index_integrity():
    tracks = _tracks()
    ids = [t["id"] for t in tracks]
    assert len(ids) == len(set(ids)), "id 重复"
    assert len(tracks) == 4, "公开基线内置曲库 = 4 首授权明确的微课 BGM"
    for t in tracks:
        path = BGM_DIR / t["filename"]
        assert path.exists(), f"{t['filename']} 缺失"
        assert path.stat().st_size > 100_000, f"{t['filename']} 体积异常"
        assert t["duration"] > 0
        assert t["name"] and t["author"]
        assert t["license"] in {"CC0 1.0", "CC BY 4.0"}
        assert t["source_url"].startswith("https://freesound.org/")


def test_removed_legacy_ids_gone():
    ids = {t["id"] for t in _tracks()}
    for legacy in (
        "ambient_calm",
        "upbeat_corporate",
        "cinematic_warm",
        "strive",
        "corporate_view",
        "echoes",
        "mirage",
        "hopeful",
        "dreamy_lofi",
    ):
        assert legacy not in ids
