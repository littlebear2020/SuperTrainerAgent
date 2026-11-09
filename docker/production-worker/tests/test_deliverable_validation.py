"""Task 11 (H5 + remotion 0 字节): 交付物必须真校验，不能只记录大小。

``worker.py`` 成功路径只有

    prior["file_size"] = output_path.stat().st_size

——**记录**大小但**从不校验**。0 字节、截断、无视频轨的产物全部标 ready/progress=100，
与完美渲染不可区分。用户点开是个打不开的文件。

修法：成功路径加交付闸——``st_size > 0`` + ffprobe 探到视频流且 duration > 0，
不满足则抛错走 failed 分支（error_message 有意义）。
"""

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from worker import _validate_deliverable  # noqa: E402


def _has_ffprobe() -> bool:
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


def test_zero_byte_output_rejected(tmp_path):
    """0 字节产物必须被拒（此前标 ready）。"""
    bad = tmp_path / "output.mp4"
    bad.write_bytes(b"")

    with pytest.raises(RuntimeError, match="0 字节|empty|zero"):
        _validate_deliverable(bad)


def test_missing_output_rejected(tmp_path):
    """产物不存在必须被拒。"""
    with pytest.raises(RuntimeError, match="不存在|missing|not found"):
        _validate_deliverable(tmp_path / "nope.mp4")


@pytest.mark.skipif(not _has_ffprobe(), reason="需要 ffprobe")
def test_non_video_file_rejected(tmp_path):
    """有字节但不是视频（无视频轨）必须被拒。"""
    junk = tmp_path / "output.mp4"
    junk.write_bytes(b"x" * 4096)

    with pytest.raises(RuntimeError, match="视频轨|video stream|duration"):
        _validate_deliverable(junk)


@pytest.mark.skipif(not _has_ffprobe(), reason="需要 ffprobe")
def test_audio_only_file_rejected(tmp_path):
    """只有音轨没画面的产物必须被拒。"""
    audio_only = tmp_path / "output.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:a", "aac", str(audio_only)],
        capture_output=True, check=True,
    )

    with pytest.raises(RuntimeError, match="视频轨|video stream"):
        _validate_deliverable(audio_only)


@pytest.mark.skipif(not _has_ffprobe(), reason="需要 ffprobe")
def test_healthy_video_accepted(tmp_path):
    """正常视频通过校验（不得误杀）。"""
    good = tmp_path / "output.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30",
         "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(good)],
        capture_output=True, check=True,
    )

    # 不抛即通过
    _validate_deliverable(good)


def test_validation_wired_into_success_path():
    """结构断言：成功路径必须真的调校验（否则函数只是死代码）。"""
    src = (Path(__file__).parent.parent / "worker.py").read_text(encoding="utf-8")
    assert "_validate_deliverable(" in src, "worker 成功路径未接通交付校验"
    # 必须在写 status=ready 之前
    val_at = src.index("_validate_deliverable(output_path")
    ready_at = src.index('status="ready"')
    assert val_at < ready_at, "校验必须在标 ready 之前"


def test_na_duration_not_misrejected(tmp_path, monkeypatch):
    """P2-16：有视频轨但 format duration=N/A（部分容器/流）不得误杀。

    与「ffprobe 不可用只做前两道」同一哲学：工具给不出时长时不因缺信息
    把好任务打死。修前 float("N/A") → ValueError → duration=0.0 → 误拒。
    """
    vid = tmp_path / "output.mp4"
    vid.write_bytes(b"x" * 4096)

    class _Proc:
        returncode = 0
        stdout = "codec_type=video\nduration=N/A\n"
        stderr = ""

    monkeypatch.setattr("worker.subprocess.run", lambda *a, **k: _Proc())
    _validate_deliverable(vid)  # 不应抛


def test_zero_duration_still_rejected(tmp_path, monkeypatch):
    """P2-16 对照：明确探到 duration=0（截断产物）仍必须拒。"""
    vid = tmp_path / "output.mp4"
    vid.write_bytes(b"x" * 4096)

    class _Proc:
        returncode = 0
        stdout = "codec_type=video\nduration=0.000000\n"
        stderr = ""

    monkeypatch.setattr("worker.subprocess.run", lambda *a, **k: _Proc())
    with pytest.raises(RuntimeError, match="duration"):
        _validate_deliverable(vid)
