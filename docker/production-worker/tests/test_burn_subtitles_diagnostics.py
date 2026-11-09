"""W2: _burn_subtitles 失败时返回 ffmpeg stderr 末行（而非笼统"see worker logs"）。"""


def test_burn_subtitles_returns_stderr_tail_on_failure(tmp_path, monkeypatch):
    """W2: 烧录失败 → 返回 (False, stderr末行)，供上层写进 subtitle_status。"""
    import pipelines._ffmpeg_helpers as h

    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n0-->1\nhi\n")
    out = tmp_path / "out.mp4"

    class _Proc:
        returncode = 1
        stderr = b"Error initializing filterchain: No such filter: 'subtitles'\n"

    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(h, "_subtitles_filter_available", lambda: True)  # F1: 走 ffmpeg 路径
    ok, err = h._burn_subtitles(video, srt, out)
    assert ok is False
    assert "No such filter" in err or "subtitles" in err, f"error 须含 ffmpeg stderr 末行：{err}"


def test_burn_subtitles_success_returns_empty_error(tmp_path, monkeypatch):
    """W2: 成功 → (True, '')。"""
    import pipelines._ffmpeg_helpers as h

    video = tmp_path / "v.mp4"
    video.write_bytes(b"mp4")
    srt = tmp_path / "s.srt"
    srt.write_text("1\n0-->1\nhi\n")
    out = tmp_path / "out.mp4"

    class _Proc:
        returncode = 0
        stderr = b""

    def _run(*a, **k):
        out.write_bytes(b"mp4")
        return _Proc()

    monkeypatch.setattr(h.subprocess, "run", _run)
    monkeypatch.setattr(h, "_subtitles_filter_available", lambda: True)  # F1: 走 ffmpeg 路径
    ok, err = h._burn_subtitles(video, srt, out)
    assert ok is True
    assert err == ""
