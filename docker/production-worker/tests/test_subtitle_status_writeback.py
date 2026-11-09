"""Task 21: PPT 字幕失败写 subtitle_status=failed（2026-08-08 P1-5 重写）。

修前版本由 fake_pipeline 自调 _write_subtitle_status 写文件，从不经过生产代码
的写入点（_apply_colorgrade_and_subtitles 的烧录失败分支），且断言是条件式
if exists——删掉生产写入调用测试仍绿（变异不可证伪）。

本版直接测生产收口函数 _apply_colorgrade_and_subtitles：烧录失败 →
① 兜底返回原视频；② subtitle_status.json 由生产代码写入且 status=failed。
断言无条件执行。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers  # noqa: E402


def _mk_video(path: Path) -> Path:
    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1",
             "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("需要 ffmpeg")
    return path


def test_burn_failure_falls_back_and_writes_failed_status(tmp_path, monkeypatch):
    video = _mk_video(tmp_path / "in.mp4")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\n你好\n", encoding="utf-8")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()

    # 强制烧录失败（mock ffmpeg rc=1）——生产写入点 _apply_colorgrade_and_subtitles
    # 的失败分支必须：fallback 保原视频 + 写 subtitle_status=failed
    class _Fail:
        returncode = 1
        stdout = ""
        stderr = b"libass error: cannot parse"

    monkeypatch.setattr(_ffmpeg_helpers, "_subtitles_filter_available", lambda: True)
    monkeypatch.setattr(_ffmpeg_helpers.subprocess, "run", lambda *a, **k: _Fail())

    result = _ffmpeg_helpers._apply_colorgrade_and_subtitles(
        video,
        task_dir=tmp_path,
        tmp_dir=out_dir,
        color_palette=None,
        subtitle_path=str(srt),
    )

    assert result == video, "烧录失败必须兜底返回原视频（保后续 audio/BGM 流程）"
    status_file = tmp_path / "subtitle_status.json"
    assert status_file.exists(), "生产代码必须写 subtitle_status.json"
    data = json.loads(status_file.read_text(encoding="utf-8"))
    assert data.get("status") == "failed", f"期望 failed，实际 {data.get('status')}"
    assert data.get("error"), "failed 必须带非空 error（用户可见的失败原因）"


def test_burn_success_writes_ok_status(tmp_path, monkeypatch):
    """对照组：烧录成功 → status=ok（防把 failed/ok 写反）。"""
    video = _mk_video(tmp_path / "in.mp4")
    srt = tmp_path / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,800\n你好\n", encoding="utf-8")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()

    class _Ok:
        returncode = 0
        stdout = ""
        stderr = b""

    real_run = subprocess.run

    def fake_run(cmd, *a, **k):
        # 只拦烧录命令（-vf subtitles）；ffprobe 等其他调用走真实 subprocess
        if "-vf" in cmd:
            # 烧录产物必须存在，否则生产代码按失败处理
            Path(cmd[-1]).write_bytes(video.read_bytes())
            return _Ok()
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(_ffmpeg_helpers, "_subtitles_filter_available", lambda: True)
    monkeypatch.setattr(_ffmpeg_helpers.subprocess, "run", fake_run)

    result = _ffmpeg_helpers._apply_colorgrade_and_subtitles(
        video,
        task_dir=tmp_path,
        tmp_dir=out_dir,
        color_palette=None,
        subtitle_path=str(srt),
    )

    assert result != video, "烧录成功应返回带字幕的新文件"
    data = json.loads((tmp_path / "subtitle_status.json").read_text(encoding="utf-8"))
    assert data.get("status") == "ok"


def test_no_subtitle_writes_skipped_status(tmp_path):
    """对照组：无字幕文件 → status=skipped。"""
    video = _mk_video(tmp_path / "in.mp4")
    out_dir = tmp_path / "tmp"
    out_dir.mkdir()

    result = _ffmpeg_helpers._apply_colorgrade_and_subtitles(
        video,
        task_dir=tmp_path,
        tmp_dir=out_dir,
        color_palette=None,
        subtitle_path=None,
    )

    assert result == video
    data = json.loads((tmp_path / "subtitle_status.json").read_text(encoding="utf-8"))
    assert data.get("status") == "skipped"
