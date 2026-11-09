"""Phase D1: ai_video 成功镜本地化——生成后立即下载到 local_visual_path。"""

import pytest


class _Frame:
    def __init__(self, index=0):
        self.index = index


def _mock_client(resp_content, status=200):
    """构造 monkeypatch httpx.AsyncClient 用的假 client。"""
    import httpx

    class _Resp:
        def __init__(self, content, status):
            self.content = content
            self.status_code = status

        def raise_for_status(self):
            if status >= 400:
                raise httpx.HTTPStatusError(f"{status}", request=None, response=self)

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None):
            return _Resp(resp_content, status)

    return _Client


@pytest.mark.asyncio
async def test_localize_downloads_and_returns_rel(tmp_path, monkeypatch):
    """D1: 下载有效视频（≥1KB）→ 写 visuals/shot_NNNN.mp4，返回相对路径。"""
    import httpx

    from app.gateway.video_gen.visual_sources import ai_video as av

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(b"x" * 4096))
    rel = await av._localize_video("http://x/v.mp4", _Frame(0), tmp_path)
    assert rel == "visuals/shot_0000.mp4"
    assert (tmp_path / rel).exists()
    assert (tmp_path / rel).stat().st_size >= 4096


@pytest.mark.asyncio
async def test_localize_returns_none_on_too_small(tmp_path, monkeypatch):
    """D1: 下载内容 <1KB（错误页/空）→ 重试耗尽返回 None（保留 remote url）。"""
    import httpx

    from app.gateway.video_gen.visual_sources import ai_video as av

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(b"tiny"))
    monkeypatch.setattr(av, "_LOCALIZE_RETRY_DELAYS", [0, 0, 0])  # 加速测试
    rel = await av._localize_video("http://x/bad", _Frame(0), tmp_path)
    assert rel is None


@pytest.mark.asyncio
async def test_localize_returns_none_on_http_error(tmp_path, monkeypatch):
    """D1: HTTP 5xx（raise_for_status）→ 重试耗尽返回 None。"""
    import httpx

    from app.gateway.video_gen.visual_sources import ai_video as av

    monkeypatch.setattr(httpx, "AsyncClient", _mock_client(b"x" * 4096, status=503))
    monkeypatch.setattr(av, "_LOCALIZE_RETRY_DELAYS", [0, 0, 0])
    rel = await av._localize_video("http://x/err", _Frame(0), tmp_path)
    assert rel is None


@pytest.mark.asyncio
async def test_render_sets_local_visual_path_on_success(tmp_path, monkeypatch):
    """D1: render() 成功生成分支后 frame.local_visual_path 被设、visual_url 保留。"""
    from app.gateway.video_gen.providers.base import VideoRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources import ai_video as av
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    class _Provider:
        supports_image_urls = False

        async def generate_video(self, prompt, duration=8.0, image_urls=None, **kwargs):
            return VideoRef(url="http://x/v.mp4", duration=duration, source="test")

    async def _fake_localize(url, frame, task_dir):
        vd = task_dir / "visuals"
        vd.mkdir(parents=True, exist_ok=True)
        rel = f"visuals/shot_{frame.index:04d}.mp4"
        (task_dir / rel).write_bytes(b"x" * 4096)
        return rel

    monkeypatch.setattr(av, "_localize_video", _fake_localize)

    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    frame = StoryboardFrame(index=0, narration="n", duration=4.0)
    sb = Storyboard(title="t", config=cfg, frames=[frame])

    src = AIVideoVisualSource(_Provider(), concurrency=2)
    await src.render(sb, task_dir=tmp_path)
    assert frame.local_visual_path == "visuals/shot_0000.mp4"
    assert frame.visual_url == "http://x/v.mp4"
