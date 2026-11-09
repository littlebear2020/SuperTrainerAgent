"""Phase E1: _download_asset 加 UA + 重试 + 类型感知校验。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import video_gen_ai_visual as av  # noqa: E402

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _stub_outbound_guard(monkeypatch):
    """C2 出站守卫与本文件的 UA/校验焦点无关——假域名（http://x/）过不了 DNS，
    桩掉以保持原测试语义；守卫本身由 test_outbound_guard.py 覆盖。"""
    monkeypatch.setattr(av.net_guard, "assert_public_http_url", lambda url: None)


def _resp_factory(data, status=200):
    """返回 _Resp 类：read 第一次返 data，之后返 b""（EOF），让 shutil.copyfileobj 终止。"""

    class _Resp:
        def __init__(self):
            self.status = status
            self._served = False

        def read(self, size=-1):
            if self._served:
                return b""
            self._served = True
            return data

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    return _Resp


def test_download_raises_on_invalid_content(tmp_path, monkeypatch):
    """E1: 下载内容通过 HTTP 但 ffprobe 校验为无效（duration=0）→ raise（不静默黑帧）。"""
    dest = tmp_path / "shot.mp4"
    _Resp = _resp_factory(b"not a video" * 100)  # >512B 但 ffprobe 判无效

    monkeypatch.setattr(av.opener, "open", lambda req, timeout=None: _Resp())
    monkeypatch.setattr(av, "_probe_duration", lambda p: 0.0)  # ffprobe 判无效
    monkeypatch.setattr(av.time, "sleep", lambda s: None)  # 跳过重试等待

    import pytest

    with pytest.raises(RuntimeError, match="download failed"):
        av._download_asset("http://x/bad", dest)
    assert not dest.exists()  # 无效文件被清理


def test_download_succeeds_on_valid_video(tmp_path, monkeypatch):
    """E1: 下载有效视频（ffprobe 时长>0）→ 正常写入，不 raise。"""
    dest = tmp_path / "shot.mp4"
    _Resp = _resp_factory(b"x" * 8192)

    monkeypatch.setattr(av.opener, "open", lambda req, timeout=None: _Resp())
    monkeypatch.setattr(av, "_probe_duration", lambda p: 5.0)  # ffprobe 判有效
    monkeypatch.setattr(av.time, "sleep", lambda s: None)

    av._download_asset("http://x/good", dest)  # 不 raise
    assert dest.exists()


def test_download_sends_user_agent(tmp_path, monkeypatch):
    """E1: 请求带 User-Agent 头（防 CDN 拦截裸请求）。"""
    captured = {}
    _Resp = _resp_factory(b"x" * 8192)

    def _capture(req, timeout=None):
        captured["headers"] = dict(req.header_items())
        return _Resp()

    monkeypatch.setattr(av.opener, "open", _capture)
    monkeypatch.setattr(av, "_probe_duration", lambda p: 5.0)
    monkeypatch.setattr(av.time, "sleep", lambda s: None)

    av._download_asset("http://x/ua", tmp_path / "shot.mp4")
    ua_keys = [k for k in captured["headers"] if k.lower() == "user-agent"]
    assert len(ua_keys) == 1, f"应带 User-Agent，实际 headers={captured['headers']}"


def test_download_png_skips_ffprobe_validation(tmp_path, monkeypatch):
    """E1（评审修正）: 图片目标（.png）不做 ffprobe 时长校验——ffprobe 对静态图返 0，
    一律校验会误杀所有 ai_image 素材。图片只做最小字节校验。"""
    dest = tmp_path / "shot.png"
    _Resp = _resp_factory(b"\x89PNG" + b"x" * 2048)  # 有效大小的图片内容

    probe_calls = {"n": 0}

    def _probe(p):
        probe_calls["n"] += 1
        return 0.0

    monkeypatch.setattr(av.opener, "open", lambda req, timeout=None: _Resp())
    monkeypatch.setattr(av, "_probe_duration", _probe)
    monkeypatch.setattr(av.time, "sleep", lambda s: None)

    av._download_asset("http://x/img", dest)  # 不 raise
    assert dest.exists()
    assert probe_calls["n"] == 0, "图片不应走 ffprobe 校验"
