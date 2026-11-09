"""C5（2026-10-12 加固批）：vimax 图像/视频生成切 360 通道的定向测试。

- video（C5a）：``vimax_video_backend`` 代码默认翻转 ``auto``→``seedance``（360
  主力 r2v）；``auto`` 语义同指 seedance；显式 ``seedance-ark`` 保留为回滚位；
  未知值 ValueError（防 typo 静默落分支——评审防呆要求）。
- image（C5b）：新增 ``vimax_image_backend`` 字段（默认 ``"360"``，``"ark"`` 为
  回滚位）；``_vimax_config_factory`` 按值路由凭据/模型/桥接类；
  ``Seedream360GeneratorForViMax`` 包装 ``QihuImageProvider`` 的请求形态
  （参考图走 ``/v1/images/edits`` + ``extra_body.image``；尺寸走
  ``extra_body.size`` 三档映射——顶层 size 被静默改派；watermark=false）与
  预算闸。

C5c（360 通道角色 vimax spike）是生产配置落值的硬门禁，须 RUN_LIVE=1 在终态
配置上跑，不在本文件（离线 CI）范围。
"""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.base import ImageRef
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService

_QIHU_EDITS = "https://api.360.cn/v1/images/edits"
_QIHU_GENERATIONS = "https://api.360.cn/v1/images/generations"


def _make_service(tmp_path: Path, **overrides) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="qk", ark_api_key="ak", engine_arch="multi_engine", **overrides)
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


def _factory_config(svc: VideoGenService, tmp_path: Path) -> dict:
    engine = svc._get_engine_registry().get_engine("vimax")
    return engine._config_factory(tmp_path / "wd", {"script": "x"})


# ── C5a: video backend 默认翻转 + auto 语义 + 回滚位 + 防呆 ───────────────


def test_vimax_video_backend_default_flipped_to_seedance():
    """C5a：代码默认 auto→seedance（360 主力 r2v）。部署侧生效以 config 显式
    落值为准（spike 硬门禁），代码默认与 config 三处落值是同一开关的两半。"""
    assert VideoGenConfig().vimax_video_backend == "seedance"


def test_vimax_image_backend_field_defaults_to_360():
    """C5b：新字段 vimax_image_backend，默认 "360"（用户决策方向），"ark" 回滚位。"""
    assert VideoGenConfig().vimax_image_backend == "360"


def test_resolve_auto_now_routes_to_360_seedance():
    """auto 语义翻转：同指 360 seedance（2026-07 Stage 5.2 曾因 360 拦人像迁
    Ark，2026-10-12 用户决策迁回——拦截风险以 C5c spike 实测为准）。"""
    from app.gateway.video_gen.service import _resolve_vimax_video_backend

    cfg = VideoGenConfig()
    assert _resolve_vimax_video_backend("realistic movie style", "auto", cfg) == ("seedance", cfg.seedance_video_model)
    assert _resolve_vimax_video_backend(None, "auto", cfg) == ("seedance", cfg.seedance_video_model)
    assert _resolve_vimax_video_backend("Cartoon", None, cfg) == ("seedance", cfg.seedance_video_model)


def test_resolve_seedance_ark_explicit_rollback_still_works():
    """显式 seedance-ark = 配置回滚位（无需 revert 代码）。"""
    from app.gateway.video_gen.service import _resolve_vimax_video_backend

    cfg = VideoGenConfig()
    assert _resolve_vimax_video_backend("realistic", "seedance-ark", cfg) == ("seedance-ark", cfg.seedance_ark_video_model)


def test_resolve_unknown_video_backend_raises_value_error():
    """防呆：未知 vimax_video_backend（typo 如 "qihu"/" 360"）必须抛 ValueError，
    不许静默落任何分支（评审要求）。"""
    from app.gateway.video_gen.service import _resolve_vimax_video_backend

    cfg = VideoGenConfig()
    for bad in ("qihu", " 360", "seednce", "SEEDANCE_ARK"):
        with pytest.raises(ValueError, match="vimax_video_backend"):
            _resolve_vimax_video_backend(None, bad, cfg)


# ── C5b: build_vimax_config image_backend 路由 ─────────────────────────────


def test_build_vimax_config_image_backend_360_routes_bridge(tmp_path):
    """image_backend="360" → Seedream360GeneratorForViMax（360 通道桥接类）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import build_vimax_config

    cfgd = build_vimax_config(
        working_dir=tmp_path,
        llm_model="m",
        llm_base_url="https://api.360.cn/v1",
        llm_api_key="k",
        image_api_key="qihu-key",
        image_base_url="https://api.360.cn",
        image_model="bytedance/doubao-seedream-5.0-pro",
        image_backend="360",
        video_api_key="k2",
        video_backend="seedance",
    )
    ig = cfgd["image_generator"]
    assert ig["class_path"].endswith("Seedream360GeneratorForViMax")
    assert ig["init_args"]["api_key"] == "qihu-key"
    assert ig["init_args"]["base_url"] == "https://api.360.cn"
    assert ig["init_args"]["model"] == "bytedance/doubao-seedream-5.0-pro"


def test_build_vimax_config_image_backend_default_stays_ark(tmp_path):
    """不传 image_backend → 既有 Ark 行为不变（存量调用方/测试零改动；service
    层按 config 字段显式传值，翻转不发生在 bridge 层默认）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import build_vimax_config

    cfgd = build_vimax_config(
        working_dir=tmp_path,
        llm_model="m",
        llm_base_url="u",
        llm_api_key="k",
        image_api_key="ark-key",
        video_api_key="k2",
        video_backend="seedance-ark",
    )
    assert cfgd["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")


def test_build_vimax_config_image_backend_unknown_raises(tmp_path):
    from app.gateway.video_gen.engines.vimax.provider_bridge import build_vimax_config

    with pytest.raises(ValueError, match="image_backend"):
        build_vimax_config(
            working_dir=tmp_path,
            llm_model="m",
            llm_base_url="u",
            llm_api_key="k",
            image_api_key="k1",
            video_api_key="k2",
            image_backend="qihu",
        )


# ── C5b: service 工厂按 config 路由 ────────────────────────────────────────


def test_vimax_factory_image_defaults_to_360_qihu_credentials(tmp_path):
    """默认（vimax_image_backend="360"）→ 360 桥 + qihu 凭据 + 360 点号命名模型；
    消除 service.py 对 Ark 图像凭据的硬编码。"""
    svc = _make_service(tmp_path)
    cfgd = _factory_config(svc, tmp_path)
    ig = cfgd["image_generator"]
    assert ig["class_path"].endswith("Seedream360GeneratorForViMax")
    assert ig["init_args"]["api_key"] == "qk"
    assert ig["init_args"]["base_url"] == svc._config.qihu_base_url
    assert ig["init_args"]["model"] == svc._config.seedream_360_image_model


def test_vimax_factory_image_ark_rollback(tmp_path):
    """vimax_image_backend="ark" → 既有 Ark 直连路径（回滚位）。"""
    svc = _make_service(tmp_path, vimax_image_backend="ark")
    cfgd = _factory_config(svc, tmp_path)
    ig = cfgd["image_generator"]
    assert ig["class_path"].endswith("SeedreamGeneratorForViMax")
    assert ig["init_args"]["api_key"] == "ak"
    assert ig["init_args"]["base_url"] == svc._config.ark_base_url
    assert ig["init_args"]["model"] == svc._config.seedream_image_model


def test_vimax_factory_image_unknown_backend_raises(tmp_path):
    svc = _make_service(tmp_path, vimax_image_backend="qihu")
    with pytest.raises(ValueError, match="vimax_image_backend"):
        _factory_config(svc, tmp_path)


def test_vimax_factory_image_backend_case_normalized(tmp_path):
    """AI 评审 low#1：大小写归一口径统一——service 层与 bridge 层（strip().lower()）、
    视频侧 _resolve_vimax_video_backend（.lower()）一致；config 写 "ARK" 须归一化
    到 ark 回滚位而非报错（" 360 " 带空白同理归一到 360）。"""
    svc = _make_service(tmp_path, vimax_image_backend="ARK")
    cfgd = _factory_config(svc, tmp_path)
    assert cfgd["image_generator"]["class_path"].endswith("SeedreamGeneratorForViMax")
    assert cfgd["image_generator"]["init_args"]["api_key"] == "ak"

    svc2 = _make_service(tmp_path, vimax_image_backend=" 360 ")
    cfgd2 = _factory_config(svc2, tmp_path)
    assert cfgd2["image_generator"]["class_path"].endswith("Seedream360GeneratorForViMax")
    assert cfgd2["image_generator"]["init_args"]["api_key"] == "qk"


def test_vimax_factory_video_default_seedance_uses_qihu_credentials(tmp_path):
    """video 默认 seedance（360）→ 走 qihu 凭据（"ark" in backend 路由既有逻辑）。"""
    svc = _make_service(tmp_path)
    cfgd = _factory_config(svc, tmp_path)
    vg = cfgd["video_generator"]
    assert vg["init_args"]["backend"] == "seedance"
    assert vg["init_args"]["api_key"] == "qk"
    assert vg["init_args"]["model"] == svc._config.seedance_video_model


# ── C5b: Seedream360GeneratorForViMax 请求形态（mock httpx）────────────────


@respx.mock
@pytest.mark.asyncio
async def test_seedream360_bridge_edits_endpoint_with_reference(tmp_path):
    """带参考图 → /v1/images/edits + extra_body.image（单图字符串，官方《图片
    编辑》口径）；extra_body.size 三档映射（16:9 → 2048x1152）；顶层无 size
    （实测被静默改派）；watermark=false；Bearer 认证。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import Seedream360GeneratorForViMax

    route = respx.post(_QIHU_EDITS).mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example/img.png"}]}))
    ref = tmp_path / "front.png"
    ref.write_bytes(b"\x89PNG\r\n\x1a\n front portrait")
    gen = Seedream360GeneratorForViMax(api_key="qihu-key", model="bytedance/doubao-seedream-5.0-pro", aspect_ratio="16:9")

    out = await gen.generate_single_image("side portrait consistent with front", reference_image_paths=[str(ref)])

    assert out.fmt == "url" and out.data == "https://cdn.example/img.png"
    assert route.called
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer qihu-key"
    body = json.loads(req.content.decode())
    assert body["model"] == "bytedance/doubao-seedream-5.0-pro"
    assert body["prompt"] == "side portrait consistent with front"
    assert "size" not in body, "顶层 size 被静默改派（T4 实测）——尺寸必须走 extra_body.size"
    assert body["extra_body"]["size"] == "2048x1152"
    assert body["extra_body"]["watermark"] is False
    img = body["extra_body"]["image"]
    assert isinstance(img, str) and img.startswith("data:image/png;base64,"), "单参考图=字符串；本地文件转 base64 data-URI"


@respx.mock
@pytest.mark.asyncio
async def test_seedream360_bridge_multi_refs_send_array(tmp_path):
    """多参考图 → extra_body.image = base64 数组（角色三视图 side/back 参考链）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import Seedream360GeneratorForViMax

    route = respx.post(_QIHU_EDITS).mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example/side.png"}]}))
    ref1, ref2 = tmp_path / "front.png", tmp_path / "side.png"
    ref1.write_bytes(b"\x89PNG front")
    ref2.write_bytes(b"\x89PNG side")
    gen = Seedream360GeneratorForViMax(api_key="k")

    await gen.generate_single_image("back portrait", reference_image_paths=[str(ref1), str(ref2)])

    body = json.loads(route.calls.last.request.content.decode())
    img = body["extra_body"]["image"]
    assert isinstance(img, list) and len(img) == 2
    assert all(u.startswith("data:image/png;base64,") for u in img)


@respx.mock
@pytest.mark.asyncio
async def test_seedream360_bridge_no_refs_uses_generations_endpoint():
    """无参考图（front portrait 首图）→ /v1/images/generations 纯文生图；
    默认 9:16 → 1152x2048（三档映射），extra_body 无 image 字段。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import Seedream360GeneratorForViMax

    route_gen = respx.post(_QIHU_GENERATIONS).mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example/front.png"}]}))
    route_edits = respx.post(_QIHU_EDITS).mock(return_value=httpx.Response(200, json={"data": [{"url": "https://cdn.example/x.png"}]}))
    gen = Seedream360GeneratorForViMax(api_key="k")

    out = await gen.generate_single_image("front portrait of Xiaoming")

    assert route_gen.called and not route_edits.called
    assert out.data == "https://cdn.example/front.png"
    body = json.loads(route_gen.calls.last.request.content.decode())
    assert body["extra_body"]["size"] == "1152x2048"
    assert "image" not in body["extra_body"]


@pytest.mark.asyncio
async def test_seedream360_bridge_budget_gates():
    """预算闸与 Ark 桥同构：max_calls 触顶抛 VimaxBudgetExceeded（计数闸在桥层，
    与 provider 无关）。"""
    from app.gateway.video_gen.engines.vimax.provider_bridge import Seedream360GeneratorForViMax, VimaxBudgetExceeded

    gen = Seedream360GeneratorForViMax(api_key="k", max_calls=1)
    gen._provider = AsyncMock()
    gen._provider.generate_image = AsyncMock(return_value=ImageRef(url="https://cdn.example/1.png", width=1152, height=2048, source="seedream-360"))

    await gen.generate_single_image("a")  # call 1
    with pytest.raises(VimaxBudgetExceeded, match="image generation budget"):
        await gen.generate_single_image("b")  # call 2 → over budget
