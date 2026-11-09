"""2026-08-20：`_get_audio_duration` 改解码口径（渐进音画错位根因修复，vg_3300179a11f0）。

根因：LAME gapless mp3 的 ffprobe **格式时长**含 encoder delay/padding（真实 TTS
产物每文件 ~55ms），解码拼接时裁掉——frame.duration/字幕轴按格式口径、音轨按
解码口径，94 帧累积 5.2s 渐进错位。

三层测试（P2-1 修正：合成 lavfi mp3 无 gapless 特征，条件断言会 skip 自噬——
确定性断言 (a)(b) 不依赖真实 gapless 产物，(c) 条件验证殿后）：
  (a) monkeypatch 假 stderr：time= 解析取最后一个（含 \r 分隔/N/A 起始行/兜底）；
  (b) 恒真方向不等式：解码口径 ≤ 格式口径 + 0.05（任何 mp3 恒真，不 skip）；
  (c) gapless 真实特征验证：格式−解码 > 40ms 才跑（无特征环境 skip 不强造绿灯）。
"""

import subprocess
from pathlib import Path

import pytest

from app.gateway.video_gen import audio_flow
from app.gateway.video_gen.orchestration import tts


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_mp3(path: Path, seconds: float = 3.0) -> str:
    """与 test_audio_concat_no_drift.py 同款生成法（lavfi sine → libmp3lame）。"""
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-acodec", "libmp3lame", "-q:a", "4", str(path)],
        capture_output=True,
        check=True,
    )
    return str(path)


def _ffprobe_format_duration(path: str) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", path],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return float(probe.stdout.strip())


# ---------------------------------------------------------------------------
# (a) stderr time= 解析：确定性（monkeypatch，不依赖真实文件）
# ---------------------------------------------------------------------------


def _fake_run_factory(ffmpeg_stderr: str, ffprobe_stdout: str = "0.0"):  # noqa: ANN202
    def _fake_run(cmd, **kwargs):  # noqa: ANN001,ANN003
        if cmd[0] == "ffmpeg":
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr=ffmpeg_stderr)
        return subprocess.CompletedProcess(cmd, 0, stdout=ffprobe_stdout, stderr="")

    return _fake_run


def test_decode_probe_takes_last_time_line(monkeypatch):
    """多个 time= 行（含 \r 进度行分隔 + 起始 N/A）→ 取最后一个有效值。"""
    stderr = "frame=    1 fps=0.0 q=0.0 size=N/A time=N/A bitrate=N/A speed=N/A\rframe=   50 fps=0.0 q=-0.0 size=N/A time=00:00:01.50 bitrate=N/A speed= 100x\rframe=  143 fps=0.0 q=-0.0 Lsize=N/A time=00:00:03.08 bitrate=N/A speed= 144x\n"
    monkeypatch.setattr(tts.subprocess, "run", _fake_run_factory(stderr, ffprobe_stdout="9.999"))
    assert tts._get_audio_duration("/fake.mp3") == pytest.approx(3.08, abs=1e-6)


def test_decode_probe_falls_back_to_ffprobe_when_no_time(monkeypatch):
    """主路径无有效 time= → 回落 ffprobe 格式口径（兜底不更坏）。"""
    monkeypatch.setattr(tts.subprocess, "run", _fake_run_factory("ffmpeg error: no such file\n", ffprobe_stdout="3.144\n"))
    assert tts._get_audio_duration("/fake.mp3") == pytest.approx(3.144, abs=1e-6)


def test_decode_probe_fallback_failure_returns_zero(monkeypatch):
    monkeypatch.setattr(tts.subprocess, "run", _fake_run_factory("boom\n", ffprobe_stdout=""))
    assert tts._get_audio_duration("/fake.mp3") == 0.0


# ---------------------------------------------------------------------------
# (b) 恒真方向不等式：解码口径 ≤ 格式口径 + 0.05（不 skip）
# ---------------------------------------------------------------------------


def test_decode_duration_never_exceeds_format_duration(tmp_path):
    """解码裁 delay/padding 只会更短——锁死「解码口径不反向变大」（恒真不等式）。"""
    f = _make_mp3(tmp_path / "a.mp3")
    decoded = tts._get_audio_duration(f)
    fmt = _ffprobe_format_duration(f)
    assert decoded > 0
    assert decoded <= fmt + 0.05, f"解码口径({decoded})不得大于格式口径({fmt})+0.05"


# ---------------------------------------------------------------------------
# (c) gapless 真实特征验证（条件 skip：合成产物无特征的环境不强造绿灯）
# ---------------------------------------------------------------------------


def test_gapless_file_decode_duration_is_shorter(tmp_path):
    f = _make_mp3(tmp_path / "g.mp3")
    fmt = _ffprobe_format_duration(f)
    decoded = tts._get_audio_duration(f)
    if fmt - decoded <= 0.040:
        pytest.skip(f"合成产物无 gapless 特征（格式 {fmt:.3f} vs 解码 {decoded:.3f}），无法复现")
    assert decoded < fmt
    # 差值量级断言（修恒真断言：decoded ≈ fmt-(fmt-decoded) 零信息量）——
    # LAME delay/padding 为数十毫秒级；>0.2s 说明探测失真而非 gapless 裁剪
    assert 0.040 < fmt - decoded < 0.2, f"格式/解码差 {fmt - decoded:.3f}s 超出 gapless 合理量级"


def test_concat_duration_matches_sum_of_decode_probes(tmp_path):
    """拼接对账：N 份拼接产物的解码时长 ≈ Σ 单份解码时长（口径一致的最终目的）。"""
    parts = [_make_mp3(tmp_path / f"p{i}.mp3", seconds=1.0) for i in range(5)]
    singles = [tts._get_audio_duration(p) for p in parts]
    fmt0 = _ffprobe_format_duration(parts[0])
    if fmt0 - singles[0] <= 0.040:
        pytest.skip(f"合成产物无 gapless 特征（格式 {fmt0:.3f} vs 解码 {singles[0]:.3f}），无法复现")
    out = str(tmp_path / "joined.mp3")
    audio_flow._concat_audio(parts, out)
    total = tts._get_audio_duration(out)
    assert abs(total - sum(singles)) < 0.1, f"拼接 {total:.3f}s 与 Σ单份 {sum(singles):.3f}s 漂移超 0.1s"


# ---------------------------------------------------------------------------
# _concat_audio expected_seconds 对账告警（只观测不改行为）
# ---------------------------------------------------------------------------


def test_concat_drift_warning_fires_over_threshold(tmp_path, monkeypatch, caplog):
    parts = [_make_mp3(tmp_path / f"p{i}.mp3", seconds=1.0) for i in range(2)]
    out = str(tmp_path / "joined.mp3")
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 10.0)
    with caplog.at_level("WARNING", logger="app.gateway.video_gen.audio_flow"):
        audio_flow._concat_audio(parts, out, expected_seconds=99.0)
    assert any("audio concat drift" in r.message for r in caplog.records), "差 >0.5s 应告警"


def test_concat_drift_warning_silent_within_threshold(tmp_path, monkeypatch, caplog):
    parts = [_make_mp3(tmp_path / f"p{i}.mp3", seconds=1.0) for i in range(2)]
    out = str(tmp_path / "joined.mp3")
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 5.0)
    with caplog.at_level("WARNING", logger="app.gateway.video_gen.audio_flow"):
        audio_flow._concat_audio(parts, out, expected_seconds=5.3)
    assert not any("audio concat drift" in r.message for r in caplog.records), "差 0.3s 不应告警"


def test_concat_drift_check_skipped_when_no_expected(tmp_path, monkeypatch):
    parts = [_make_mp3(tmp_path / f"p{i}.mp3", seconds=1.0) for i in range(2)]
    out = str(tmp_path / "joined.mp3")
    called = []
    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: called.append(p) or 1.0)
    audio_flow._concat_audio(parts, out)
    assert called == [], "expected_seconds=None 不应触发对账探测"
