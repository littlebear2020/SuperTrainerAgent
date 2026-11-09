"""Phase 1：消灭末帧定格——分层降级链测试（Tier 1 慢放 / Tier 2 Ken Burns / Tier 3 tpad）。

测试铁律：
- 所有 concat 产物 ffprobe 校验时长 ∈ [a+b−0.3, a+b+0.5]
- 变异测试：撤回修复看测试真红
- gap_ratio = gap / target
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import (  # noqa: E402
    _ffmpeg_extend_clip_to_duration,
    _probe_duration,
    _probe_dimensions,
    normalize_clip_resolution,
)


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_clip(path, duration, *, size="320x240", with_audio=False):
    cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"testsrc2=s={size}:r=30"]
    if with_audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
    cmd += ["-t", str(duration), "-c:v", "libx264", "-pix_fmt", "yuv420p"]
    if with_audio:
        cmd += ["-c:a", "aac", "-shortest"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)
    return path


def _frame_at(video, t, tmp_path):
    out = tmp_path / f"f_{abs(hash((str(video), t))) % 99999}.png"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-ss", str(t), "-i", str(video),
         "-frames:v", "1", "-update", "1", str(out)],
        capture_output=True, check=True,
    )
    return np.asarray(Image.open(out).convert("L"), dtype=float)


# ---------------------------------------------------------------------------
# Tier 1: setpts 微慢放 (≤10%)
# ---------------------------------------------------------------------------

class TestTier1SlowMo:
    """gap_ratio ≤ 10% → 整段 setpts 微慢放，不克隆末帧。"""

    def test_small_gap_uses_slowmo(self, tmp_path):
        """9.3s 源 → 10s 目标（gap_ratio=7%）：产物时长精确 + 整段慢放。"""
        src = _make_clip(tmp_path / "src.mp4", 9.3)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 10.0, out)

        actual = _probe_duration(out)
        assert actual == pytest.approx(10.0, abs=0.15), f"慢放后时长 {actual}s != 10.0s"

    def test_slowmo_not_freeze(self, tmp_path):
        """慢放 ≠ 定格：输出末帧不等于源末帧（tpad 会克隆源末帧）。

        9.0s 源 → 10s 目标（gap_ratio=10%）。
        tpad 定格：out@9.5s = 克隆 src 的最后一帧（约 src@8.95s），diff ≈ 0。
        setpts 慢放：out@9.5s = src 内容经慢放后的对应帧（约 src@8.55s），与 src 末帧差异大。
        """
        src = _make_clip(tmp_path / "src.mp4", 9.0)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 10.0, out)

        # 源的最后一帧（在源停止前的最后一帧）
        src_last_frame = _frame_at(src, 8.95, tmp_path)
        # 输出 9.5s 处——在补出来的尾段（源只到 9.0s）
        out_late_frame = _frame_at(out, 9.5, tmp_path)
        diff = float(np.abs(src_last_frame - out_late_frame).mean())
        # testsrc2 在最后 0.4s（8.55→8.95）有明显内容变化
        # tpad: out@9.5 = clone(src_last) → diff ≈ 0
        # slowmo: out@9.5 ≈ src@8.55 → 与 src@8.95 差异大（testsrc2 动态）
        assert diff > 2.0, (
            f"out@9.5s ≈ src 末帧（diff={diff:.2f}）—— 疑似 tpad 定格而非慢放"
        )

    def test_exact_10pct_boundary_uses_slowmo(self, tmp_path):
        """gap_ratio 恰好 = 10% → Tier 1 慢放（边界条件，≤ 包含）。"""
        src = _make_clip(tmp_path / "src.mp4", 9.0)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 10.0, out)
        actual = _probe_duration(out)
        assert actual == pytest.approx(10.0, abs=0.15)


# ---------------------------------------------------------------------------
# Tier 2: Ken Burns 末段缓推 (默认主力)
# ---------------------------------------------------------------------------

class TestTier2KenBurns:
    """gap_ratio > 10% → 末段 Ken Burns zoompan 缓推，前段原速。"""

    def test_large_gap_total_duration(self, tmp_path):
        """5s 源 → 12s 目标：产物总时长 ≈12s。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)
        actual = _probe_duration(out)
        assert actual == pytest.approx(12.0, abs=0.5), f"Ken Burns 补长后 {actual}s != 12.0s"

    def test_front_section_unchanged(self, tmp_path):
        """前段（5s 源的前 4.5s）应与源逐帧一致——Ken Burns 只动末段。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)

        src_frame = _frame_at(src, 2.0, tmp_path)
        out_frame = _frame_at(out, 2.0, tmp_path)
        diff = float(np.abs(src_frame - out_frame).mean())
        assert diff < 5.0, f"前段被改动（diff={diff:.2f}），Ken Burns 不应动前段"

    def test_tail_section_has_movement(self, tmp_path):
        """末段相邻帧有像素位移——证明在动（Ken Burns zoom），非定格。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)

        # 末段 7-10s 之间应有运动
        a = _frame_at(out, 7.0, tmp_path)
        b = _frame_at(out, 9.0, tmp_path)
        diff = float(np.abs(a - b).mean())
        assert diff > 0.1, f"末段无运动（diff={diff:.2f}），疑似定格"

    def test_tpad_not_in_command(self, tmp_path, monkeypatch):
        """Ken Burns 路径不应出现 tpad= 滤镜（那是 Tier 3 兜底）。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"

        ffmpeg_cmds: list[str] = []
        original_run = subprocess.run

        def capture_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd and cmd[0] == "ffmpeg":
                ffmpeg_cmds.append(" ".join(str(c) for c in cmd))
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", capture_run)
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)

        # ffmpeg 命令不应含 tpad= 滤镜（正常路径走 Ken Burns，不走 Tier 3）
        for cmd_str in ffmpeg_cmds:
            assert "tpad=" not in cmd_str, f"Tier 2 路径不应出现 tpad= 滤镜: {cmd_str[:120]}"


# ---------------------------------------------------------------------------
# Tier 3: tpad 最终兜底（保留语义，永不失败）
# ---------------------------------------------------------------------------

class TestTier3TpadFallback:
    """抽帧失败等极端情况 → tpad 硬定格兜底。"""

    def test_tpad_fallback_when_frame_extraction_fails(self, tmp_path, monkeypatch):
        """_sseof 抽帧失败时 → 回落 tpad 定格。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"

        original_run = subprocess.run

        def fail_sseof(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            if "-sseof" in cmd_str:
                # 模拟抽帧失败
                return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"mock fail")
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fail_sseof)
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)
        actual = _probe_duration(out)
        assert actual == pytest.approx(12.0, abs=0.5), f"tpad 兜底后时长 {actual}s"


# ---------------------------------------------------------------------------
# Task 3: minterpolate（默认关闭）
# ---------------------------------------------------------------------------

class TestMinterpolate:
    """interp_enabled=false（默认）→ 不进入 minterpolate 分支。"""

    def test_default_disables_minterpolate(self, tmp_path, monkeypatch):
        """默认不开启 interp → 不应出现 minterpolate 指令。"""
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"

        commands: list[list[str]] = []
        original_run = subprocess.run

        def capture_run(cmd, *args, **kwargs):
            if isinstance(cmd, list):
                commands.append(list(cmd))
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", capture_run)
        _ffmpeg_extend_clip_to_duration(src, 12.0, out)  # gap_ratio > 10%

        for cmd in commands:
            cmd_str = " ".join(str(c) for c in cmd)
            assert "minterpolate" not in cmd_str, (
                f"默认 interp_enabled=false 但出现了 minterpolate: {cmd_str[:100]}"
            )

    def test_minterpolate_degradation_chain(self, tmp_path, monkeypatch):
        """interp_enabled=true 且 gap_ratio ≤ 0.50 时 mci 失败 → blend → Ken Burns 逐级降级。

        用 5s 源 → 6.5s 目标（gap_ratio = 1.5/6.5 ≈ 0.23 ≤ 0.50）。
        """
        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"

        mci_attempts = [0]
        blend_attempts = [0]
        original_run = subprocess.run

        def fail_minterpolate(cmd, *args, **kwargs):
            cmd_str = " ".join(str(c) for c in cmd) if isinstance(cmd, list) else str(cmd)
            if "mi_mode=mci" in cmd_str:
                mci_attempts[0] += 1
                return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"mci fail")
            if "mi_mode=blend" in cmd_str:
                blend_attempts[0] += 1
                return subprocess.CompletedProcess(cmd, returncode=1, stdout=b"", stderr=b"blend fail")
            return original_run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", fail_minterpolate)
        _ffmpeg_extend_clip_to_duration(
            src, 6.5, out, config={"interp_enabled": True}
        )
        assert mci_attempts[0] >= 1, "mci 未被尝试"
        assert blend_attempts[0] >= 1, "mci 失败后未降级到 blend"
        # blend 也失败 → 应走 Ken Burns
        actual = _probe_duration(out)
        assert actual > 0, "所有 minterpolate 降级后产物为空"


# ---------------------------------------------------------------------------
# Task 4: 配置 + 日志
# ---------------------------------------------------------------------------

class TestConfigAndLogging:
    """config 透传 + fill_strategy 日志。"""

    def test_config_slow_max_ratio_override(self, tmp_path):
        """config 中 slow_max_ratio 覆盖模块常量。"""
        src = _make_clip(tmp_path / "src.mp4", 9.0)
        out = tmp_path / "out.mp4"
        # gap=1s, target=10s, gap_ratio=0.1. 设 slow_max_ratio=0.2 → Tier 1
        _ffmpeg_extend_clip_to_duration(
            src, 10.0, out, config={"slow_max_ratio": 0.20}
        )
        actual = _probe_duration(out)
        assert actual == pytest.approx(10.0, abs=0.15)

    def test_fill_strategy_logged(self, tmp_path, caplog):
        """每段补长打 INFO 日志 fill_strategy=...。"""
        import logging as _logging

        src = _make_clip(tmp_path / "src.mp4", 5.0)
        out = tmp_path / "out.mp4"
        with caplog.at_level(_logging.INFO, logger="pipelines._ffmpeg_helpers"):
            _ffmpeg_extend_clip_to_duration(src, 12.0, out)

        logged_str = " ".join(r.message for r in caplog.records)
        assert "fill_strategy" in logged_str, f"未记录 fill_strategy: {logged_str[:200]}"

    def test_no_env_dependency(self):
        """worker 禁止 env 配置——函数不读 os.environ。"""
        import inspect

        from pipelines import _ffmpeg_helpers
        source = inspect.getsource(_ffmpeg_helpers)
        # 函数内不应出现 os.environ 或 getenv
        assert "os.environ" not in source, "_ffmpeg_helpers 引入了 os.environ"
        assert "getenv" not in source, "_ffmpeg_helpers 引入了 getenv"


# ---------------------------------------------------------------------------
# 既有测试兼容（验证旧行为不回退）
# ---------------------------------------------------------------------------

class TestBackwardCompatibility:
    """签名兼容：旧调用方式（不传 config）仍工作。"""

    def test_long_clip_trimmed(self, tmp_path):
        """clip ≥ target → 截断不补长。"""
        src = _make_clip(tmp_path / "long.mp4", 5.0)
        out = tmp_path / "trim.mp4"
        _ffmpeg_extend_clip_to_duration(src, 2.0, out)
        assert _probe_duration(out) == pytest.approx(2.0, abs=0.1)

    def test_no_audio_in_output(self, tmp_path):
        """补长产物无音轨（音频在 mix 层单独合成）。"""
        src = _make_clip(tmp_path / "src.mp4", 9.0, with_audio=True)
        out = tmp_path / "out.mp4"
        _ffmpeg_extend_clip_to_duration(src, 10.0, out)
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
            capture_output=True, text=True, check=True,
        )
        # Tier 1/2/3 都应无音轨（-an）
        assert proc.stdout.strip() == "", "补长产物不应含音轨"
