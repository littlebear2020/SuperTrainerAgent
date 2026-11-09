"""E17: mixed _image_to_video fallback 失败须有 warning 日志、不静默产空段。"""

import logging
from pathlib import Path


def test_image_to_video_logs_warning_when_both_ffmpeg_fail(tmp_path, monkeypatch, caplog):
    """主命令 + fallback 都失败 → logger.warning（不静默）；output 不被当作有效段。"""
    import pipelines.video_gen_mixed as m

    image = tmp_path / "img.png"
    image.write_bytes(b"png")
    output = tmp_path / "out.mp4"

    class _Proc:
        returncode = 1
        stdout = b""
        stderr = b"boom"

    monkeypatch.setattr(m.subprocess, "run", lambda *a, **k: _Proc())

    with caplog.at_level(logging.WARNING, logger="pipelines.video_gen_mixed"):
        m._image_to_video(image, output, duration=3.0, width=1080, height=1920)

    assert any("image_to_video" in rec.message or "图生视频" in rec.message for rec in caplog.records), (
        "fallback 失败须 logger.warning，不得静默"
    )


def test_image_to_video_silent_on_success(tmp_path, monkeypatch, caplog):
    """主命令成功 → 不写 warning（不回归）。"""
    import pipelines.video_gen_mixed as m

    image = tmp_path / "img.png"
    image.write_bytes(b"png")
    output = tmp_path / "out.mp4"

    class _Proc:
        returncode = 0
        stdout = b""
        stderr = b""

    runs = {"n": 0}

    def _run(*a, **k):
        runs["n"] += 1
        output.write_bytes(b"mp4")  # 模拟成功写出
        return _Proc()

    monkeypatch.setattr(m.subprocess, "run", _run)

    with caplog.at_level(logging.WARNING, logger="pipelines.video_gen_mixed"):
        m._image_to_video(image, output, duration=3.0, width=1080, height=1920)

    assert runs["n"] == 1, "主命令成功不应触发 fallback"
    assert not any("image_to_video" in r.message for r in caplog.records)
