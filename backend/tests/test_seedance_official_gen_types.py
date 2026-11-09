"""2026-08-31 官方 6 类型归一：360 seedance 下发形态（事故 vg_ffe94de7b688 修正）。

官方文档《创建视频生成（seedance）》（https://ai.360.com/docs/503146854e0.md）定义
6 种生成类型，**不存在「首帧 + 多图参考」的 content 形态**：

- 类型 2 图生视频-首帧 = [text, image×1]；
- 类型 3 首尾帧 = [text, image×2]（无 role，第一张=首帧、第二张=尾帧）；
- 类型 6 多模态参考 = [text, image×n] + role=reference。

机制 M（389099ea2）的「preview_first 多图无 role 全量下发」落入未定义区（2 图 =
首尾帧 → 角色三视图被当**尾帧** → 每镜尾部闪现）。本文件锁四条归一规则
（providers/seedance.py ``_enforce_official_gen_types``）。

复核修复（2026-08-31 第二轮）：①留痕后置——``dropped_360_multi_ref`` 只在
360 提交/轮询**成功后**写（修前先于 ``_submit``，降级 Ark 时台账虚报弃图）；
②留痕函数抗「合法 JSON 顶层非数组」外部损坏（修前 TypeError 穿透打断单帧）。

BUG-B 铁律：走生产同款 ``FallbackAIVideoProvider(Throttled(seedance-360),
Throttled(seedance-ark))`` 包裹链（E3 主备形态），捕获点在假 httpx client 的
submit body——裸 mock provider 测的是生产里不存在的配置。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

PNG = b"\x89PNG\r\n\x1a\n" + b"p" * 2048
REF1 = b"\x89PNG\r\n\x1a\n" + b"1" * 2048  # 角色 A 三视图（字节可与预览图区分）
REF2 = b"\x89PNG\r\n\x1a\n" + b"2" * 2048  # 角色 B 三视图


def _write_refs(tmp_path: Path) -> tuple[Path, Path, Path]:
    d = tmp_path / "refs"
    d.mkdir()
    preview, r1, r2 = d / "preview.png", d / "char_a.png", d / "char_b.png"
    preview.write_bytes(PNG)
    r1.write_bytes(REF1)
    r2.write_bytes(REF2)
    return preview, r1, r2


def _chain():
    """生产 E3 同款：Fallback(Throttled(seedance-360 主力), Throttled(seedance-ark 兜底))。

    捕获 client / 首轮 poll succeeded 形态复用 test_ai_video_preview_first_frame
    （tests/ 无 __init__.py，同目录裸模块名经 pytest basedir sys.path 插入可导，
    函数内导入免 isort 分类烦恼——P2-1 迁移同款约定）。
    """
    from test_ai_video_preview_first_frame import _ArkCaptureClient, _QihuCaptureClient

    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    s360 = SeedanceVideoProvider(api_key="k", base_url="http://s360", poll_interval=0)
    ark = SeedanceArkVideoProvider(api_key="k", base_url="http://ark", poll_interval=0, max_inflight=1)
    s_client, ark_client = _QihuCaptureClient(), _ArkCaptureClient()
    s360._client = s_client  # type: ignore[assignment]
    ark._client = ark_client  # type: ignore[assignment]
    provider = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(s360, qps=1000.0, max_retries=0),
        ThrottledAIVideoProvider(ark, qps=1000.0, max_retries=0),
    )
    return provider, s_client, ark_client


def _imgs(body: dict) -> list[dict]:
    return [c for c in body["content"] if c.get("type") == "image_url"]


def _img_bytes(entry: dict) -> bytes:
    """data-URI image_url 条目 → 原始字节（byte 级比对哪张图被下发）。"""
    return base64.b64decode(entry["image_url"]["url"].split(",", 1)[1])


def _prewrite_frame_record(tmp_path: Path, *, frame_index: int, paths: list[Path]) -> None:
    """模拟生产时序：ai_video 在调 provider 前为本帧写的 ref_injection 台账。"""
    from app.gateway.video_gen.service import record_ref_injection_sidecar

    record_ref_injection_sidecar(
        tmp_path,
        frame_index=frame_index,
        declared_entities=["小林", "老王"],
        injected_refs=[{"entity": "preview", "path": str(paths[0]), "view": "preview"}, {"entity": "小林", "path": str(paths[1])}, {"entity": "老王", "path": str(paths[2])}],
        skipped=[],
        reason="",
        first_frame=True,
    )


class _QihuRateLimitedClient:
    """假 httpx client：360 submit 恒 429（``RateLimitError``——``_FALLBACK_EXC``
    可降级异常族，耗尽重试后由 Fallback 转 Ark 兜底）。"""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def post(self, url, **kw):  # noqa: ANN001, ANN003
        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=429, text="rate limited")

    async def get(self, url, **kw):  # noqa: ANN001, ANN003
        raise AssertionError("submit 未成功不应进入 poll")


@pytest.mark.asyncio
async def test_preview_first_truncated_to_single_image_type2(tmp_path) -> None:
    """规则 1：role 归一为 None 后 ≥2 图（preview_first 形态）→ 截单图（预览图）。

    content 恰 2 项（text + 单图）、extra_body 无 role（官方类型 2）；被弃参考图
    进 sidecar skipped（读改合并——既有 injected_refs 台账不得被抹，前端「参考
    资产」标签数据源）。"""
    provider, s_client, ark_client = _chain()
    preview, r1, r2 = _write_refs(tmp_path)
    _prewrite_frame_record(tmp_path, frame_index=3, paths=[preview, r1, r2])

    await provider.generate_video(
        "双人课程单镜",
        duration=5.0,
        image_urls=[str(preview), str(r1), str(r2)],
        role="first_frame_reference",
        task_dir=tmp_path,
        frame_index=3,
    )

    assert s_client.bodies and not ark_client.bodies, "主力成功不应走 Ark 兜底"
    body = s_client.bodies[0]
    assert len(body["content"]) == 2, f"官方类型 2 = text + 单图，实际 {len(body['content'])} 项"
    imgs = _imgs(body)
    assert len(imgs) == 1 and _img_bytes(imgs[0]) == PNG, "唯一下发图 byte 级 = 预览图（角色参考被弃）"
    assert "role" not in body.get("extra_body", {}), "类型 2 不设 role"

    rec = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))[0]
    assert rec["reason"] == "dropped_360_multi_ref"
    assert [i["entity"] for i in rec["injected_refs"]] == ["preview", "小林", "老王"], "既有注入台账保留（读改合并非整条替换）"
    assert [s["path"] for s in rec["skipped"]] == [str(r1), str(r2)], "被弃参考图路径留痕"


@pytest.mark.asyncio
async def test_r2v_reference_full_array_type6(tmp_path) -> None:
    """规则 2：role="reference"（r2v）→ 全量数组 + extra_body.role=reference（类型 6，现状不变）。"""
    provider, s_client, _ = _chain()
    preview, r1, r2 = _write_refs(tmp_path)

    await provider.generate_video("p", duration=5.0, image_urls=[str(preview), str(r1), str(r2)], role="reference")

    body = s_client.bodies[0]
    imgs = _imgs(body)
    assert len(imgs) == 3, f"r2v 全量下发，实际 {len(imgs)} 图"
    assert body["extra_body"]["role"] == "reference", "类型 6 须带全局 role=reference"


@pytest.mark.asyncio
async def test_single_preview_first_frame_type2_unchanged(tmp_path) -> None:
    """规则 3：单图 + role None（纯 preview 首帧）→ 单图下发（现状不变）。"""
    provider, s_client, _ = _chain()
    preview, _, _ = _write_refs(tmp_path)

    await provider.generate_video("p", duration=5.0, image_urls=[str(preview)], role=None)

    body = s_client.bodies[0]
    imgs = _imgs(body)
    assert len(imgs) == 1 and _img_bytes(imgs[0]) == PNG
    assert "role" not in body.get("extra_body", {})


@pytest.mark.asyncio
async def test_explicit_first_last_frames_passes_two_images(tmp_path) -> None:
    """规则 4：显式 role="first_last_frames"（vimax 双帧预留通道）→ 恰 2 图无 role 放行（类型 3）。

    ai_video 链路不产生该形态（2026-08-31 全仓核查）；本用例锁预留通道防回归。"""
    from app.gateway.video_gen.providers.seedance import ROLE_FIRST_LAST_FRAMES

    provider, s_client, _ = _chain()
    preview, r1, _ = _write_refs(tmp_path)

    await provider.generate_video("p", duration=5.0, image_urls=[str(preview), str(r1)], role=ROLE_FIRST_LAST_FRAMES)

    body = s_client.bodies[0]
    imgs = _imgs(body)
    assert len(imgs) == 2 and _img_bytes(imgs[0]) == PNG and _img_bytes(imgs[1]) == REF1, "双图按序放行（首帧+尾帧）"
    assert "role" not in body.get("extra_body", {}), "官方类型 3 = 双图无 role"


@pytest.mark.asyncio
async def test_first_last_frames_requires_exactly_two_images(tmp_path) -> None:
    """规则 4 误用护栏：first_last_frames ≠2 图 → 显式 ValueError（未定义形态不静默）。"""
    from app.gateway.video_gen.providers.seedance import ROLE_FIRST_LAST_FRAMES

    provider, _, _ = _chain()
    preview, r1, r2 = _write_refs(tmp_path)

    with pytest.raises(ValueError, match="恰 2 图"):
        await provider.generate_video("p", duration=5.0, image_urls=[str(preview), str(r1), str(r2)], role=ROLE_FIRST_LAST_FRAMES)


@pytest.mark.asyncio
async def test_submit_video_truncates_same_as_generate(tmp_path) -> None:
    """submit/poll 分离路径与 generate_video 同规——修前只修 generate 会给分离
    提交路径留下第二条未归一通路（qihu_video.py T3 同款教训）。生产上该路径由
    ``ThrottledAIVideoProvider.generate_video`` 内部触发（inner 暴露 submit/poll
    即分拆，throttle.py E2），本用例直调内层 submit_video 锁同规。"""
    from test_ai_video_preview_first_frame import _QihuCaptureClient

    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider

    inner = SeedanceVideoProvider(api_key="k", base_url="http://s360", poll_interval=0)
    client = _QihuCaptureClient()
    inner._client = client  # type: ignore[assignment]
    preview, r1, r2 = _write_refs(tmp_path)

    await inner.submit_video(
        "p",
        duration=5.0,
        image_urls=[str(preview), str(r1), str(r2)],
        role="first_frame_reference",
        task_dir=tmp_path,
        frame_index=0,
    )

    assert len(_imgs(client.bodies[0])) == 1, "submit 路径同样截单图"
    rec = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))[0]
    assert rec["reason"] == "dropped_360_multi_ref", "提交成功后留痕（后置写入——复核修复第二轮）"


@pytest.mark.asyncio
async def test_submit_rate_limited_fallback_ark_writes_no_false_dropped_sidecar(tmp_path) -> None:
    """复核修复（第二轮·问题1）：360 提交 429 耗尽重试 → Fallback 降级 Ark 全量兜底。

    Ark 以 ``first_frame + reference_image×(n-1)`` 使用**全部**参考图——成片未弃
    任何图，sidecar 不得虚报 ``dropped_360_multi_ref``（修前留痕先于 ``_submit``
    落盘，降级成功后台账失实，污染「参考资产」标签与降级排查口径）。"""
    from test_ai_video_preview_first_frame import _ArkCaptureClient

    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    s360 = SeedanceVideoProvider(api_key="k", base_url="http://s360", poll_interval=0)
    ark = SeedanceArkVideoProvider(api_key="k", base_url="http://ark", poll_interval=0, max_inflight=1)
    s_client, ark_client = _QihuRateLimitedClient(), _ArkCaptureClient()
    s360._client = s_client  # type: ignore[assignment]
    ark._client = ark_client  # type: ignore[assignment]
    provider = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(s360, qps=1000.0, max_retries=0),
        ThrottledAIVideoProvider(ark, qps=1000.0, max_retries=0),
    )
    preview, r1, r2 = _write_refs(tmp_path)
    _prewrite_frame_record(tmp_path, frame_index=3, paths=[preview, r1, r2])

    await provider.generate_video(
        "双人课程单镜",
        duration=5.0,
        image_urls=[str(preview), str(r1), str(r2)],
        role="first_frame_reference",
        task_dir=tmp_path,
        frame_index=3,
    )

    assert s_client.bodies and ark_client.bodies, "360 提交被 429 → Ark 兜底出片"
    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert [i["role"] for i in imgs] == ["first_frame", "reference_image", "reference_image"], "Ark 兜底全量使用参考图（首帧+参考）"

    rec = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))[0]
    assert rec["skipped"] == [] and rec["reason"] != "dropped_360_multi_ref", "降级 Ark 成片未弃任何参考图——不得虚报 dropped_360_multi_ref"
    assert [i["entity"] for i in rec["injected_refs"]] == ["preview", "小林", "老王"], "ai_video 预写台账原样保留"


def test_dropped_sidecar_tolerates_scalar_top_level_json(tmp_path) -> None:
    """复核修复（第二轮·问题2）：留痕文件为合法 JSON 但顶层非数组（外部损坏）。

    修前 ``for rec in records`` 对 ``42`` 抛 TypeError 穿透 ``except OSError``，
    经 throttle「只计数不重试」上抛 → 该帧退化为静态占位——违背「留痕失败不阻塞
    渲染」纪律。现在按无既有记录处理并自愈重写为数组。"""
    from app.gateway.video_gen.providers.seedance import _record_dropped_refs_sidecar

    sidecar_dir = tmp_path / "sidecar"
    sidecar_dir.mkdir()
    (sidecar_dir / "ref_injection.json").write_text("42", encoding="utf-8")

    _record_dropped_refs_sidecar(tmp_path, frame_index=0, kept="/p.png", dropped=["/a.png"])  # 不抛即通过

    data = json.loads((sidecar_dir / "ref_injection.json").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) == 1, "标量文件自愈重写为数组"
    assert data[0]["reason"] == "dropped_360_multi_ref"
    assert [s["path"] for s in data[0]["skipped"]] == ["/a.png"]
