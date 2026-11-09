"""T7 (Lane 2, 2026-10-15): vimax 两道测试守卫。

1. **P26 记账接线源码扫描守卫**：vendored 树所有 ``init_chat_model(`` 接线点
   必须在 ``wrap_for_accounting(...)`` 内——裸构造会让该 LLM 调用绕过记账
   （token 用量/成本不可见）。上游 rebase 或 vendored patch 漂移时本守卫拦截。
   只读扫描 vendored 文件（不违 ban）；扫描器助手另有合成样例的单测（变异证伪：
   塞一行裸 init_chat_model 必须被抓）。
2. **class_path 接缝测试**：真实 ``build_vimax_config`` → 临时 yaml →
   ``Script2VideoPipeline.init_from_config`` 实例化——修前无任何测试走过这条
   生产装载路径（class_path 拼写/签名漂移只在线上炸）。假 base_url/key，
   构造期不发请求。
"""

from __future__ import annotations

from pathlib import Path

import yaml

VIMAX_PKG = Path(__file__).resolve().parents[1] / "packages" / "vimax" / "vimax"

# P26 记账接线点数（2026-10-16 T1 复核：novel 系 7 agents + novel2movie_pipeline
# 已随 novel2movie 拆除删除，剩 script2video + idea2video 两条活管线各 1 执行点
# + docstring 示例 = 3 处命中）。
# 下限断言（≥）而非精确相等：Lane 1 / 上游新增接线点不该被本守卫卡住，漏包才卡。
_MIN_WRAPPED_SITES = 3


def _find_unwrapped_init_chat_model_lines(text: str) -> list[int]:
    """扫描单文件源码：返回「init_chat_model( 出现但同行无 wrap_for_accounting」的行号。

    行内注释剥离；P26 全部接线点均为同行包裹形态（wrap_for_accounting(init_chat_model(…)…)），
    跨行裸构造会被本规则抓出。
    """
    offenders: list[int] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        code = line.split("#", 1)[0]
        if "init_chat_model(" in code and "wrap_for_accounting" not in code:
            offenders.append(lineno)
    return offenders


def test_scanner_helper_catches_unwrapped_construction():
    """变异证伪：塞一行裸 init_chat_model 必须被扫描器抓出（守卫的守卫）。"""
    synthetic = """
def _bad():
    self.chat_model = init_chat_model(
        model="m",
    )

def _good():
    self.chat_model = wrap_for_accounting(init_chat_model(
        model="m",
    ), tag="x")
"""
    offenders = _find_unwrapped_init_chat_model_lines(synthetic)
    assert offenders == [3], f"裸构造在第 3 行须被抓，实际 {offenders}"


def test_all_vendored_init_chat_model_sites_are_accounting_wrapped():
    """P26 守卫：vendored 树全部 init_chat_model 接线点均在 wrap_for_accounting 内。"""
    offenders: list[str] = []
    wrapped_sites = 0
    for path in VIMAX_PKG.rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        bad = _find_unwrapped_init_chat_model_lines(text)
        for lineno in bad:
            offenders.append(f"{path.relative_to(VIMAX_PKG)}:{lineno}")
        for lineno, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            if "init_chat_model(" in code and "wrap_for_accounting" in code:
                wrapped_sites += 1
    assert not offenders, "vendored 存在绕过记账的裸 init_chat_model 接线点（P26 违规）：\n" + "\n".join(offenders)
    assert wrapped_sites >= _MIN_WRAPPED_SITES, f"接线点数 {wrapped_sites} < 预期下限 {_MIN_WRAPPED_SITES}——扫描规则可能漏扫（多行形态），须复核"


def test_build_vimax_config_seam_instantiates_real_bridges(tmp_path):
    """class_path 接缝：build_vimax_config → yaml → init_from_config 实例化出本仓桥。

    假 base_url/key——构造期零网络请求；断言 image/video generator 均为 app 层
    桥类型（class_path 拼写/import 路径/init_args 签名漂移在此拦截，而非线上）。
    """
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    from app.gateway.video_gen.engines.vimax.provider_bridge import (
        SeedanceVideoGeneratorForViMax,
        Seedream360GeneratorForViMax,
        build_vimax_config,
    )

    cfg = build_vimax_config(
        working_dir=tmp_path,
        llm_model="fake-model",
        llm_base_url="http://127.0.0.1:9",
        llm_api_key="fake-key",
        image_api_key="fake-key",
        image_backend="360",
        video_api_key="fake-key",
        video_backend="seedance",
        pipeline="script2video",
    )
    config_path = tmp_path / "vimax_config.yaml"
    config_path.write_text(yaml.safe_dump(cfg, allow_unicode=True), encoding="utf-8")

    try:
        pipeline = Script2VideoPipeline.init_from_config(config_path=str(config_path))
        assert isinstance(pipeline.image_generator, Seedream360GeneratorForViMax), f"image 桥类型漂移：{type(pipeline.image_generator)}"
        assert isinstance(pipeline.video_generator, SeedanceVideoGeneratorForViMax), f"video 桥类型漂移：{type(pipeline.video_generator)}"
    finally:
        # 构造出的桥持有 httpx client——测试侧显式关闭，防泄漏告警
        import asyncio

        async def _close():
            for attr in ("image_generator", "video_generator"):
                obj = getattr(pipeline, attr, None)
                close = getattr(obj, "close", None)
                if callable(close):
                    await close()

        asyncio.run(_close())
