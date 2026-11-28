"""Batch B（2026-11-28）：Remotion 缩略图改调共享 helper 的委托测试。

放在 worker 独立套件而不是 backend/tests：backend 的
``test_worker_incremental_manifest.py`` 在导入时把 ``sys.modules["pipelines"]`` 换成空包桩，
backend 进程里 ``import pipelines.compose_remotion`` 会随收集顺序失败。
"""

from __future__ import annotations

from pathlib import Path

import pipelines.compose_remotion as cr


def test_remotion_thumbnail_delegates_to_shared_helper(tmp_path: Path, monkeypatch) -> None:
    calls: list[tuple[Path, Path, tuple, dict]] = []

    def fake(video, output, *args, **kwargs):
        calls.append((video, output, args, kwargs))
        return True

    monkeypatch.setattr(cr, "_ffmpeg_extract_thumbnail", fake)
    video = tmp_path / "output.mp4"
    video.write_bytes(b"x")

    cr._extract_thumbnail(video, tmp_path)

    assert len(calls) == 1
    called_video, called_output, args, kwargs = calls[0]
    assert called_video == video
    assert called_output == tmp_path / "thumbnail.jpg"
    assert args == ()
    # 保持现有取帧位置（25%）与超时（60s）
    assert kwargs.get("seek_ratio") == 0.25
    assert kwargs.get("timeout") == 60


def test_remotion_thumbnail_never_raises_when_helper_fails(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(cr, "_ffmpeg_extract_thumbnail", lambda *a, **k: False)
    cr._extract_thumbnail(tmp_path / "output.mp4", tmp_path)

    def boom(*a, **k):
        raise RuntimeError("helper exploded")

    monkeypatch.setattr(cr, "_ffmpeg_extract_thumbnail", boom)
    cr._extract_thumbnail(tmp_path / "output.mp4", tmp_path)
    assert not (tmp_path / "thumbnail.jpg").exists()
