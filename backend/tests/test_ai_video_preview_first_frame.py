"""机制 M（2026-08-31）：预览图 i2v 首帧 + 角色/道具参考**合并**（取代 08-17 互斥）。

⚠️ 2026-08-31 晚间订正：T2.0 探针「结果 a」（360 role 缺省多图 = 首图首帧 + 余参考）
是**错误结论**——探针模型错配（标准版而非生产 fast）且只比首帧灰度（尾部闪回漏检）。
官方文档定案 6 类型，**不存在「首帧+多图参考」形态**：360 通道 preview_first 多图
截单图（类型 2，弃参考图 sidecar 留痕）；Ark 走 role="first_frame_reference"
（first_frame + reference_image×n-1，Ark 原版 schema 合法）；H3 取首图=预览图。
详见 docs/plans/2026-08-31-360-seedance-official-gen-types-plan.md。

BUG-B 铁律：provider 行为测试一律走 ``FallbackAIVideoProvider(Throttled(...),
Throttled(...))`` 真实包裹链（生产 provider 从不裸奔），捕获点在假 httpx client 的
submit body——能力/接线在包裹链上失效时这里会红。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_video as mod
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 2048

_IR = {"subject": "一位讲师", "action": "讲解产品", "environment": "教室", "style": "写实"}


def _sb(frame: StoryboardFrame) -> Storyboard:
    return Storyboard(title="t", config=StoryboardConfig(video_type="ai_video", width=1920, height=1080), frames=[frame])


def _frame(**kw) -> StoryboardFrame:
    base = dict(index=0, narration="n", duration=5.0, image_prompt="p", visual_ir=dict(_IR))
    base.update(kw)
    return StoryboardFrame(**base)


@pytest.fixture()
def _no_localize(monkeypatch):
    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)


@pytest.fixture()
def _preview_file(tmp_path):
    (tmp_path / "visuals").mkdir(exist_ok=True)
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(PNG)
    return "visuals/frame_0000.png"


class _ArkCaptureClient:
    """假 httpx client：记录 Ark submit body，首 poll 即 succeeded。"""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def post(self, url, **kw):  # noqa: ANN001, ANN003
        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"id": "cgt-ark-1"})

    async def get(self, url, **kw):  # noqa: ANN001, ANN003
        return MagicMock(
            status_code=200,
            json=lambda: {"status": "succeeded", "content": {"video_url": "http://v/ark.mp4", "last_frame_url": "http://v/last.png"}},
        )


class _QihuCaptureClient:
    """假 httpx client：记录 360 submit body，首 poll 即 succeeded（seedance/H3 通用）。"""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list[dict] = []

    async def post(self, url, **kw):  # noqa: ANN001, ANN003
        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"data": {"id": "cgt-360-1"}})

    async def get(self, url, **kw):  # noqa: ANN001, ANN003
        return MagicMock(status_code=200, json=lambda: {"data": {"status": "succeeded", "url": "http://v/360.mp4", "extra_data": json.dumps({"content": {"last_frame_url": "http://v/l.png"}, "usage": {}})}})


def _fallback_chain():
    """生产 E3 同款包裹链形态：Fallback(Throttled(ark_primary), Throttled(qihu_fallback))。

    返回 (provider, ark_client, qihu_client)。
    """
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    ark = SeedanceArkVideoProvider(api_key="k", base_url="http://ark", poll_interval=0, max_inflight=1)
    qihu = QihuVideoProvider(api_key="k", base_url="http://q", model="minimax/MiniMax-H3", poll_interval=0)
    ark_client, qihu_client = _ArkCaptureClient(), _QihuCaptureClient()
    ark._client = ark_client  # type: ignore[assignment]
    qihu._client = qihu_client  # type: ignore[assignment]
    provider = FallbackAIVideoProvider(
        ThrottledAIVideoProvider(ark, qps=1000.0, max_retries=0),
        ThrottledAIVideoProvider(qihu, qps=1000.0, max_retries=0),
    )
    return provider, ark_client, qihu_client


# ── ① render 主链：合并顺序 + Ark 混合 role（真实包裹链）────────────────────


@pytest.mark.asyncio
async def test_merge_preview_first_then_character_refs_through_real_chain(tmp_path, _no_localize, _preview_file):
    """M：预览图恒放数组首位，角色合成图随后；经真实包裹链落在 Ark submit body 上
    为 first_frame + reference_image（混合映射）——修前二者互斥，角色图抢占时预览
    图被整体跳过。"""
    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    provider, ark_client, _ = _fallback_chain()

    frame = _frame(
        visible_characters=[{"name": "小林"}],
        visual_type="image",
        local_visual_path=_preview_file,
    )
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    assert ark_client.bodies, "Ark 主力应收到 submit"
    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 2, f"预览图+角色合成图合并下发，实际 {imgs}"
    assert imgs[0]["role"] == "first_frame", "首图（预览图）必须是 first_frame"
    assert imgs[0]["image_url"]["url"].startswith("data:image/")
    assert imgs[1]["role"] == "reference_image", "第二张（角色合成图）必须是 reference_image，不得回落 first+last 错配"
    # 方言措辞：混合分工指代（图片1=预览图首帧；带名时图片2=角色三视图）
    text = next(c["text"] for c in ark_client.bodies[0]["content"] if c.get("type") == "text")
    assert "图片1为本镜预览图" in text and "图片2为小林的三视图" in text, text


@pytest.mark.asyncio
async def test_no_preview_frame_keeps_legacy_behavior(tmp_path, _no_localize):
    """无预览图帧回退旧行为：character → role=reference → 全 reference_image。"""
    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    provider, ark_client, _ = _fallback_chain()

    frame = _frame(visible_characters=[{"name": "小林"}])
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert [i["role"] for i in imgs] == ["reference_image"], "无预览图：单角色参考保持 r2v 全参考映射（现状零变化）"


@pytest.mark.asyncio
async def test_seedance_360_mixed_role_omitted_all_images_sent(tmp_path, _no_localize, _preview_file):
    """2026-08-31 官方 6 类型归一（取代 T2.0 探针「结果 a」——该探针模型错配 +
    只比首帧灰度漏检尾部闪回）：360 seedance 收到 role="first_frame_reference"
    （preview_first 多图形态）时归一为**截单图**（预览图）下发——官方类型 2；
    全量下发的 2 图会被解释为首尾帧（三视图当尾帧 → 每镜尾部闪现）。
    弃参考图 dropped_360_multi_ref sidecar 留痕（角色一致性降级文字契约）。"""
    import json as _json

    from app.gateway.video_gen.providers.seedance import SeedanceVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    inner = SeedanceVideoProvider(api_key="k", base_url="http://s", poll_interval=0)
    client = _QihuCaptureClient()
    inner._client = client  # type: ignore[assignment]
    provider = ThrottledAIVideoProvider(inner, qps=1000.0, max_retries=0)

    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    frame = _frame(
        visible_characters=[{"name": "小林"}],
        visual_type="image",
        local_visual_path=_preview_file,
    )
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    body = client.bodies[0]
    imgs = [c for c in body["content"] if c.get("type") == "image_url"]
    assert len(body["content"]) == 2 and len(imgs) == 1, f"360 官方类型 2：text + 单图（预览图），实际 {body['content']}"
    assert imgs[0]["image_url"]["url"].startswith("data:image/"), "首图=预览图（data-URI 归一）"
    assert "role" not in body.get("extra_body", {}), "类型 2 不设 role"
    # 留痕：ai_video 先写的本帧 ref_injection 记录被读改合并——injected_refs 保留
    # （前端「参考资产」标签数据源），被弃参考图进 skipped + reason 改写。
    rec = _json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))[0]
    assert rec["reason"] == "dropped_360_multi_ref"
    assert rec["injected_refs"] and rec["injected_refs"][0]["entity"] == "preview", "既有注入台账不得被整条替换抹掉"
    assert any(s.get("path", "").endswith("chars/comp.png") and s.get("reason") == "dropped_360_multi_ref" for s in rec["skipped"]), rec["skipped"]


@pytest.mark.asyncio
async def test_h3_consumes_preview_only_and_flags_truncation(tmp_path, _no_localize, _preview_file):
    """H3 零改动口径：取首图=预览图（M 后首图恰是预览图），角色参考被弃并置
    multi_image_truncated → visual_coverage sidecar 留痕（角色一致性降级文字契约）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    inner = QihuVideoProvider(api_key="k", base_url="http://h3", model="minimax/MiniMax-H3", poll_interval=0)
    client = _QihuCaptureClient()
    inner._client = client  # type: ignore[assignment]
    provider = ThrottledAIVideoProvider(inner, qps=1000.0, max_retries=0)

    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    frame = _frame(
        visible_characters=[{"name": "小林"}],
        visual_type="image",
        local_visual_path=_preview_file,
    )
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="minimax-h3")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    imgs = [c for c in client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 1, "H3 只消费首图（预览图），其余参考被弃"
    assert imgs[0]["image_url"]["url"].startswith("data:image/")
    coverage = json.loads((tmp_path / "visual_coverage.json").read_text(encoding="utf-8"))
    assert coverage["character_ref_truncated"] is True, "截断降级须 sidecar 留痕（多图截断→角色一致性降级文字契约）"


# ── ② sidecar：first_frame 标记 + preview 条目 ────────────────────────────


@pytest.mark.asyncio
async def test_ref_injection_sidecar_marks_first_frame(tmp_path, _no_localize, _preview_file):
    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    provider, _, _ = _fallback_chain()

    frame = _frame(
        visible_characters=[{"name": "小林"}],
        visual_type="image",
        local_visual_path=_preview_file,
    )
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    rec = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))[0]
    assert rec["first_frame"] is True
    assert rec["injected_refs"][0]["entity"] == "preview", "预览图来源记录为 preview（首位与下发数组同序）"
    assert rec["injected_refs"][1]["entity"] == "小林"


# ── ③ 拒图重试梯子：混合 kind 仅丢首帧保角色参考 ──────────────────────────


class _RejectThenSucceedArkClient(_ArkCaptureClient):
    """首图含预览图的 submit 400（10940 图片内容检测），第二次成功。

    第一次 body 带 2 图（预览+角色），降级重试 body 应只剩角色图。
    """

    def __init__(self, reject_first_n: int = 1) -> None:
        super().__init__()
        self._left = reject_first_n

    async def post(self, url, **kw):  # noqa: ANN001, ANN003
        self.bodies.append(kw.get("json") or {})
        if self._left > 0:
            self._left -= 1
            return MagicMock(
                status_code=400,
                text='{"error":{"code":"10940","message":"InputImageSensitiveContentDetected"}}',
            )
        return MagicMock(status_code=200, json=lambda: {"id": "cgt-ark-2"})


@pytest.mark.asyncio
async def test_preview_rejected_drops_first_frame_keeps_character_refs(tmp_path, _no_localize, _preview_file):
    """混合 kind 拒图梯子（核查反馈 3b）：首帧（预览图）被拒 → 仅丢首帧、保留角色
    参考降级 r2v 重试（role=reference 全参考映射）+ sidecar 记 first_frame_dropped。"""
    from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    ark = SeedanceArkVideoProvider(api_key="k", base_url="http://ark", poll_interval=0, max_inflight=1)
    client = _RejectThenSucceedArkClient()
    ark._client = client  # type: ignore[assignment]
    provider = FallbackAIVideoProvider(ThrottledAIVideoProvider(ark, qps=1000.0, max_retries=0), ThrottledAIVideoProvider(ark, qps=1000.0, max_retries=0))

    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "comp.png").write_bytes(PNG)
    frame = _frame(
        visible_characters=[{"name": "小林"}],
        visual_type="image",
        local_visual_path=_preview_file,
    )
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(
        _sb(frame),
        task_dir=tmp_path,
        character_roster={"小林": {"composite": str(tmp_path / "chars" / "comp.png")}},
    )

    assert len(client.bodies) == 2, "首帧被拒应触发一次降级重试"
    retry_imgs = [c for c in client.bodies[1]["content"] if c.get("type") == "image_url"]
    assert len(retry_imgs) == 1 and retry_imgs[0]["role"] == "reference_image", "重试只带角色参考（r2v），预览图已丢"
    text = next(c["text"] for c in client.bodies[1]["content"] if c.get("type") == "text")
    assert "图片1为本镜预览图" not in text, "预览图首帧指代不得残留在重试 prompt 里"
    from app.gateway.video_gen.degradations import read_degradations

    degs = read_degradations(tmp_path)
    assert any("first_frame_dropped: content_reject" in str(d.get("detail", "")) for d in degs), degs


# ── ④ _assign_roles 混合模式（n=1/2/4）+ 纯函数与方言措辞 ──────────────────


def test_assign_roles_mixed_mode():
    from app.gateway.video_gen.providers.seedance_ark import _assign_roles

    assert _assign_roles(1, first_frame_plus_refs=True) == ["first_frame"]
    assert _assign_roles(2, first_frame_plus_refs=True) == ["first_frame", "reference_image"], "n=2 不得走默认 first+last（第二张是参考不是尾帧）"
    assert _assign_roles(4, first_frame_plus_refs=True) == ["first_frame"] + ["reference_image"] * 3
    # 默认分支零回归（互不干扰）
    assert _assign_roles(2) == ["first_frame", "last_frame"]
    assert _assign_roles(3, entity_refs=True) == ["reference_image"] * 3


def test_seedance_dialect_preview_first_wording():
    from app.gateway.video_gen.prompt_translators import vocab

    named = vocab.seedance_reference_injection(3, "preview_first", ["小林", "魔方"], 1)
    assert "图片1为本镜预览图" in named
    assert "图片2为小林的三视图" in named and "图片3为魔方的产品参考图" in named, "names 对应图片 2..n（不含预览图）"

    plain = vocab.seedance_reference_injection(2, "preview_first")
    assert "图片1为本镜预览图" in plain and "参考" in plain

    # 旧 kind 措辞零回归
    assert "图片1为本镜预览图" in vocab.seedance_reference_injection(1, "preview")
    assert "三视图" in vocab.seedance_reference_injection(1, "character", ["小林"])
