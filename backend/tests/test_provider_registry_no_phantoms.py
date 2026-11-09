"""Task 7: registry 不得宣告无实现的 provider。

⚠️ **计划前置事实 10 已被证伪**：计划称 `minimax-h3` 「在 KNOWN_VIDEO_PROVIDERS 里
但无实现文件」。实测不成立——它于 2026-08-01（Task 6）接入，复用
``QihuVideoProvider`` 协议、仅换模型 id（``minimax/MiniMax-H3``，已实测 GET /v1/models），
有构造分支（``registry._try_make_ai_video_provider``）与单测
（``test_video_gen_provider_selection.py::test_minimax_h3_uses_probed_model_id``）。
故本 task **只摘 kling**（真纯桩，``raise NotImplementedError``），保留 minimax-h3——
摘掉它等于删一个可用能力，不在本轮授权范围。

判据用计划自己给的那条：「名单里每个名字都必须能真正构造出可用实现」。
minimax-h3 通过该判据，kling 不通过。
"""

import inspect

from app.gateway.video_gen.providers import registry as reg


def test_kling_not_advertised() -> None:
    """kling 是纯桩（generate_video 直接 raise NotImplementedError），
    修前配了 key 就报 available=True，用户一用即崩，且它的 fallback 永不触发
    （NotImplementedError 不在 _FALLBACK_EXC）。"""
    assert "kling" not in reg.ProviderRegistry.KNOWN_VIDEO_PROVIDERS


def _code_lines(src: str) -> str:
    """剥掉注释行——「摘除 kling」这类说明性注释本身含该词，不能算接线残留。"""
    return "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))


def test_kling_not_in_key_map_or_factory() -> None:
    """四处接线必须全清，只删名单会留下「配了 key 仍能构造」的暗门。"""
    assert "kling" not in reg.ProviderRegistry._VIDEO_PROVIDER_KEY
    assert "kling" not in reg._VIDEO_PROVIDER_LABELS
    factory_code = _code_lines(inspect.getsource(reg.ProviderRegistry._try_make_ai_video_provider))
    assert "kling" not in factory_code, "构造分支未清理"
    default_code = _code_lines(inspect.getsource(reg.ProviderRegistry._build_ai_video))
    assert "kling" not in default_code, "fallback 分支未清理（该分支本就永不触发）"


def test_kling_not_in_schemas_engine_map() -> None:
    """schemas.ENGINE_MAP 是 CreateVideoRequest.engine 的校验白名单
    （计划写作 `schemas.py:18` 的 `"ai_video": {...}`，实际变量名是 ENGINE_MAP）。"""
    from app.gateway.video_gen import schemas

    assert "kling" not in schemas.ENGINE_MAP["ai_video"]


def test_every_known_video_provider_can_be_constructed() -> None:
    """名单里每个名字都必须有真实构造路径（计划给的核心判据）。

    用「构造分支里是否出现该名字」作代理判据——真正实例化需要 API key，
    且 available_video_providers 刻意不构造 provider（防句柄泄漏，commit 4b3413471）。
    """
    factory_src = inspect.getsource(reg.ProviderRegistry._try_make_ai_video_provider)
    missing = [name for name in reg.ProviderRegistry.KNOWN_VIDEO_PROVIDERS if name != "auto" and f'"{name}"' not in factory_src]
    assert missing == [], f"以下 provider 在名单里但无构造分支：{missing}"


def test_every_known_image_provider_can_be_constructed() -> None:
    """image 侧同款静态守护（2026-09-23 计划 §3.3：no_phantoms 扩展 image 侧）。

    定位为**增强**而非补缺——key_map 交叉验证
    （test_video_gen_provider_selection.py::test_availability_key_map_matches_actual_factory_branches）
    已对 image+video 双侧兜底；本守护不构造实例、失败更早、定位更快。
    """
    factory_src = inspect.getsource(reg.ProviderRegistry._try_make_ai_image_provider)
    missing = [name for name in reg.ProviderRegistry.KNOWN_IMAGE_PROVIDERS if f'"{name}"' not in factory_src]
    assert missing == [], f"以下 image provider 在名单里但无构造分支：{missing}"


def test_minimax_h3_stays_advertised() -> None:
    """反向锚：minimax-h3 有实现（复用 QihuVideoProvider + H3 模型 id），不得被误摘。

    本测试存在的意义是防止后续审计照抄「计划前置事实 10」再删一次。
    """
    assert "minimax-h3" in reg.ProviderRegistry.KNOWN_VIDEO_PROVIDERS
    factory_src = inspect.getsource(reg.ProviderRegistry._try_make_ai_video_provider)
    assert "minimax-h3" in factory_src
