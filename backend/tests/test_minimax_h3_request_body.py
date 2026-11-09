"""minimax-h3 请求体必须带 extra_body.resolution + ratio（2026-08-03 修 100% 400）。

实测（https://api.360.cn/v1/videos/async_generations，真实 key）：

| 请求 | 结果 |
|---|---|
| H3 无 extra_body（修前的 body） | 400 `missing required parameter resolution` |
| H3 有 resolution 缺 ratio | 400 `t2va 场景必须显式指定 ratio` |
| H3 resolution=768P + ratio=16:9 | ✅ |
| H3 resolution=1080P | 400 `does not support 1080P, supported: 768P, 2K` |
| Hailuo 2.3 无 extra_body | ✅（不需要） |

**范围决策**：只对 H3 加 extra_body，不无条件加给所有 qihu 请求。qihu/Hailuo 既是
config 默认 ai_video_provider 又是 seedance-ark 的降级目标（主力出片路径），
而「Hailuo 带 extra_body 也不报错」未经本地复验——不拿主力路径赌未复验结论。
"""

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider


class _CapturingClient:
    """记录 POST body 的假 httpx client（submit 后立刻让 poll 拿到 url）。"""

    def __init__(self) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.is_closed = False

    async def post(self, url: str, **kw: Any) -> MagicMock:  # noqa: ANN401
        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"data": {"id": "task_1"}})

    async def get(self, url: str, **kw: Any) -> MagicMock:  # noqa: ANN401
        # 真实响应用 data.url（不是 video_url）；status 接受 success/succeeded
        return MagicMock(
            status_code=200,
            json=lambda: {"data": {"status": "succeeded", "url": "http://v/1.mp4"}},
        )


def _run(model: str, duration: float = 5.0, **gen_kwargs: Any) -> dict[str, Any]:
    # poll_interval=0：_poll 在首次 GET 前就 sleep，默认 15s 会让每个用例干等 15 秒
    p = QihuVideoProvider(api_key="k", base_url="http://b", model=model, poll_interval=0)
    client = _CapturingClient()
    p._client = client  # type: ignore[assignment]
    asyncio.run(p.generate_video("一只猫", duration=duration, **gen_kwargs))
    return client.bodies[0]


def test_h3_body_includes_resolution_and_ratio() -> None:
    body = _run("minimax/MiniMax-H3", ratio="9:16")
    assert body["extra_body"]["resolution"] == "768P"
    assert body["extra_body"]["ratio"] == "9:16"
    # 顶层结构不变（与 Hailuo 同构，非 happyhorse 的 input/parameters 嵌套）
    assert body["content"][0]["text"] == "一只猫"
    assert body["duration"] == 5


def test_h3_never_requests_1080p() -> None:
    """1080P 会 400（H3 只支持 768P/2K）。"""
    assert _run("minimax/MiniMax-H3")["extra_body"]["resolution"] != "1080P"


def test_h3_ratio_defaults_when_missing() -> None:
    """调用方没传 ratio → 必须兜底，不能缺字段（缺了就 400）。"""
    assert _run("minimax/MiniMax-H3")["extra_body"]["ratio"] == "16:9"


def test_h3_rejects_adaptive_ratio() -> None:
    """_size_to_ratio 对非常规尺寸返回 "adaptive"，它不在 H3 可用值里 → 必须换掉。

    这是评审方案漏掉的一条：直接透传 ratio 会让 1000x1234 这类尺寸继续 400。
    """
    assert _run("minimax/MiniMax-H3", ratio="adaptive")["extra_body"]["ratio"] == "16:9"


@pytest.mark.parametrize("ratio", ["16:9", "4:3", "1:1", "3:4", "9:16", "21:9"])
def test_h3_passes_through_all_supported_ratios(ratio: str) -> None:
    assert _run("minimax/MiniMax-H3", ratio=ratio)["extra_body"]["ratio"] == ratio


def test_hailuo_body_unchanged() -> None:
    """Hailuo 2.3（默认 provider + seedance-ark 降级目标）的 body 必须逐字节不变。

    它不需要 extra_body；加了虽然实测也能过，但那条未经本地复验，
    不拿主力出片路径去赌。
    """
    body = _run("minimax/MiniMax-Hailuo-2.3-no-safety_checker", ratio="9:16")
    assert "extra_body" not in body
    assert set(body) == {"model", "content", "duration"}


def test_ratio_no_longer_swallowed_by_kwargs() -> None:
    """回归锚：ratio 曾被 **kwargs 静默吞掉（调用方一直在传，从未进 body）。"""
    import inspect

    src = inspect.getsource(QihuVideoProvider.generate_video)
    assert 'kwargs.get("ratio")' in src


# ── T1/T2（2026-08-17 H3 duration 400 修复批）：栅格声明 + 向上取整 ──


def test_h3_declares_duration_grid() -> None:
    """T1：H3 网关实锤 supported durations 4s...15s 整数秒（事故 400 报文原文）。

    不声明 -> align_to_provider_grid 对 H3 no-op -> 3.84s 旁白直发 3s -> 400 连环。
    """
    p = QihuVideoProvider(api_key="k", model="minimax/MiniMax-H3")
    assert p.min_video_duration == 4.0
    assert p.integer_seconds_only is True
    assert p.max_video_duration == 15.0


def test_hailuo_keeps_conservative_grid() -> None:
    """T1 锁不连坐：Hailuo 2.3 默认模型无栅格声明，保守默认逐值不变。"""
    p = QihuVideoProvider(api_key="k", model="minimax/MiniMax-Hailuo-2.3-no-safety_checker")
    assert p.min_video_duration == 0.0
    assert p.integer_seconds_only is False
    assert p.max_video_duration == 8.0


def test_submit_ceils_fractional_duration() -> None:
    """T2 变异靶：3.9 必须 ceil 成 4（int() 截断成 3 -> H3 400）。

    主路径有栅格对齐，此取整是防御纵深（vimax bridge 直调 generate_video
    绕过对齐，浮点时长直达 _submit）。
    """
    assert _run("minimax/MiniMax-H3", duration=3.9)["duration"] == 4
    assert _run("minimax/MiniMax-Hailuo-2.3-no-safety_checker", duration=3.9)["duration"] == 4
    assert _run("minimax/MiniMax-H3", duration=4.0)["duration"] == 4, "整数不变"


# ── T3（2026-08-18）：图生视频 i2v 接线——声明+消费+归一三段同修 ──
# 官方实锤（backend/docs/360-api/create-video-generation.md 示例9/10）：
# H3 图生视频 = content[].image_url（无 role），单图=首帧、双图按序=首尾帧；
# url 口径「图片 URL（确保可访问）或图片 Base64 编码」。


def test_h3_supports_image_urls_declared_and_model_gated() -> None:
    """声明按 _is_h3_model 门控——Hailuo 2.3（已下架）不得误声明。"""
    p = QihuVideoProvider(api_key="k", model="minimax/MiniMax-H3")
    assert p.supports_image_urls is True
    hailuo = QihuVideoProvider(api_key="k", model="minimax/MiniMax-Hailuo-2.3-no-safety_checker")
    assert hailuo.supports_image_urls is False


def test_h3_i2v_plural_image_urls_map_to_content_array() -> None:
    """调用方传复数 image_urls（ai_video.py 口径）→ 取首图进 content array
    （无 role，恰合官方 H3 示例9 首帧形态）。修前复数被 **kwargs 静默吞。"""
    body = _run("minimax/MiniMax-H3", image_urls=["http://img/first.png", "http://img/second.png"])
    imgs = [c for c in body["content"] if c.get("type") == "image_url"]
    assert imgs == [{"type": "image_url", "image_url": {"url": "http://img/first.png"}}], f"应只取首图作首帧，实际 {imgs}"


def test_h3_i2v_local_path_normalized_to_data_uri(tmp_path) -> None:
    """本地预览图（绝对路径）必须 data-URI 化——360 远端抓不到本地路径
    （官方口径：url 须为可访问 URL 或 Base64；registry 降级分支同规）。"""
    img = tmp_path / "preview.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    body = _run("minimax/MiniMax-H3", image_urls=[str(img)])
    url = body["content"][1]["image_url"]["url"]
    assert url.startswith("data:image/"), f"本地路径须转 data-URI，实际 {url!r}"
    assert str(img) not in url


def test_h3_submit_video_also_consumes_plural_image_urls(tmp_path) -> None:
    """submit_video（Throttle 分离提交路径）与 generate_video 同口径——只修
    generate_video 会留下第二条静默丢图通道。"""
    img = tmp_path / "preview.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake-bytes")
    p = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)
    client = _CapturingClient()
    p._client = client  # type: ignore[assignment]
    asyncio.run(p.submit_video("一只猫", duration=5.0, image_urls=[str(img)]))
    imgs = [c for c in client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert imgs and imgs[0]["image_url"]["url"].startswith("data:image/")


# ── 2026-09-04 H3 r2v（多图参考）：role="reference" 分派 ──
# 探针实证（temp/h3_reference_probe/RESULTS.md）：360 网关 r2v 唯一正确接法 =
# 全局 extra_body.role="reference"；per-item role 被静默吞掉（T1）、裸发多图 =
# 首尾帧陷阱（T3，禁止）。开关 VIDEO_GEN_H3_R2V 默认开（调用时读），"0" 回退。


def test_h3_r2v_all_images_in_content_with_global_role(monkeypatch) -> None:
    """role="reference" 多图 → 全量进 content[]（无截断）+ extra_body.role，
    与既有 resolution/ratio 合并（三键并存，非覆盖）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    body = _run("minimax/MiniMax-H3", ratio="9:16", role="reference", image_urls=["http://img/char.png", "http://img/prop.png", "http://img/bg.png"])
    imgs = [c for c in body["content"] if c.get("type") == "image_url"]
    assert [i["image_url"]["url"] for i in imgs] == ["http://img/char.png", "http://img/prop.png", "http://img/bg.png"], f"应全量按序下发，实际 {imgs}"
    assert body["extra_body"]["role"] == "reference"
    assert body["extra_body"]["resolution"] == "768P", "r2v 与既有 resolution 合并非覆盖"
    assert body["extra_body"]["ratio"] == "9:16", "r2v 下 ratio 真实生效（探针 T2/T5）"
    assert set(body["extra_body"]) == {"resolution", "ratio", "role"}


def test_h3_r2v_single_image_also_reference(monkeypatch) -> None:
    """单图 role="reference"（无预览图帧只有 1 张合成图 / style_anchor 兜底单图）
    也走 r2v——修「合成图当视频首帧」事故（frame 85 型）的核心分支。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    body = _run("minimax/MiniMax-H3", role="reference", image_urls=["http://img/composite.png"])
    imgs = [c for c in body["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 1 and imgs[0]["image_url"]["url"] == "http://img/composite.png"
    assert body["extra_body"]["role"] == "reference"


def test_h3_r2v_local_paths_normalized_per_image(monkeypatch, tmp_path) -> None:
    """r2v 每张图各自 data-URI 归一（本地路径 360 远端抓不到）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    a, b = tmp_path / "char.png", tmp_path / "prop.png"
    a.write_bytes(b"\x89PNG\r\n\x1a\nfake-a")
    b.write_bytes(b"\x89PNG\r\n\x1a\nfake-b")
    body = _run("minimax/MiniMax-H3", role="reference", image_urls=[str(a), str(b)])
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert len(urls) == 2 and all(u.startswith("data:image/") for u in urls), f"逐张归一，实际 {urls}"
    assert urls[0] != urls[1], "两张图不得归一成同一张"


def test_h3_r2v_flag_off_reverts_to_first_frame(monkeypatch) -> None:
    """VIDEO_GEN_H3_R2V=0 一键回退：role="reference" 也走旧首帧行为、不注 role。"""
    monkeypatch.setenv("VIDEO_GEN_H3_R2V", "0")
    body = _run("minimax/MiniMax-H3", role="reference", image_urls=["http://img/1.png", "http://img/2.png"])
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert urls == ["http://img/1.png"], "开关关 → 只发首图（旧行为）"
    assert "role" not in body["extra_body"]


def test_h3_r2v_non_h3_model_keeps_old_path(monkeypatch) -> None:
    """非 H3 模型（Hailuo）：role="reference" 也维持旧行为（首图 i2v + 无 extra_body）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    body = _run("minimax/MiniMax-Hailuo-2.3-no-safety_checker", role="reference", image_urls=["http://img/1.png", "http://img/2.png"])
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert urls == ["http://img/1.png"]
    assert "extra_body" not in body


def test_h3_first_frame_reference_role_keeps_old_path(monkeypatch) -> None:
    """role="first_frame_reference"（preview_first 混合形态）维持现状：取首图 i2v，
    不走 r2v（官方互斥：首帧不得与参考图混用，探针 T1 实证 360 混发静默退化
    首尾帧）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    body = _run("minimax/MiniMax-H3", role="first_frame_reference", image_urls=["http://img/preview.png", "http://img/char.png"])
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert urls == ["http://img/preview.png"]
    assert "role" not in body["extra_body"]


def test_h3_r2v_submit_video_path(monkeypatch) -> None:
    """submit_video（Throttle 分离提交路径）r2v 同口径——防第二通道静默丢图。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    p = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)
    client = _CapturingClient()
    p._client = client  # type: ignore[assignment]
    asyncio.run(p.submit_video("一只猫", duration=5.0, image_urls=["http://img/a.png", "http://img/b.png"], role="reference"))
    body = client.bodies[0]
    urls = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert urls == ["http://img/a.png", "http://img/b.png"]
    assert body["extra_body"]["role"] == "reference"


def test_h3_r2v_over_six_debug_log_no_truncation(monkeypatch, caplog) -> None:
    """>6 张（超工作点）：只记 debug 不截断（张数防护单点在 apply_ref_cap）。"""
    monkeypatch.delenv("VIDEO_GEN_H3_R2V", raising=False)
    urls = [f"http://img/{i}.png" for i in range(7)]
    with caplog.at_level("DEBUG", logger="app.gateway.video_gen.providers.qihu_video"):
        body = _run("minimax/MiniMax-H3", role="reference", image_urls=urls)
    sent = [c["image_url"]["url"] for c in body["content"] if c.get("type") == "image_url"]
    assert sent == urls, "7 张全量下发（不截断）"
    assert any("exceed working point 6" in r.getMessage() for r in caplog.records), "超工作点 debug 留痕"


# ── 条目 38（2026-09-12 缺陷批）：空帧边界——无图 reference 降级纯 t2v ────────


@pytest.mark.parametrize("empty_image_urls", [None, []])
def test_h3_empty_reference_images_degrades_to_pure_t2v(empty_image_urls: Any) -> None:
    """role=reference 但 image_urls 空（None/[]）→ 纯 t2v 降级。

    锁的是 qihu_video「无图 reference 降级纯 t2v」的刻意语义（:239-240
    ``if role != "reference" or not image_urls: return None``）：图片只经
    content[] 下发，reference 空集时 content 无 image_url 项、extra_body 不注
    role（仅 resolution/ratio 两键）、顶层从无 image_urls 字段。"""
    body = _run("minimax/MiniMax-H3", role="reference", image_urls=empty_image_urls)
    assert all("image_url" not in part for part in body["content"]), "content 不得含 image_url 项"
    assert set(body["extra_body"].keys()) == {"resolution", "ratio"}, "extra_body 不得注 role"
    assert "image_urls" not in body, "顶层从无 image_urls 字段"
    assert body["content"][0]["text"] == "一只猫"  # 纯文本 t2v 照常下发
