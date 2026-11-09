"""Phase 4 T-Q1（2026-08-17）：黑帧/冻结检测--数据收集先行（D-D 拍板：只记录不重生）。

真 ffmpeg 扫描用例（lavfi 生成黑视频/正常视频）+ render 接线（sidecar KIND 断言）。
"""

from __future__ import annotations

import json
import shutil
import subprocess

import pytest

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

_HAS_FFMPEG = shutil.which("ffmpeg") is not None


def _make_video(path, src: str, secs: float) -> None:  # noqa: ANN001
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"{src}:d={secs}", "-r", "10", "-pix_fmt", "yuv420p", str(path)],
        capture_output=True,
        timeout=60,
        check=True,
    )


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 不在 PATH")
def test_scan_flags_black_video(tmp_path):
    from app.gateway.video_gen.visual_sources.ai_video import _scan_low_quality

    p = tmp_path / "black.mp4"
    _make_video(p, "color=c=black:s=160x120", 3.0)
    out = _scan_low_quality(p, 3.0)
    assert out is not None and "黑帧" in out, "黑视频应命中低质量风险"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 不在 PATH")
def test_scan_passes_normal_video(tmp_path):
    from app.gateway.video_gen.visual_sources.ai_video import _scan_low_quality

    p = tmp_path / "normal.mp4"
    _make_video(p, "testsrc=s=160x120", 3.0)
    assert _scan_low_quality(p, 3.0) is None, "动态测试图不应命中"


@pytest.mark.skipif(not _HAS_FFMPEG, reason="ffmpeg 不在 PATH")
def test_scan_missing_file_none(tmp_path):
    from app.gateway.video_gen.visual_sources.ai_video import _scan_low_quality

    assert _scan_low_quality(tmp_path / "nope.mp4", 3.0) is None


@pytest.mark.asyncio
async def test_render_records_low_quality_sidecar(monkeypatch, tmp_path):
    """接线：扫描命中 -> degradations.json 写 KIND_LOW_VISUAL_QUALITY（只记录不重生）。"""
    import app.gateway.video_gen.visual_sources.ai_video as aiv

    monkeypatch.setattr(aiv, "_scan_low_quality", lambda vp, d: "画面低质量风险（黑帧 2.5s 占 83%）--数据收集中，不自动重生")

    async def fake_localize(url, frame, td, dest_name=None):
        rel = f"visuals/shot_{frame.index:04d}.mp4"
        f = td / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"mp4")
        return rel

    monkeypatch.setattr(aiv, "_localize_video", fake_localize)
    monkeypatch.setattr(aiv, "_probe_video_seconds", lambda p: 3.0)

    class _P:
        async def generate_video(self, prompt: str, **kw):
            return VideoRef(url="http://v/1.mp4", duration=3.0, source="test")

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n", image_prompt="p", duration=3.0)],
    )
    src = aiv.AIVideoVisualSource(_P(), provider_name="seedance")
    await src.render(sb, task_dir=tmp_path)

    data = json.loads((tmp_path / "degradations.json").read_text())["items"]
    hits = [d for d in data if d.get("kind") == "low_visual_quality"]
    assert hits and "黑帧" in hits[0]["detail"], "T-Q1 命中须写 sidecar（数据收集）"
