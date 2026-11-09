"""任务级生成模型选择（Task 6，2026-08-01 反馈 8）。

**修复前 `engine` 是死旋钮：** `CreateVideoRequest.engine` 经 `ENGINE_MAP` 校验后
一路传到 `_make_visual_source(task_type, engine="auto", ...)`，而**该函数体完全不读
`engine` 参数** —— provider 一律取 `self._providers.ai_image` / `.ai_video`，
即启动时按 `config.yaml` 全局构建的那一个。前端 `ENGINE_OPTIONS` 也只给
remotion/vimax 各一个 `auto` 且 `.length > 1` 才渲染 → UI 从不显示。

本 Task 新增 `image_provider` / `video_provider` 两个任务级字段（`None` = 跟随 config）。

**用户拍板的两条关键语义：**
1. **显式指定则不降级** —— 用户选了 A 却静默出 B 的图比失败更糟；失败时把真实原因
   回传前端。`None`（未指定）时现有降级链**保持不变**（回归护栏）。
2. UI 用下拉且不展示任何模型限制文案。

两个新 provider 的模型 id 已于 2026-08-01 打 360 网关 `/v1/models` **实测确认**
（214 个模型，HTTP 200），非猜测：
  - `seedream-360` → `bytedance/doubao-seedream-5.0-pro`（注意是 `5.0` 点号，
    与 Ark 直连的 `doubao-seedream-5-0-pro-260628` 命名不同）
  - `minimax-h3`   → `minimax/MiniMax-H3`
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.schemas import CreateVideoRequest


def _base_payload(**over):
    p = {"video_type": "ai_image", "topic": "测试主题"}
    p.update(over)
    return p


# ── schema ──────────────────────────────────────────────────────────────


def test_provider_fields_default_to_none():
    """不传 = 跟随 config 默认（保持今天的行为）。"""
    req = CreateVideoRequest(**_base_payload())
    assert req.image_provider is None
    assert req.video_provider is None


def test_valid_provider_names_accepted():
    for name in ("seedream", "seedream-360", "gpt-image-2", "dashscope"):
        req = CreateVideoRequest(**_base_payload(image_provider=name))
        assert req.image_provider == name
    # 2026-08-03 Task 7：删掉 "kling"——它是纯桩（generate_video 直接
    # raise NotImplementedError），已从 KNOWN_VIDEO_PROVIDERS 摘除，
    # 现在传它应当 422（见下方 test_kling_rejected_after_removal）。
    for name in ("seedance-ark", "seedance", "minimax-h3"):
        req = CreateVideoRequest(**_base_payload(video_provider=name))
        assert req.video_provider == name


def test_kling_rejected_after_removal():
    """Task 7：摘除后 kling 必须像其他非法名一样 422，不能静默落库到 worker 才炸。"""
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(video_provider="kling"))


def test_mini_happyhorse_rejected_after_removal():
    """2026-08-28 拍板摘除：任务级显式选 seedance-mini/happyhorse 必须 422
    （vimax 桥内部 fallback 直连 provider 不走本校验，不受影响）。"""
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(video_provider="seedance-mini"))
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(video_provider="happyhorse"))


def test_unknown_provider_rejected():
    """非法名必须 422，不能静默落库后到 worker 才炸。"""
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(image_provider="不存在的模型"))
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(video_provider="不存在的模型"))


def test_image_and_video_provider_namespaces_are_separate():
    """生图名不能塞进 video_provider（反之亦然）——否则用户选错也不报错。"""
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(video_provider="seedream"))
    with pytest.raises(ValidationError):
        CreateVideoRequest(**_base_payload(image_provider="kling"))


# ── registry 按名取用 ────────────────────────────────────────────────────


def test_registry_make_by_name_returns_requested_provider():
    """具名取用必须真的换 provider（这是核心断言——修复前 registry 无此入口）。"""
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)

    p = reg.make_ai_image("gpt-image-2")
    inner = getattr(p, "_inner", p)  # Throttle 包装
    assert isinstance(inner, GptImage2Provider), f"应返回 GptImage2，实际 {type(inner)}"


def test_registry_make_by_name_none_keeps_default_chain():
    """None → 回退到启动时构建的默认实例（含既有降级链）。回归护栏。"""
    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    assert reg.make_ai_image(None) is reg.ai_image
    assert reg.make_ai_video(None) is reg.ai_video


def test_registry_named_provider_has_no_fallback():
    """用户决策 4：显式指定不降级 —— 具名产物不得是 Fallback 包装。"""
    from app.gateway.video_gen.providers.registry import (
        FallbackAIImageProvider,
        FallbackAIVideoProvider,
    )

    cfg = VideoGenConfig(qihu_api_key="k", ark_api_key="a")
    reg = ProviderRegistry(cfg)

    img = reg.make_ai_image("seedream")
    assert not isinstance(img, FallbackAIImageProvider), "显式指定不得挂降级链"
    vid = reg.make_ai_video("seedance-ark")
    assert not isinstance(vid, FallbackAIVideoProvider), "显式指定不得挂降级链"


def test_registry_unavailable_provider_raises_clear_error():
    """未配 key 的 provider 具名取用 → 抛明确异常（含「未配置」字样），不返回 stub。

    静默 stub 会让用户拿到一堆占位图还以为成功了。
    """
    cfg = VideoGenConfig(qihu_api_key="k")  # 无 dashscope_api_key
    reg = ProviderRegistry(cfg)
    with pytest.raises(ValueError, match="未配置|not configured"):
        reg.make_ai_image("dashscope")


def test_registry_unknown_name_raises():
    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    with pytest.raises(ValueError):
        reg.make_ai_image("完全不存在")
    with pytest.raises(ValueError):
        reg.make_ai_video("完全不存在")


# ── Step 2b：两个新 provider（模型 id 已实测） ──────────────────────────


def test_seedream_360_uses_probed_model_id():
    """360 通道 seedream 走 Qihu 协议 + 实测到的模型 id。

    ⚠️ 不能复用 Ark 的 SeedreamImageProvider —— 那是 Ark 直连协议
    （ark_base_url + ARK_API_KEY + image:[list] 参考图字段）。
    """
    from app.gateway.video_gen.providers.qihu_image import QihuImageProvider

    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    inner = reg._try_make_ai_image_provider(cfg, "seedream-360")
    assert inner is not None, "seedream-360 应可构建（仅需 qihu_api_key）"
    assert isinstance(inner, QihuImageProvider), "应复用 Qihu 协议"
    assert inner._model == "bytedance/doubao-seedream-5.0-pro", f"模型 id 必须是实测值（5.0 点号），实际={inner._model!r}"


def test_minimax_h3_uses_probed_model_id():
    """MiniMax-H3 复用 Qihu 视频协议，仅模型 id 不同；qihu(Hailuo) 已于 2026-08-11 下架。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider

    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    inner = reg._try_make_ai_video_provider(cfg, "minimax-h3")
    assert inner is not None
    assert isinstance(inner, QihuVideoProvider)
    assert inner._model == "minimax/MiniMax-H3", f"模型 id 必须是实测值（驼峰 MiniMax + 大写 H3），实际={inner._model!r}"


def test_new_providers_require_qihu_key():
    """无 qihu key → 两个新 provider 都构建不出来（与同网关其它 provider 一致）。"""
    cfg = VideoGenConfig()  # 无任何 key
    reg = ProviderRegistry(cfg)
    assert reg._try_make_ai_image_provider(cfg, "seedream-360") is None
    assert reg._try_make_ai_video_provider(cfg, "minimax-h3") is None


# ── 端到端：覆盖值真的走到 visual source（核心断言） ─────────────────────


def _make_svc(cfg: VideoGenConfig):
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.service import VideoGenService

    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir="/tmp",
        sandbox_provider=AsyncMock(),
    )


def test_make_visual_source_consumes_image_provider_override():
    """**这是本 Task 的核心断言** —— 修复前 `_make_visual_source` 完全不读 provider 参数。

    `engine` 就是这么变成死旋钮的：schema 校验通过、值一路传到函数，函数却不看它。
    这条断言确保新字段不会重蹈覆辙。
    """
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider

    svc = _make_svc(VideoGenConfig(qihu_api_key="k"))
    src = svc._make_visual_source("ai_image", image_provider="gpt-image-2")

    provider = src._provider
    inner = getattr(provider, "_inner", provider)
    assert isinstance(inner, GptImage2Provider), f"覆盖值未生效，实际 provider={type(inner)}（修复前即此症状：参数被忽略）"


def test_make_visual_source_consumes_video_provider_override():
    """生视频侧同构：minimax-h3 必须真的换成 H3 模型。"""
    svc = _make_svc(VideoGenConfig(qihu_api_key="k"))
    src = svc._make_visual_source("ai_video", video_provider="minimax-h3")

    provider = src._provider
    inner = getattr(provider, "_inner", provider)
    assert inner._model == "minimax/MiniMax-H3", f"覆盖值未生效，实际 model={inner._model!r}"


def test_make_visual_source_none_matches_default_behaviour():
    """回归护栏：不指定时必须与改动前一致（用 config 默认那一个实例）。"""
    cfg = VideoGenConfig(qihu_api_key="k")
    svc = _make_svc(cfg)
    src = svc._make_visual_source("ai_image")
    assert src._provider is svc._providers.ai_image, "未指定时不得改变既有行为"


def test_mixed_visual_source_also_honours_override():
    """混合任务（逐帧不同 visual_source）也要接线，否则用户选的模型被静默忽略。"""
    from app.gateway.video_gen.providers.gpt_image2 import GptImage2Provider
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_svc(VideoGenConfig(qihu_api_key="k"))
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1920, height=1080))
    sb.frames.append(StoryboardFrame(index=0, narration="a", visual_source="ai_image"))
    sb.frames.append(StoryboardFrame(index=1, narration="b", visual_source="ai_video"))

    src = svc._make_visual_source_mixed(sb, image_provider="gpt-image-2", video_provider="minimax-h3")
    img = src._sources["ai_image"]._provider
    vid = src._sources["ai_video"]._provider
    assert isinstance(getattr(img, "_inner", img), GptImage2Provider)
    assert getattr(vid, "_inner", vid)._model == "minimax/MiniMax-H3"


def test_unavailable_override_surfaces_error_not_silent_stub():
    """指定未配 key 的模型 → 抛错（含可读原因），**不得**静默回落默认或给 stub。

    用户决策 4：选了 A 却静默出 B 的图比失败更糟。
    """
    svc = _make_svc(VideoGenConfig(qihu_api_key="k"))  # 无 dashscope key
    with pytest.raises(ValueError, match="未配置|not configured"):
        svc._make_visual_source("ai_image", image_provider="dashscope")


# ── GET /api/video/providers 端点 ────────────────────────────────────────


def _make_app(cfg: VideoGenConfig):
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    mock_svc._providers = ProviderRegistry(cfg)
    app.state.video_gen_service = mock_svc
    return app


def test_providers_endpoint_contract():
    """返回 {image:[{name,label,available,is_default}], video:[... + capability]} 结构。

    2026-08-03 Task 13：video 侧多一个 ``capability`` 键（能力元数据随列表下发，
    前端不必为每个新模型改代码）。image 侧刻意不加——无时长概念，能力模型不同（YAGNI）。
    断言仍用精确集合比较（不放松成 issubset），以便下次擅自加键时转红。
    """
    from fastapi.testclient import TestClient

    client = TestClient(_make_app(VideoGenConfig(qihu_api_key="k")))
    resp = client.get("/api/video/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"image", "video"}
    expected = {
        # E5（2026-08-21）：两侧均新增 annotations（能力/风险标注元数据，D5 口径）。
        "image": {"name", "label", "available", "is_default", "annotations"},
        "video": {"name", "label", "available", "is_default", "capability", "annotations"},
    }
    for group in ("image", "video"):
        assert body[group], f"{group} 不应为空"
        for item in body[group]:
            assert set(item) == expected[group]
            assert isinstance(item["available"], bool)
    for item in body["video"]:
        assert set(item["capability"]) == {
            "max_duration",
            "min_duration",
            "integer_seconds",
            "supports_image_urls",
            "supported_ratios",
        }


def test_providers_endpoint_marks_unconfigured_as_unavailable():
    """未配 key 的 provider 必须 available=False（前端据此不渲染）。"""
    from fastapi.testclient import TestClient

    # 只配 qihu：dashscope 没 key
    client = TestClient(_make_app(VideoGenConfig(qihu_api_key="k")))
    body = client.get("/api/video/providers").json()

    img = {i["name"]: i for i in body["image"]}
    vid = {i["name"]: i for i in body["video"]}
    assert img["dashscope"]["available"] is False, "未配 dashscope key 却报可用"
    # 2026-08-03 Task 7：原断言 vid["kling"]["available"] is False 已删——kling
    # 不再出现在列表里（纯桩不该被宣告）。改断言它**根本不在**，这比「在但不可用」
    # 更强：修前配了 kling_api_key 就会报 available=True，用户一选即 NotImplementedError。
    assert "kling" not in vid, "纯桩 provider 不得出现在可用列表"
    # 未配 ark key → seedance-ark 应在列表里但不可用（对照组，证明"不在"不是因为列表空）
    assert vid["seedance-ark"]["available"] is False, "未配 ark key 却报可用"
    # 同网关的几个应可用
    assert img["gpt-image-2"]["available"] is True
    assert img["seedream-360"]["available"] is True
    assert vid["minimax-h3"]["available"] is True


def test_providers_endpoint_no_limitation_copy():
    """用户决策 6/7：下拉只给模型名，**不展示任何限制/警告文案**。"""
    from fastapi.testclient import TestClient

    client = TestClient(_make_app(VideoGenConfig(qihu_api_key="k", ark_api_key="a")))
    body = client.get("/api/video/providers").json()
    blob = " ".join(i["label"] for g in ("image", "video") for i in body[g])
    for banned in ("真人", "拦", "漂移", "不支持", "限制", "警告", "注意"):
        assert banned not in blob, f"label 里出现了限制文案「{banned}」：{blob}"


def test_providers_endpoint_does_not_leak_provider_instances():
    """回归护栏：`/providers` 探测可用性**不得构建 provider 实例**。

    初版实现用「能否构建」判定可用性（`factory(...) is not None`），每调一次就往
    `_closables` 塞 9 个带 httpx client 的实例且永不释放 —— 实测调 3 次
    `_closables` 从 2 涨到 29。该端点在每次打开创建对话框时都会被调用，
    10 用户场景下会持续累积到进程重启。

    可用性只取决于 key 是否配置，查 config 属性即可，无需构建。
    """
    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    baseline = len(reg._closables)

    for _ in range(3):
        reg.available_image_providers()
        reg.available_video_providers()

    assert len(reg._closables) == baseline, f"探测可用性泄漏了 provider 实例：_closables {baseline} → {len(reg._closables)}"


def test_availability_key_map_matches_actual_factory_branches():
    """防漂移：key 映射表的判定必须与 `_try_make_*` 的实际构建结果一致。

    可用性改为查表后，表若与工厂分支脱节（新增 provider 忘了登记、或某分支改了
    依赖的 key），端点就会谎报可用性 —— 用户选了个其实构建不出来的模型。
    本用例对多种 key 组合逐一交叉验证两者结论相同。
    """
    combos = [
        {},
        {"qihu_api_key": "k"},
        {"ark_api_key": "a"},
        {"qihu_api_key": "k", "ark_api_key": "a"},
        {"dashscope_api_key": "d"},
        {"kling_api_key": "c"},
        {"qihu_api_key": "k", "ark_api_key": "a", "dashscope_api_key": "d", "kling_api_key": "c"},
    ]
    for kwargs in combos:
        cfg = VideoGenConfig(**kwargs)
        reg = ProviderRegistry(cfg)

        for item in reg.available_image_providers():
            actual = reg._try_make_ai_image_provider(cfg, item["name"]) is not None
            assert item["available"] == actual, f"生图 {item['name']} 在 {kwargs} 下判定={item['available']} 实际={actual}"

        for item in reg.available_video_providers():
            actual = reg._try_make_ai_video_provider(cfg, item["name"]) is not None
            assert item["available"] == actual, f"生视频 {item['name']} 在 {kwargs} 下判定={item['available']} 实际={actual}"


def test_closables_registered_for_named_providers():
    """具名构建的 provider 也要登记进 _closables，避免 httpx client 泄漏。"""
    cfg = VideoGenConfig(qihu_api_key="k")
    reg = ProviderRegistry(cfg)
    before = len(reg._closables)
    reg.make_ai_image("gpt-image-2")
    assert len(reg._closables) > before, "具名 provider 未登记 _closables → httpx 泄漏"
