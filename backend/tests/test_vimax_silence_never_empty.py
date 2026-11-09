"""P1-6: _create_silence 不得写零字节文件；_concat_chunks 须校验时长。"""

import subprocess

import pytest

from app.gateway.video_gen.engines.vimax import narration_generator as ng


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def test_create_silence_raises_on_ffmpeg_failure(tmp_path, monkeypatch):
    """ffmpeg 失败时不得写零字节文件，必须 raise。"""
    out = tmp_path / "silence.mp3"

    def fake_run(*a, **kw):
        return subprocess.CompletedProcess(a[0], 1, b"", b"error")

    monkeypatch.setattr(ng.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="silence generation failed"):
        ng._create_silence(out, 2.0)
    assert not out.exists() or out.stat().st_size > 0, "不得留下零字节文件"


def test_concat_chunks_rejects_zero_byte_input(tmp_path):
    """_concat_chunks 遇到零字节输入须 raise（而非静默返回截断产物）。"""
    # Create 4 valid mp3s (2s each) and put zero-byte in position 2
    chunks = []
    for i in range(4):
        if i == 2:
            bad = tmp_path / f"chunk_{i}.mp3"
            bad.write_bytes(b"")
            chunks.append(str(bad))
        else:
            p = tmp_path / f"chunk_{i}.mp3"
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=24000:cl=mono", "-t", "2", "-c:a", "libmp3lame", "-q:a", "9", str(p)],
                capture_output=True,
                check=True,
            )
            chunks.append(str(p))

    output = str(tmp_path / "output.mp3")
    with pytest.raises(RuntimeError):
        ng._concat_chunks(chunks, output)
