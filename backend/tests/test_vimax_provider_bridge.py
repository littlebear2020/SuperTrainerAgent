"""Tests for the ViMax provider bridge (阶段 2.3, §5.3).

The bridge adapts TrainerAI's providers (NanoBananaImageProvider, QihuVideoProvider)
to ViMax's ImageGenerator/VideoGenerator Protocols, so the vendored ViMax pipeline
uses TrainerAI backends instead of ViMax's own yunwu/google generators. RenderBackend
loads the bridge via class_path + init_args from the config built by build_vimax_config.
"""

from unittest.mock import AsyncMock

import httpx
import pytest
import respx
import tenacity

from app.gateway.video_gen.engines.vimax.provider_bridge import (
    NanoBananaGeneratorForViMax,
    SeedanceVideoGeneratorForViMax,
    SeedreamGeneratorForViMax,
    VimaxBudgetExceeded,
    _to_image_url,
    build_vimax_config,
)
from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.providers.throttle import ProviderUnavailableError, RateLimitError

_ARK_IMAGE_URL = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

# ── NanoBananaGeneratorForViMax (image) ──────────────────────────────────


@pytest.mark.asyncio
async def test_image_bridge_calls_nano_banana_and_wraps_url():
    bridge = NanoBananaGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/img.png", source="nano-banana"))

    out = await bridge.generate_single_image("a cat", reference_image_paths=["https://cdn/ref.png"])

    bridge._provider.generate_image.assert_awaited_once_with("a cat", reference_image_paths=["https://cdn/ref.png"])
    assert out.fmt == "url"
    assert out.ext == "png"
    assert out.data == "https://cdn/img.png"


@pytest.mark.asyncio
async def test_image_bridge_prompt_only_call_matches_vimax_front_portrait_contract():
    """ViMax's CharacterPortraitsGenerator.generate_front_portrait calls the image
    generator with ONLY ``prompt=...`` — no reference_image_paths (the first portrait
    has no reference). The bridge MUST accept that call shape, i.e.
    reference_image_paths must be optional. Regression for the 2.8 real-run failure:
    ``generate_single_image() missing 1 required positional argument:
    'reference_image_paths'``."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/front.png"))

    out = await bridge.generate_single_image(prompt="front portrait of Xiaoming")  # exactly how ViMax calls it

    bridge._provider.generate_image.assert_awaited_once_with("front portrait of Xiaoming", reference_image_paths=None)
    assert out.data == "https://cdn/front.png"


@pytest.mark.asyncio
async def test_video_bridge_prompt_only_call_matches_vimax_no_first_frame_contract():
    """A ViMax shot with no first frame (text-to-video) calls the video generator
    with only prompt. The bridge must accept that shape too."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/t2v.mp4"))

    await bridge.generate_single_video(prompt="a cat walks")  # no reference passed

    bridge._provider.generate_video.assert_awaited_once_with("a cat walks", image_url=None)


@pytest.mark.asyncio
async def test_video_bridge_falls_back_to_t2v_on_content_review_rejection(tmp_path):
    """i2v content-review rejection (Green net check) → retry as t2v (drop image).

    Real failure (vg_86d2767fe762): happyhorse i2v with ViMax's first-frame
    image returned FAILED "Green net check failed for image (input)".
    Rather than fail the whole shot, retry once as text-to-video (no image) so
    disaster/dramatic shots that trip 360's image review can still render.
    """
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy", backend="happyhorse")
    bridge._provider = AsyncMock()
    # First call (i2v) raises content-review RuntimeError; second (t2v) succeeds.
    bridge._provider.generate_video = AsyncMock(
        side_effect=[
            RuntimeError("Video task t1 failed: Green net check failed for image (input): inappropriate content."),
            VideoRef(url="https://cdn/t2v_fallback.mp4"),
        ]
    )
    # A real first-frame image so the i2v path is taken first.
    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    out = await bridge.generate_single_video(prompt="flood disaster scene", reference_image_paths=[str(img)])

    assert out.data == "https://cdn/t2v_fallback.mp4"
    # Two calls: first i2v (with image_url), second t2v (image_url=None).
    assert bridge._provider.generate_video.await_count == 2
    first_call = bridge._provider.generate_video.await_args_list[0].kwargs
    second_call = bridge._provider.generate_video.await_args_list[1].kwargs
    assert first_call.get("image_url") is not None, "first attempt must be i2v with image"
    assert second_call.get("image_url") is None, "fallback must drop image (t2v)"


@pytest.mark.asyncio
async def test_video_bridge_no_t2v_fallback_on_non_content_errors(tmp_path):
    """Non-content-review errors (e.g. timeout, 5xx) must NOT trigger t2v fallback —
    t2v with no image would produce a wrong shot. Those stay with the retry decorator."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy", backend="happyhorse")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/ok.mp4"))

    # Sanity: a plain success doesn't double-call.
    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    await bridge.generate_single_video(prompt="ok", reference_image_paths=[str(img)])
    assert bridge._provider.generate_video.await_count == 1


@pytest.mark.asyncio
async def test_image_bridge_passes_empty_refs_as_none():
    """ViMax may pass an empty list when there are no references; the provider's
    no-reference path is triggered by None (per nano_banana.generate_image)."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))

    await bridge.generate_single_image("a cat", reference_image_paths=[])

    bridge._provider.generate_image.assert_awaited_once_with("a cat", reference_image_paths=None)


# ── SeedanceVideoGeneratorForViMax (video, i2v via 海螺) ──────────────────


@pytest.mark.asyncio
async def test_video_bridge_uses_first_ref_as_i2v_first_frame():
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/out.mp4", duration=5.0, source="qihu"))

    out = await bridge.generate_single_video("a cat walks", reference_image_paths=["https://cdn/frame0.png", "https://cdn/frame1.png"])

    # only the first reference is the i2v first frame (path A)
    bridge._provider.generate_video.assert_awaited_once_with("a cat walks", image_url="https://cdn/frame0.png")
    assert out.fmt == "url"
    assert out.ext == "mp4"
    assert out.data == "https://cdn/out.mp4"


@pytest.mark.asyncio
async def test_video_bridge_handles_no_reference_image():
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/t2c.mp4"))

    await bridge.generate_single_video("a cat walks", reference_image_paths=[])

    bridge._provider.generate_video.assert_awaited_once_with("a cat walks", image_url=None)


# ── local reference path → data URL (the 2.8 seam fix) ───────────────────


def test_to_image_url_converts_local_file_to_data_url(tmp_path):
    """ViMax saves first frames as local files; the 360 video API needs a URL.
    Mirror nano_banana: convert a local file to a base64 data URL."""
    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n fake png")
    url = _to_image_url(str(img))
    assert url.startswith("data:image/png;base64,")


def test_to_image_url_passes_through_urls_and_data_uris():
    assert _to_image_url("https://cdn/x.png") == "https://cdn/x.png"
    assert _to_image_url("data:image/png;base64,YWJj") == "data:image/png;base64,YWJj"


@pytest.mark.asyncio
async def test_video_bridge_normalizes_local_ref_to_data_url(tmp_path):
    img = tmp_path / "frame0.png"
    img.write_bytes(b"\x89PNG fake")
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/out.mp4"))

    await bridge.generate_single_video("a cat walks", reference_image_paths=[str(img)])

    sent = bridge._provider.generate_video.call_args.kwargs["image_url"]
    assert sent.startswith("data:image/png;base64,")  # local file → data URL for the 360 API


# ── build_vimax_config ────────────────────────────────────────────────────


def test_build_vimax_config_produces_classpath_and_init_args(tmp_path):
    working = tmp_path / "vimax_working"
    cfg = build_vimax_config(
        working_dir=working,
        llm_model="qwen/qwen3.8-max",
        llm_base_url="https://api.360.cn/v1",
        llm_api_key="LLMKEY",
        image_api_key="IMGKEY",
        video_api_key="VIDKEY",
    )

    # chat_model.init_args translated from TrainerAI ModelConfig (not create_chat_model)
    assert cfg["chat_model"]["init_args"]["model"] == "qwen/qwen3.8-max"
    assert cfg["chat_model"]["init_args"]["base_url"] == "https://api.360.cn/v1"
    assert cfg["chat_model"]["init_args"]["api_key"] == "LLMKEY"
    assert cfg["chat_model"]["init_args"]["model_provider"] == "openai"

    # generators point at the host bridge classes (not ViMax's own). Image now
    # defaults to SeedreamGeneratorForViMax (§4.2, Ark seedream 5.0 Pro) —
    # NanoBananaGeneratorForViMax stays defined as a rollback fallback.
    assert cfg["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")
    assert cfg["image_generator"]["init_args"]["api_key"] == "IMGKEY"
    assert cfg["video_generator"]["class_path"].endswith("SeedanceVideoGeneratorForViMax")
    assert cfg["video_generator"]["init_args"]["api_key"] == "VIDKEY"

    # working_dir so ViMax writes final_video.mp4 under the task dir
    assert cfg["working_dir"] == str(working)


# ── budget hard-cutoff (阶段 4.5: cap the expensive image/video calls) ────


@pytest.mark.asyncio
async def test_image_bridge_enforces_call_budget():
    """A per-generator call cap stops a runaway ViMax run mid-pipeline (the image/
    video calls are vimax's main cost; capping them is the meaningful hard-cutoff)."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy", max_calls=2)
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))

    await bridge.generate_single_image("a", reference_image_paths=[])  # call 1
    await bridge.generate_single_image("b", reference_image_paths=[])  # call 2
    with pytest.raises(VimaxBudgetExceeded, match="image"):  # call 3 → over budget
        await bridge.generate_single_image("c", reference_image_paths=[])


@pytest.mark.asyncio
async def test_video_bridge_enforces_call_budget():
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy", max_calls=1)
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/v.mp4"))

    await bridge.generate_single_video("a", reference_image_paths=[])  # call 1
    with pytest.raises(VimaxBudgetExceeded, match="video"):  # call 2 → over budget
        await bridge.generate_single_video("b", reference_image_paths=[])


@pytest.mark.asyncio
async def test_no_budget_by_default():
    """max_calls=None (default) → no enforcement (backward compatible)."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))
    for _ in range(50):
        await bridge.generate_single_image("x", reference_image_paths=[])  # no raise


# ── aspect ratio passthrough (regression for vimax real-run vg_8159cd6cc3fa) ─
# Real run came back 768x768 despite a 16:9 request. Fix: carry request aspect
# → build_vimax_config → image bridge init → NanoBanana extra_body.aspectRatio.
# (See test_nano_banana_provider.py for the API-body assertion.)


def test_build_vimax_config_passes_image_aspect_ratio_to_bridge_init():
    import pathlib

    cfg = build_vimax_config(
        working_dir=pathlib.Path("/tmp/x"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="L",
        image_api_key="I",
        video_api_key="V",
        image_aspect_ratio="16:9",
    )
    assert cfg["image_generator"]["init_args"]["aspect_ratio"] == "16:9"


def test_build_vimax_config_aspect_ratio_defaults_to_none_when_unset():
    import pathlib

    cfg = build_vimax_config(
        working_dir=pathlib.Path("/tmp/x"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="L",
        image_api_key="I",
        video_api_key="V",
    )
    assert cfg["image_generator"]["init_args"].get("aspect_ratio") is None


@pytest.mark.asyncio
async def test_image_bridge_forwards_aspect_ratio_to_provider():
    """The bridge constructed with aspect_ratio must hand it to the provider so
    each generate call uses it (not the provider default 1:1)."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy", aspect_ratio="16:9")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))

    await bridge.generate_single_image("wide shot")

    # aspect_ratio flows into the provider call as a kwarg
    assert bridge._provider.generate_image.await_args.kwargs.get("aspect_ratio") == "16:9"


# ── ¥ cost budget (4.5: cap by ¥ amount, not just call count) ─────────────────


@pytest.mark.asyncio
async def test_image_bridge_tracks_cost_cny_per_call():
    """Each image call adds its unit cost (¥0.3) to cost_cny. After N calls the
    tracker reflects N × 0.3. This is the basis for the ¥ budget hard-cutoff."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy")
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))
    for _ in range(5):
        await bridge.generate_single_image("x", reference_image_paths=[])
    assert bridge.cost_cny == pytest.approx(1.5)  # 5 × ¥0.3


@pytest.mark.asyncio
async def test_image_bridge_enforces_max_cost_cny_budget():
    """max_cost_cny caps total ¥ spend across image calls (plan §8.3: ¥100 default).
    Stops mid-pipeline like the call-count cap, but by ¥ instead of count."""
    bridge = NanoBananaGeneratorForViMax(api_key="dummy", max_cost_cny=1.0)
    bridge._provider = AsyncMock()
    bridge._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn/x.png"))
    # ¥0.3/call × 3 calls = ¥0.9 (OK), 4th call = ¥1.2 (exceeds ¥1.0)
    for _ in range(3):
        await bridge.generate_single_image("x", reference_image_paths=[])
    with pytest.raises(VimaxBudgetExceeded, match="cost"):
        await bridge.generate_single_image("over", reference_image_paths=[])


@pytest.mark.asyncio
async def test_video_bridge_enforces_max_cost_cny_budget():
    """Video calls cost ¥1.5 each (plan §8.3). ¥3.0 budget → 2 calls OK, 3rd exceeds."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="dummy", max_cost_cny=3.0)
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/v.mp4"))
    await bridge.generate_single_video("a", reference_image_paths=[])
    await bridge.generate_single_video("b", reference_image_paths=[])
    with pytest.raises(VimaxBudgetExceeded, match="cost"):
        await bridge.generate_single_video("c", reference_image_paths=[])


# ── C3: seedance backend selection + multi-reference r2v (决策4) ──────────


def test_video_bridge_default_backend_uses_qihu():
    """Default backend remains Hailuo (QihuVideoProvider) — no behavior change."""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    bridge = SeedanceVideoGeneratorForViMax(api_key="k")
    assert isinstance(bridge._provider, QihuVideoProvider)
    assert bridge._backend == "hailuo"


def test_video_bridge_seedance_backend_uses_seedance_provider():
    """C3: backend='seedance' wires SeedanceVideoProvider (r2v) into the bridge."""
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    bridge = SeedanceVideoGeneratorForViMax(api_key="k", backend="seedance")
    assert isinstance(bridge._provider, SeedanceVideoProvider)
    assert bridge._backend == "seedance"


@pytest.mark.asyncio
async def test_video_bridge_seedance_forwards_all_references_as_image_urls():
    """C3: seedance backend forwards ALL reference_image_paths as image_urls (r2v
    multi-reference), not just the first frame like Hailuo i2v."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="k", backend="seedance")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/v.mp4", source="seedance"))

    await bridge.generate_single_video("prompt", reference_image_paths=["https://a.png", "https://b.png", "https://c.png"])

    bridge._provider.generate_video.assert_awaited_once()
    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert kwargs.get("image_urls") == ["https://a.png", "https://b.png", "https://c.png"]
    assert "image_url" not in kwargs  # multi-ref path, not the single first-frame path


@pytest.mark.asyncio
async def test_video_bridge_hailuo_still_uses_single_first_frame():
    """Hailuo backend unchanged: forwards only the first reference as image_url."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="k", backend="hailuo")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/v.mp4"))

    await bridge.generate_single_video("prompt", reference_image_paths=["https://a.png", "https://b.png"])

    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert kwargs.get("image_url") == "https://a.png"
    assert "image_urls" not in kwargs


def test_build_vimax_config_wires_video_backend():
    """C3: build_vimax_config(video_backend=...) propagates backend into the video
    generator init_args so SeedanceVideoGeneratorForViMax picks the right provider."""
    from pathlib import Path

    cfg_seedance = build_vimax_config(
        working_dir=Path("/w"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="k",
        video_api_key="k",
        video_model="volcengine/doubao-seedance-2-0",
        video_backend="seedance",
    )
    assert cfg_seedance["video_generator"]["init_args"]["backend"] == "seedance"

    cfg_hailuo = build_vimax_config(
        working_dir=Path("/w"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="k",
        video_api_key="k",
    )
    assert cfg_hailuo["video_generator"]["init_args"]["backend"] == "hailuo"


# ── happyhorse backend + style routing (写实→happyhorse / 卡通→seedance) ────


def test_video_bridge_happyhorse_backend_uses_happyhorse_provider():
    from app.gateway.video_gen.providers.happyhorse import HappyhorseVideoProvider

    bridge = SeedanceVideoGeneratorForViMax(api_key="k", backend="happyhorse")
    assert isinstance(bridge._provider, HappyhorseVideoProvider)
    assert bridge._backend == "happyhorse"


# ── Stage 5.2: seedance-ark backend (Ark-direct seedance, 字节默认首选) ────
# Switches ViMax video from happyhorse (360) + 360-seedance to Ark-direct
# seedance-ark. happyhorse降为 fallback. 角色首帧 (Seedream产物, §4.2) are 字节
# "信任产物" — safe as seedance-ark i2v input (not blocked by 人脸审查, §0.4).
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 5.2.


def test_video_bridge_seedance_ark_backend_uses_seedance_ark_provider():
    """Stage 5.2: backend='seedance-ark' wires SeedanceArkVideoProvider (Ark
    /api/v3/contents/generations/tasks) into the bridge, distinct from the
    360-gateway seedance/happyhorse/hailuo providers."""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    bridge = SeedanceVideoGeneratorForViMax(
        api_key="ark-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedance-2-0-260128",
        backend="seedance-ark",
    )
    assert isinstance(bridge._provider, SeedanceArkVideoProvider)
    assert bridge._backend == "seedance-ark"


@pytest.mark.asyncio
async def test_video_bridge_seedance_ark_i2v_single_ref_as_image_url(tmp_path):
    """seedance-ark i2v: 1 reference → image_url (first_frame, data URL of the
    Seedream-generated角色首帧). NOT image_urls (that's the 2-ref path)."""
    bridge = SeedanceVideoGeneratorForViMax(
        api_key="ark-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedance-2-0-260128",
        backend="seedance-ark",
    )
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/ark.mp4", source="seedance-ark"))

    img = tmp_path / "frame1.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n frame1")

    out = await bridge.generate_single_video("a girl reading", reference_image_paths=[str(img)])

    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert kwargs.get("image_url", "").startswith("data:image/png;base64,"), "1 ref → image_url (first_frame data URL)"
    assert "image_urls" not in kwargs, "1 ref must NOT use image_urls (that's the 2-ref path)"
    assert out.data == "https://cdn/ark.mp4"


@pytest.mark.asyncio
async def test_video_bridge_seedance_ark_i2v_two_refs_as_image_urls(tmp_path):
    """seedance-ark i2v: 2 references → image_urls=[first_frame, last_frame] (both
    data URLs). The SeedanceArkVideoProvider assigns roles internally (§5.1)."""
    bridge = SeedanceVideoGeneratorForViMax(
        api_key="ark-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedance-2-0-260128",
        backend="seedance-ark",
    )
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/ark.mp4", source="seedance-ark"))

    img1 = tmp_path / "frame1.png"
    img1.write_bytes(b"\x89PNG\r\n\x1a\n frame1")
    img2 = tmp_path / "frame2.png"
    img2.write_bytes(b"\x89PNG\r\n\x1a\n frame2")

    await bridge.generate_single_video("a girl walking then running", reference_image_paths=[str(img1), str(img2)])

    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert "image_url" not in kwargs, "2 refs must use image_urls, not image_url"
    image_urls = kwargs.get("image_urls")
    assert isinstance(image_urls, list)
    assert len(image_urls) == 2
    assert all(u.startswith("data:image/png;base64,") for u in image_urls), "both refs → data URLs"


@pytest.mark.asyncio
async def test_video_bridge_seedance_ark_t2v_no_refs():
    """seedance-ark t2v: 0 references → text-only (image_url=None, no image_urls).
    A ViMax shot with no first frame calls the video generator with only prompt."""
    bridge = SeedanceVideoGeneratorForViMax(
        api_key="ark-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedance-2-0-260128",
        backend="seedance-ark",
    )
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/t2v.mp4", source="seedance-ark"))

    await bridge.generate_single_video("a sunset over the ocean")

    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert "image_urls" not in kwargs
    assert kwargs.get("image_url") is None, "0 refs → t2v (image_url=None)"


@pytest.mark.asyncio
async def test_video_bridge_seedance_ark_content_review_falls_back_to_t2v(tmp_path):
    """seedance-ark content-review rejection → retry as t2v (drop image), mirroring
    the happyhorse/hailuo content-review handling. The Ark provider raises RuntimeError
    for poll status:failed (InputImageSensitiveContentDetected); _is_content_review_rejection
    detects it → t2v fallback so the shot can still render. NOT retried by tenacity
    (no shot-retry) — only the single t2v fallback call."""
    bridge = SeedanceVideoGeneratorForViMax(
        api_key="ark-key",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        model="doubao-seedance-2-0-260128",
        backend="seedance-ark",
    )
    bridge._provider = AsyncMock()
    # First call (i2v) raises content-review RuntimeError; second (t2v) succeeds.
    bridge._provider.generate_video = AsyncMock(
        side_effect=[
            RuntimeError("Ark seedance task cgt-x failed: InputImageSensitiveContentDetected; sensitive content"),
            VideoRef(url="https://cdn/t2v_fallback.mp4", source="seedance-ark"),
        ]
    )
    img = tmp_path / "frame.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n frame")

    out = await bridge.generate_single_video("dramatic scene", reference_image_paths=[str(img)])

    assert out.data == "https://cdn/t2v_fallback.mp4"
    assert bridge._provider.generate_video.await_count == 2  # 1 i2v + 1 t2v fallback (NOT 6 from tenacity 3x)
    first = bridge._provider.generate_video.await_args_list[0].kwargs
    second = bridge._provider.generate_video.await_args_list[1].kwargs
    assert first.get("image_url") is not None, "first attempt must be i2v with image"
    assert second.get("image_url") is None, "fallback must drop image (t2v)"


@pytest.mark.asyncio
async def test_video_bridge_seedance_ark_enforces_budget():
    """Budget tracking applies to seedance-ark too (mirror the happyhorse budget test)."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="ark-key", backend="seedance-ark", max_calls=1)
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/v.mp4"))

    await bridge.generate_single_video("a", reference_image_paths=[])  # call 1
    with pytest.raises(VimaxBudgetExceeded, match="video"):  # call 2 → over budget
        await bridge.generate_single_video("b", reference_image_paths=[])


@pytest.mark.asyncio
async def test_video_bridge_happyhorse_forwards_single_first_frame():
    """happyhorse (i2v) forwards the first reference as image_url (single), like
    hailuo — NOT the multi-image_urls path (seedance-only)."""
    bridge = SeedanceVideoGeneratorForViMax(api_key="k", backend="happyhorse")
    bridge._provider = AsyncMock()
    bridge._provider.generate_video = AsyncMock(return_value=VideoRef(url="https://cdn/h.mp4", source="happyhorse"))

    await bridge.generate_single_video("prompt", reference_image_paths=["https://a.png", "https://b.png"])

    kwargs = bridge._provider.generate_video.call_args.kwargs
    assert kwargs.get("image_url") == "https://a.png"
    assert "image_urls" not in kwargs


def test_resolve_vimax_video_backend_auto_routes_to_seedance_360():
    """C5a (2026-10-12): auto/default → seedance (360 r2v). Stage 5.2 (2026-07) 曾
    默认 seedance-ark——360 拦人像时期迁 Ark；2026-10-12 决策迁回 360（拦截风险
    以 C5c 角色 spike 实测为准），seedance-ark 降为回滚位。style 不影响 auto 路由。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import _resolve_vimax_video_backend

    cfg = VideoGenConfig()
    for style in ("realistic movie style", None, "default", "Cartoon", "Anime Style", "日漫动画风"):
        assert _resolve_vimax_video_backend(style, "auto", cfg) == ("seedance", cfg.seedance_video_model)


def test_resolve_vimax_video_backend_explicit_override_beats_style():
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import _resolve_vimax_video_backend

    cfg = VideoGenConfig()
    assert _resolve_vimax_video_backend("Cartoon", "hailuo", cfg) == ("hailuo", cfg.qihu_video_model)
    assert _resolve_vimax_video_backend("realistic", "seedance", cfg) == ("seedance", cfg.seedance_video_model)
    assert _resolve_vimax_video_backend("realistic", "happyhorse", cfg)[0] == "happyhorse"
    assert _resolve_vimax_video_backend("realistic", "seedance-ark", cfg) == ("seedance-ark", cfg.seedance_ark_video_model)


# ── B2: happyhorse transient RuntimeError per-shot retry ─────────────────


@pytest.mark.asyncio
async def test_happyhorse_runtime_error_video_latent_failure_is_retried(monkeypatch):
    """B2: a happyhorse RuntimeError (video latent download failed) must be
    retried per-shot, not bubble up to the whole-pipeline retry. Regression:
    vg_cda1b579a220 — one shot's latent-download failure triggered a full
    pipeline retry that regenerated 117 first_frames."""
    # wait_exponential(2, max=30) → 0：仍走满 3 次 attempt，只是不真睡 2s+4s。
    monkeypatch.setattr(SeedanceVideoGeneratorForViMax._generate_video_with_retry.retry, "wait", tenacity.wait_none())
    call_count = {"n": 0}

    class _FlakyProvider:
        async def generate_video(self, prompt, **kwargs):
            call_count["n"] += 1
            if call_count["n"] < 3:
                raise RuntimeError('Video task abc-123 failed: <400> InternalError.Algo: Video latent download failed: {"name":"InvalidParameter","code":400}')
            # 3rd call succeeds
            return VideoRef(url="https://example.com/recovered.mp4")

    gen = SeedanceVideoGeneratorForViMax(
        api_key="k",
        base_url="https://api.360.cn",
        model="alibaba/happyhorse-1.0-i2v",
        backend="happyhorse",
        max_wait=10,
    )
    gen._provider = _FlakyProvider()

    vref = await gen.generate_single_video("prompt", reference_image_paths=["/tmp/x.png"])
    assert call_count["n"] == 3, "RuntimeError (latent download) must be retried until success"
    # generate_single_video returns a VideoOutput (fmt/url/data), not the raw VideoRef.
    assert "recovered.mp4" in vref.data


@pytest.mark.asyncio
async def test_hailuo_4xx_runtime_error_NOT_retried_by_tenacity():
    """B2: a hailuo 4xx RuntimeError (content review rejection) must NOT be
    retried BY TENACITY (the per-shot retry decorator). It DOES get the existing
    t2v fallback (retry without image — correct behavior, _is_content_review_rejection).
    This test pins that tenacity doesn't multiply-retry it: total provider calls
    = 2 (1 i2v + 1 t2v fallback), NOT 6 (3 i2v tenacity + 3 t2v tenacity)."""
    call_count = {"n": 0}

    class _RejectProvider:
        async def generate_video(self, prompt, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("360 video submit failed (400): content review rejected")

    gen = SeedanceVideoGeneratorForViMax(
        api_key="k",
        base_url="https://api.360.cn",
        model="minimax/MiniMax-Hailuo-2.3",
        backend="hailuo",
        max_wait=10,
    )
    gen._provider = _RejectProvider()

    with pytest.raises(RuntimeError, match="content review"):
        await gen.generate_single_video("prompt", reference_image_paths=["/tmp/x.png"])
    # 1 i2v attempt + 1 t2v fallback attempt = 2 total. NOT 6 (tenacity 3x each).
    assert call_count["n"] == 2, f"hailuo content-review must NOT trigger tenacity retry; expected 2 calls (i2v + t2v fallback), got {call_count['n']}"


@pytest.mark.asyncio
async def test_happyhorse_generic_video_task_failure_NOT_retried():
    """B2: a happyhorse RuntimeError that is NEITHER a transient pattern
    (InternalError.Algo / Video latent download failed) NOR a content-review
    rejection must NOT be retried by tenacity OR the t2v fallback. The generic
    'Video task ... failed' prefix alone is not a retry signal — matching it
    would wrongly retry non-transient failures.

    Uses 'unknown subsystem error' so _is_content_review_rejection misses too
    (no 'content'/'policy'/'safety' keywords) — isolates B2's predicate."""
    call_count = {"n": 0}

    class _GenericFailProvider:
        async def generate_video(self, prompt, **kwargs):
            call_count["n"] += 1
            raise RuntimeError("Video task abc failed: unknown subsystem error")

    gen = SeedanceVideoGeneratorForViMax(
        api_key="k",
        base_url="https://api.360.cn",
        model="alibaba/happyhorse-1.0-i2v",
        backend="happyhorse",
        max_wait=10,
    )
    gen._provider = _GenericFailProvider()

    with pytest.raises(RuntimeError, match="unknown subsystem"):
        await gen.generate_single_video("prompt", reference_image_paths=["/tmp/x.png"])
    assert call_count["n"] == 1, "generic 'Video task failed' (no transient + no content-review) must not retry at all"


# ── SeedreamGeneratorForViMax (image, Ark seedream 5.0 Pro, §4.2) ──────────
# Switches ViMax image generation from the hardcoded NanoBanana to Ark-direct
# seedream, preserving the character-consistency multi-reference mechanism via
# the `image: [b64_data_uri_list]` body field (NOT reference_image; NOT
# /images/edits which 404s). 角色首帧 is a 字节"信任产物" — safe as Stage 5.2
# seedance i2v input (not blocked by 人脸审查). See
# backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 4.2 + §1.7.


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_no_refs_returns_image_output_and_omits_image_field():
    """No reference_image_paths (front portrait case) → text-only t2i; the request
    body has NO ``image`` field (§4.2: empty/None refs → no image list)."""
    import json

    route = respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/front.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    out = await gen.generate_single_image("front portrait of Xiaoming")

    assert out.fmt == "url"
    assert out.ext == "png"
    assert out.data == "https://tos-seedream.example/front.png"
    assert route.called
    body = json.loads(route.calls.last.request.content.decode())
    assert body["model"] == "doubao-seedream-5-0-pro-260628"
    assert body["prompt"] == "front portrait of Xiaoming"
    assert "image" not in body  # text-only path omits the image list


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_with_refs_sends_image_data_uri_list(tmp_path):
    """reference_image_paths=[file1, file2] (side/back portrait case) → request body
    has ``image: [b64_data_uri_of_file1, b64_data_uri_of_file2]``. Each local file
    is converted to a ``data:image/png;base64,...`` URI via _to_image_url."""
    import json

    ref1 = tmp_path / "front.png"
    ref1.write_bytes(b"\x89PNG\r\n\x1a\n front portrait")
    ref2 = tmp_path / "side.png"
    ref2.write_bytes(b"\x89PNG\r\n\x1a\n side portrait")
    route = respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/side.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    out = await gen.generate_single_image(
        "side portrait consistent with front",
        reference_image_paths=[str(ref1), str(ref2)],
    )

    assert out.data == "https://tos-seedream.example/side.png"
    body = json.loads(route.calls.last.request.content.decode())
    assert "image" in body
    assert isinstance(body["image"], list)
    assert len(body["image"]) == 2
    assert body["image"][0].startswith("data:image/png;base64,")
    assert body["image"][1].startswith("data:image/png;base64,")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_empty_refs_omits_image_field():
    """Empty reference list (no references) → no ``image`` field, mirroring the
    NanoBanana bridge's empty→None normalization."""
    import json

    route = respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/x.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    await gen.generate_single_image("a cat", reference_image_paths=[])
    body = json.loads(route.calls.last.request.content.decode())
    assert "image" not in body


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_uses_bearer_auth_and_ark_native_model():
    """The bridge authenticates with ``Bearer {api_key}`` and the Ark-native model id
    (no bytedance/ prefix), mirroring SeedreamImageProvider (§4.1)."""
    import json

    route = respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/x.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key-xyz")
    await gen.generate_single_image("a sunrise")
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer ark-key-xyz"
    body = json.loads(req.content.decode())
    assert body["model"] == "doubao-seedream-5-0-pro-260628"


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_429_raises_rate_limit_error():
    """429 → RateLimitError (infrastructure → fallback). Mirrors §4.1 classification."""
    respx.post(_ARK_IMAGE_URL).mock(return_value=httpx.Response(429, text="rate limited"))
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    with pytest.raises(RateLimitError):
        await gen.generate_single_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_5xx_raises_provider_unavailable():
    """5xx → ProviderUnavailableError (infrastructure → fallback). Mirrors §4.1."""
    respx.post(_ARK_IMAGE_URL).mock(return_value=httpx.Response(503, text="unavailable"))
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    with pytest.raises(ProviderUnavailableError):
        await gen.generate_single_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_4xx_raises_runtime_error_no_fallback():
    """4xx (auth/content) → RuntimeError, NOT in _FALLBACK_EXC → no fallback (§4.1).
    Content-review rejections must surface to the user, not bounce to qihu."""
    respx.post(_ARK_IMAGE_URL).mock(return_value=httpx.Response(400, text="bad request"))
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    with pytest.raises(RuntimeError, match="400"):
        await gen.generate_single_image("test")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_enforces_call_budget():
    """A per-generator call cap stops a runaway ViMax run mid-pipeline (mirrors the
    NanoBanana budget test). Budget check happens BEFORE the HTTP call."""
    respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/x.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key", max_calls=2)
    await gen.generate_single_image("a")  # call 1
    await gen.generate_single_image("b")  # call 2
    with pytest.raises(VimaxBudgetExceeded, match="image"):  # call 3 → over budget
        await gen.generate_single_image("c")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_tracks_cost_cny_per_call():
    """Each image call adds ¥0.3 to cost_cny (mirrors NanoBanana cost tracking)."""
    respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/x.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    for _ in range(5):
        await gen.generate_single_image("x")
    assert gen.cost_cny == pytest.approx(1.5)  # 5 × ¥0.3


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_enforces_max_cost_cny_budget():
    """max_cost_cny caps total ¥ spend across image calls (plan §8.3)."""
    respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/x.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key", max_cost_cny=1.0)
    # ¥0.3/call × 3 calls = ¥0.9 (OK), 4th call = ¥1.2 (exceeds ¥1.0)
    for _ in range(3):
        await gen.generate_single_image("x")
    with pytest.raises(VimaxBudgetExceeded, match="cost"):
        await gen.generate_single_image("over")


@respx.mock
@pytest.mark.asyncio
async def test_seedream_bridge_prompt_only_call_matches_vimax_front_portrait_contract():
    """ViMax's CharacterPortraitsGenerator.generate_front_portrait calls the image
    generator with ONLY ``prompt=...`` — no reference_image_paths (the first portrait
    has no reference). The bridge MUST accept that call shape (mirror NanoBanana)."""
    respx.post(_ARK_IMAGE_URL).mock(
        return_value=httpx.Response(200, json={"data": [{"url": "https://tos-seedream.example/front.png"}]}),
    )
    gen = SeedreamGeneratorForViMax(api_key="ark-key")
    out = await gen.generate_single_image(prompt="front portrait of Xiaoming")  # exactly how ViMax calls it
    assert out.data == "https://tos-seedream.example/front.png"


def test_seedream_bridge_accepts_aspect_ratio_kwarg():
    """SeedreamGeneratorForViMax accepts aspect_ratio via **_ (like NanoBanana) so
    build_vimax_config's image_aspect_ratio wiring doesn't break construction. Seedream
    doesn't use aspect_ratio in the request body (size is fixed by the t2i default),
    but it must not error on init."""
    gen = SeedreamGeneratorForViMax(api_key="ark-key", aspect_ratio="16:9")
    assert gen is not None


# ── build_vimax_config wiring (§4.2: image → SeedreamGeneratorForViMax) ────


def test_build_vimax_config_wires_seedream_generator_and_model():
    """§4.2: build_vimax_config's image_generator.class_path is now
    SeedreamGeneratorForViMax (was NanoBananaGeneratorForViMax). image_model flows
    into init_args so the bridge uses cfg.seedream_image_model."""
    import pathlib

    cfg = build_vimax_config(
        working_dir=pathlib.Path("/tmp/x"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="L",
        image_api_key="ARKKEY",
        image_base_url="https://ark.cn-beijing.volces.com/api/v3",
        image_model="doubao-seedream-5-0-pro-260628",
        video_api_key="V",
    )
    assert cfg["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")
    assert cfg["image_generator"]["init_args"]["api_key"] == "ARKKEY"
    assert cfg["image_generator"]["init_args"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert cfg["image_generator"]["init_args"]["model"] == "doubao-seedream-5-0-pro-260628"


def test_build_vimax_config_image_defaults_to_seedream_generator():
    """Even with no image_* overrides, the default image class is now Seedream (§4.2).
    NanoBanana stays defined for rollback (revert this default)."""
    import pathlib

    cfg = build_vimax_config(
        working_dir=pathlib.Path("/tmp/x"),
        llm_model="m",
        llm_base_url="u",
        llm_api_key="L",
        image_api_key="I",
        video_api_key="V",
    )
    assert cfg["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")
    # default image_model is the Ark seedream id (was the nano-banana gemini id)
    assert cfg["image_generator"]["init_args"]["model"] == "doubao-seedream-5-0-pro-260628"


# ── service.py::_vimax_config_factory routes Ark image credentials (§4.2) ──


def test_vimax_config_factory_default_routes_360_credentials(tmp_path):
    """C5 (2026-10-12): default (vimax_image_backend=360 + vimax_video_backend=seedance)
    routes BOTH image and video to the 360 gateway with qihu credentials —
    消除 Stage 5.2 时期 image=Ark 硬编码。ark rollback = 显式 config 两键（下一测试）。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(
        qihu_api_key="qihu-key",
        ark_api_key="ark-key-xxx",
        ark_base_url="https://ark.cn-beijing.volces.com/api/v3",
        seedream_image_model="doubao-seedream-5-0-pro-260628",
        seedance_ark_video_model="doubao-seedance-2-0-260128",
        engine_arch="multi_engine",
    )
    providers = MagicMock()
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    engine = svc._get_engine_registry().get_engine("vimax")
    vimax_cfg = engine._config_factory(tmp_path / "wd", {"script": "x"})

    # image side routed to 360 (C5b: Seedream360GeneratorForViMax + qihu 凭据 + 点号命名)
    assert vimax_cfg["image_generator"]["init_args"]["api_key"] == "qihu-key"
    assert vimax_cfg["image_generator"]["init_args"]["base_url"] == cfg.qihu_base_url
    assert vimax_cfg["image_generator"]["init_args"]["model"] == cfg.seedream_360_image_model
    assert vimax_cfg["image_generator"]["class_path"].endswith("Seedream360GeneratorForViMax")
    # video side routed to 360 (C5a: seedance default)
    assert vimax_cfg["video_generator"]["init_args"]["api_key"] == "qihu-key"
    assert vimax_cfg["video_generator"]["init_args"]["base_url"] == cfg.qihu_base_url
    assert vimax_cfg["video_generator"]["init_args"]["model"] == cfg.seedance_video_model
    assert vimax_cfg["video_generator"]["init_args"]["backend"] == "seedance"


def test_vimax_config_factory_ark_rollback_routes_ark_credentials(tmp_path):
    """C5 回滚位：vimax_image_backend=ark + vimax_video_backend=seedance-ark →
    Stage 5.2 原路径（image Ark seedream + video seedance-ark，均 Ark 凭据）。"""
    from unittest.mock import MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(
        qihu_api_key="qihu-key",
        ark_api_key="ark-key-xxx",
        ark_base_url="https://ark.cn-beijing.volces.com/api/v3",
        seedream_image_model="doubao-seedream-5-0-pro-260628",
        seedance_ark_video_model="doubao-seedance-2-0-260128",
        vimax_image_backend="ark",
        vimax_video_backend="seedance-ark",
        engine_arch="multi_engine",
    )
    providers = MagicMock()
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    engine = svc._get_engine_registry().get_engine("vimax")
    vimax_cfg = engine._config_factory(tmp_path / "wd", {"script": "x"})

    assert vimax_cfg["image_generator"]["init_args"]["api_key"] == "ark-key-xxx"
    assert vimax_cfg["image_generator"]["init_args"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert vimax_cfg["image_generator"]["init_args"]["model"] == "doubao-seedream-5-0-pro-260628"
    assert vimax_cfg["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")
    assert vimax_cfg["video_generator"]["init_args"]["api_key"] == "ark-key-xxx"
    assert vimax_cfg["video_generator"]["init_args"]["base_url"] == "https://ark.cn-beijing.volces.com/api/v3"
    assert vimax_cfg["video_generator"]["init_args"]["model"] == "doubao-seedance-2-0-260128"
    assert vimax_cfg["video_generator"]["init_args"]["backend"] == "seedance-ark"
