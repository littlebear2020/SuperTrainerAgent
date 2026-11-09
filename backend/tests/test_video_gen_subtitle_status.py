"""P0-2: 字幕烧录状态不再静默——worker 写 subtitle_status.json，gateway 读出回传前端。

根因：worker 烧录失败只写 subtitle_burn_failed.txt（无人读），用户看不到"为什么没字幕"。
修复：worker 写结构化 subtitle_status.json(ok/failed/skipped+error)，gateway 提取回传。
libass 非根因(Dockerfile:13 构建期门禁)，真根因是静默失败不可见。
"""

import json
from pathlib import Path

from app.gateway.video_gen.service import _extract_subtitle_status


def test_extract_subtitle_status_failed(tmp_path: Path):
    (tmp_path / "subtitle_status.json").write_text(json.dumps({"status": "failed", "error": "libass render error"}), encoding="utf-8")
    assert _extract_subtitle_status(tmp_path) == {"status": "failed", "error": "libass render error"}


def test_extract_subtitle_status_ok(tmp_path: Path):
    (tmp_path / "subtitle_status.json").write_text(json.dumps({"status": "ok"}), encoding="utf-8")
    assert _extract_subtitle_status(tmp_path) == {"status": "ok", "error": None}


def test_extract_subtitle_status_missing_is_skipped(tmp_path: Path):
    """无 status 文件 → skipped（未配置字幕或未到烧录步骤）。"""
    assert _extract_subtitle_status(tmp_path) == {"status": "skipped", "error": None}


def test_extract_subtitle_status_malformed_is_skipped(tmp_path: Path):
    """status 文件损坏 → skipped（不崩）。"""
    (tmp_path / "subtitle_status.json").write_text("not json{", encoding="utf-8")
    assert _extract_subtitle_status(tmp_path) == {"status": "skipped", "error": None}
