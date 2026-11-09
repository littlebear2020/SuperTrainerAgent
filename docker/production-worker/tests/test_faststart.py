"""Task 3f: 所有产物编码出口必须含 -movflags +faststart（Web 边下边播）。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines._ffmpeg_helpers import _get_quality_args  # noqa: E402


def test_quality_args_include_faststart():
    """所有质量档编码参数必须含 -movflags +faststart。"""
    for quality in ("draft", "standard", "high", "preview"):
        args = _get_quality_args(quality)
        assert "-movflags" in args and "+faststart" in args, (
            f"{quality} 缺 faststart: {args}"
        )


def test_convert_format_mp4_relocates_moov_for_faststart(tmp_path, monkeypatch):
    """Bug 1: mp4→mp4 不能短路——须做 moov 重定位（stream copy + faststart）。

    上游 concat 之后的 burn字幕/colorgrade/audio mix/BGM/fade 等重编码步均未带
    faststart，moov 落在尾部 → 最终产物仍不能 Web 边下边播。convert_format 是最后
    出口，mp4 分支须用 `-c copy -movflags +faststart` 重定位 moov（不重编码、不损画质），
    而非短路返回 input。修 Task 3f 对带字幕/调色视频失效的盲区。
    """
    import subprocess

    from pipelines._ffmpeg_helpers import _ffmpeg_convert_format

    captured: list[list[str]] = []

    def fake_run(cmd, *a, **k):
        captured.append(list(cmd))
        Path(cmd[-1]).write_bytes(b"fake-mp4")  # 模拟 ffmpeg 成功写出 output
        return type("P", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr("pipelines._ffmpeg_helpers.subprocess.run", fake_run)

    src = tmp_path / "in.mp4"
    src.write_bytes(b"fake")
    out = tmp_path / "out.mp4"
    result = _ffmpeg_convert_format(src, out, output_format="mp4")

    assert result == out, "mp4 出口应写出 output（moov 重定位），非短路返回 input"
    assert captured, "应调用 ffmpeg 做 moov 重定位，而非短路"
    cmd = captured[0]
    assert "-movflags" in cmd and "+faststart" in cmd, f"mp4 出口须 faststart: {cmd}"
    assert "copy" in cmd, "moov 重定位用 stream copy 不重编码"

