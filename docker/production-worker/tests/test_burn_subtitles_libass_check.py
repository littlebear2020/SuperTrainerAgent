"""Phase F1: 缺 libass 时 _burn_subtitles 返回明确的中文错误。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as h  # noqa: E402


def test_burn_returns_clear_error_when_libass_missing(tmp_path, monkeypatch):
    """F1: subtitles 滤镜不可用 → 返回明确 libass 缺失错误（非 ffmpeg 笼统报错）。"""
    monkeypatch.setattr(h, "_subtitles_filter_available", lambda: False)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    ok, err = h._burn_subtitles(v, tmp_path / "s.srt", tmp_path / "o.mp4")
    assert ok is False
    assert "libass" in err
    assert "brew" in err or "Docker" in err  # 含明确修复指引


def test_burn_proceeds_when_libass_present(tmp_path, monkeypatch):
    """F1: subtitles 滤镜可用 → 不因检测短路（走原 ffmpeg 路径，ffmpeg 失败仍返 False）。"""
    called = {"ffmpeg": False}

    class _Proc:
        returncode = 1
        stderr = b"some ffmpeg error"

    def _fake_run(cmd, **k):
        called["ffmpeg"] = True
        return _Proc()

    monkeypatch.setattr(h, "_subtitles_filter_available", lambda: True)
    monkeypatch.setattr(h.subprocess, "run", _fake_run)
    v = tmp_path / "v.mp4"
    v.write_bytes(b"x")
    ok, err = h._burn_subtitles(v, tmp_path / "s.srt", tmp_path / "o.mp4")
    assert called["ffmpeg"] is True, "libass 可用时应调用 ffmpeg"
    assert ok is False  # ffmpeg 失败（mock returncode=1）
