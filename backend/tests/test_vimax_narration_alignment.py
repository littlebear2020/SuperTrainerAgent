"""ViMax 对白镜 TTS 逐镜定长对齐（修复累积漂移）。

测试 _align_chunk_to_shot：
- 短于目标 → apad 补尾部静音
- 长于目标(≤1.3x) → atempo 加速
- 极长(>1.3x) → atempo 1.3 + 硬截断（R3：加 warning）
- 已接近目标 → 原样返回
"""

from unittest.mock import patch

from app.gateway.video_gen.engines.vimax import narration_generator as ng


def test_align_pads_short_chunk(tmp_path):
    """chunk 2s，目标 5.16s → apad 补到 ~5.16s。"""
    chunk = tmp_path / "narration_chunk_0000.mp3"
    chunk.write_bytes(b"fake-mp3")
    with patch.object(ng, "_probe_audio_duration", return_value=2.0), patch.object(ng, "subprocess") as mock_sub:
        mock_sub.run.return_value = type("P", (), {"returncode": 0})()
        aligned = tmp_path / "narration_chunk_0000_aligned.mp3"
        aligned.write_bytes(b"fake-aligned")
        result = ng._align_chunk_to_shot(chunk, target_duration=5.16, task_dir=tmp_path, chunk_idx=0)
    cmd = mock_sub.run.call_args[0][0]
    assert "apad=whole_dur=5.16" in " ".join(cmd)
    assert result.endswith("_aligned.mp3")


def test_align_speeds_up_long_chunk(tmp_path):
    """chunk 6s，目标 5.16s → atempo 加速（ratio≈1.16）+ -t 截断。"""
    chunk = tmp_path / "narration_chunk_0001.mp3"
    chunk.write_bytes(b"fake-mp3")
    with patch.object(ng, "_probe_audio_duration", return_value=6.0), patch.object(ng, "subprocess") as mock_sub:
        mock_sub.run.return_value = type("P", (), {"returncode": 0})()
        aligned = tmp_path / "narration_chunk_0001_aligned.mp3"
        aligned.write_bytes(b"fake-aligned")
        result = ng._align_chunk_to_shot(chunk, target_duration=5.16, task_dir=tmp_path, chunk_idx=1)
    cmd = mock_sub.run.call_args[0][0]
    cmd_str = " ".join(cmd)
    assert "atempo=" in cmd_str
    assert "-t" in cmd_str
    assert result.endswith("_aligned.mp3")


def test_align_caps_atempo_at_130(tmp_path):
    """chunk 8s，目标 5.16s（ratio 1.55）→ atempo 不超 1.3（R3 截断）。"""
    chunk = tmp_path / "narration_chunk_0002.mp3"
    chunk.write_bytes(b"fake-mp3")
    with patch.object(ng, "_probe_audio_duration", return_value=8.0), patch.object(ng, "subprocess") as mock_sub:
        mock_sub.run.return_value = type("P", (), {"returncode": 0})()
        aligned = tmp_path / "narration_chunk_0002_aligned.mp3"
        aligned.write_bytes(b"fake-aligned")
        ng._align_chunk_to_shot(chunk, target_duration=5.16, task_dir=tmp_path, chunk_idx=2)
    cmd_str = " ".join(mock_sub.run.call_args[0][0])
    import re

    m = re.search(r"atempo=([\d.]+)", cmd_str)
    assert m is not None
    assert float(m.group(1)) <= 1.3 + 0.001


def test_align_extreme_overflow_logs_warning(tmp_path, caplog):
    """chunk 远超目标(ratio>1.3) → 写 warning 便于验收统计（R3）。"""
    import logging

    chunk = tmp_path / "narration_chunk_0004.mp3"
    chunk.write_bytes(b"fake-mp3")
    with patch.object(ng, "_probe_audio_duration", return_value=10.0), patch.object(ng, "subprocess") as mock_sub:
        mock_sub.run.return_value = type("P", (), {"returncode": 0})()
        aligned = tmp_path / "narration_chunk_0004_aligned.mp3"
        aligned.write_bytes(b"fake-aligned")
        with caplog.at_level(logging.WARNING):
            ng._align_chunk_to_shot(chunk, target_duration=5.16, task_dir=tmp_path, chunk_idx=4)
    assert "仍需截断" in caplog.text or "截断" in caplog.text


def test_align_already_correct_returns_original(tmp_path):
    """chunk 5.1s，目标 5.16s（差 <0.1s）→ 原样返回，不调 ffmpeg。"""
    chunk = tmp_path / "narration_chunk_0003.mp3"
    chunk.write_bytes(b"fake-mp3")
    with patch.object(ng, "_probe_audio_duration", return_value=5.1), patch.object(ng, "subprocess") as mock_sub:
        result = ng._align_chunk_to_shot(chunk, target_duration=5.16, task_dir=tmp_path, chunk_idx=3)
    assert result == str(chunk)
    mock_sub.run.assert_not_called()
