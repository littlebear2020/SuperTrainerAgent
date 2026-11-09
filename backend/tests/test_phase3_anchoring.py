"""Phase 3 锚定（2026-08-17）：T-S1 文本锚定指令 + T-S2 组首帧参考锚定。

T-S1：course 系统提示要求同组复用锚定场景与道具（E3 验证 -41%）。
T-S2：两阶段渲染（组首先行 -> 抽锚 -> 组员带锚参考，星型）。
2026-08-18 T6 拍板③：默认开（VIDEO_GEN_GROUP_ANCHOR=0 一键回退）；
2026-08-19 探针验证通过，seedance-360 排除移除（对齐 T3 preview 闸开闸）；
与角色图/预览图互斥。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame


class _CapLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages.append(messages)

        class _Resp:
            content = self._content

        return _Resp()


@pytest.mark.asyncio
async def test_ts1_anchoring_instruction_in_course_prompt():
    llm = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_video"}]}')
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n")],
    )
    await planner._generate_course_prompts_enhanced(llm, sb, "")
    sys = " ".join(str(m.get("content", "")) for m in llm.messages[0])
    assert "锚定规则" in sys and "锚定场景" in sys, "T-S1 文本锚定指令须在场"
    assert "跨分组才更换场景" in sys


# ── T-S2 ───────────────────────────────────────────────────────────────


class _AnchorFakeProvider:
    """记录调用顺序与 image_urls；supports_image_urls=True。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def supports_image_urls(self) -> bool:
        return True

    async def generate_video(self, prompt: str, *, duration: float = 5.0, image_urls=None, **kw):
        self.calls.append({"prompt": prompt, "image_urls": list(image_urls or [])})
        return VideoRef(url="http://v/1.mp4", duration=duration, source="test")


def _grouped_sb() -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=1920, height=1080)
    # visual_ir 非空（F3 要点，2026-08-19）：seedance 方言要求 IR 至少一个非空
    # 字段才走五槽拼装，否则 legacy 透传忽略 reference 参数——组锚被拦用例的
    # 「图片1」prompt 断言会恒真空转。
    return Storyboard(
        title="t",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="n0", image_prompt="p0", duration=4.0, visual_ir={"subject": "办公室白板前", "action": "工程师讲解架构"}),
            StoryboardFrame(index=1, narration="n1", image_prompt="p1", duration=4.0, visual_ir={"subject": "同一间办公室", "action": "镜头切到屏幕特写"}),
        ],
        scene_groups=[SceneGroup(index=0, title="g", frame_indices=[0, 1])],
    )


def _patch_localize(monkeypatch, tmp_path):  # noqa: ANN001, ANN202
    """跳过网络本地化：直接落一个假 mp4；probe 返回 4.0；抽帧写真 jpg。"""

    async def fake_localize(url, frame, td, dest_name=None):
        rel = f"visuals/shot_{frame.index:04d}.mp4"
        f = td / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"mp4")
        return rel

    def fake_probe(p):  # noqa: ANN001, ARG001
        return 4.0

    def fake_extract(video_path, jpg_path):  # noqa: ANN001, ARG001
        jpg_path.parent.mkdir(parents=True, exist_ok=True)
        jpg_path.write_bytes(b"jpg")
        return True

    import app.gateway.video_gen.visual_sources.ai_video as aiv

    monkeypatch.setattr(aiv, "_localize_video", fake_localize)
    monkeypatch.setattr(aiv, "_probe_video_seconds", fake_probe)
    monkeypatch.setattr(aiv, "_extract_anchor_image", fake_extract)


@pytest.mark.asyncio
async def test_ts2_default_on_without_env(monkeypatch, tmp_path):
    """T6 拍板③：未设 env → 默认开（星型组锚注入）。默认被翻回 off 该用例即红。"""
    monkeypatch.delenv("VIDEO_GEN_GROUP_ANCHOR", raising=False)
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_grouped_sb(), task_dir=tmp_path)
    assert len(provider.calls) == 2
    # 星型：组首（帧0）无参考；组员（帧1）带组锚
    assert provider.calls[0]["image_urls"] == []
    assert provider.calls[1]["image_urls"] == [str(tmp_path / "anchor_g0.jpg")]


@pytest.mark.asyncio
async def test_ts2_env_zero_rolls_back(monkeypatch, tmp_path):
    """回退位：VIDEO_GEN_GROUP_ANCHOR=0 → 不注入组锚（拍板③保留的 env=0 一键回退）。"""
    monkeypatch.setenv("VIDEO_GEN_GROUP_ANCHOR", "0")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_grouped_sb(), task_dir=tmp_path)
    assert len(provider.calls) == 2
    assert all(c["image_urls"] == [] for c in provider.calls), "env=0：不注入组锚"


@pytest.mark.asyncio
async def test_ts2_seedance_360_anchor_on_after_probe(monkeypatch, tmp_path):
    """2026-08-19 探针验证通过（对齐 T3 preview 闸开闸）：seedance-360 组锚注入
    开闸——星型形态同 test_ts2_default_on_without_env（组首无参考、组员带锚）。"""
    monkeypatch.delenv("VIDEO_GEN_GROUP_ANCHOR", raising=False)
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="seedance")
    await src.render(_grouped_sb(), task_dir=tmp_path)
    assert len(provider.calls) == 2
    assert provider.calls[0]["image_urls"] == []
    assert provider.calls[1]["image_urls"] == [str(tmp_path / "anchor_g0.jpg")]


@pytest.mark.asyncio
async def test_ts2_env_zero_rolls_back_seedance(monkeypatch, tmp_path):
    """回退位对 seedance-360 同样生效：VIDEO_GEN_GROUP_ANCHOR=0 → 不注入组锚。"""
    monkeypatch.setenv("VIDEO_GEN_GROUP_ANCHOR", "0")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="seedance")
    await src.render(_grouped_sb(), task_dir=tmp_path)
    assert len(provider.calls) == 2
    assert all(c["image_urls"] == [] for c in provider.calls), "env=0：seedance-360 不注入组锚"


class _AnchorRejectFakeProvider:
    """组锚被 10940 图片内容检测拦的假 provider：image_urls 非空抛、空则成功。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    @property
    def supports_image_urls(self) -> bool:
        return True

    async def generate_video(self, prompt: str, *, duration: float = 5.0, image_urls=None, **kw):
        self.calls.append({"prompt": prompt, "image_urls": list(image_urls or [])})
        if image_urls:
            raise RuntimeError('360 seedance submit failed (400): {"error":{"code":10940,"message":"InputImageSensitiveContentDetected.PrivacyInformation"}}')
        return VideoRef(url="http://v/1.mp4", duration=duration, source="test")


@pytest.mark.asyncio
async def test_ts2_anchor_rejected_drops_anchor_retries_pure_t2v(monkeypatch, tmp_path):
    """P2-2（2026-08-19）：组锚图被 10940 拦 → 丢锚降级纯 t2v 重试成功；sidecar
    detail 为组锚措辞（锁定修正 C 的 else 支，此前无测试覆盖）。"""
    monkeypatch.delenv("VIDEO_GEN_GROUP_ANCHOR", raising=False)
    from app.gateway.video_gen import degradations as deg
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorRejectFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="seedance")
    await src.render(_grouped_sb(), task_dir=tmp_path)

    # 组首 1 次成功（无锚）；组员 2 次（带锚被拦 → 丢锚纯 t2v 重试成功）
    assert len(provider.calls) == 3, f"组首 1 次 + 组员被拦重试共 3 次，实际 {len(provider.calls)}"
    assert provider.calls[0]["image_urls"] == []
    assert provider.calls[1]["image_urls"] == [str(tmp_path / "anchor_g0.jpg")]
    assert provider.calls[2]["image_urls"] == [], "丢锚重试应为纯 t2v"
    assert "图片1" in provider.calls[1]["prompt"], f"组锚方言应注入参考图指代，实际 {provider.calls[1]['prompt']}"
    assert "图片1" not in provider.calls[2]["prompt"], f"丢锚重试须按 reference_count=0 重翻译，实际 {provider.calls[2]['prompt']}"
    items = deg.read_degradations(tmp_path)
    entry = next((i for i in items if i["kind"] == deg.KIND_PREVIEW_IMAGE_REJECTED), None)
    assert entry is not None, f"应记 preview_image_rejected，实际 {[i['kind'] for i in items]}"
    assert entry["frame_index"] == 1
    assert "组锚参考图" in entry["detail"], f"detail 须组锚措辞（修正 C else 支），实际 {entry['detail']}"


@pytest.mark.asyncio
async def test_ts2_on_group_first_then_anchor(monkeypatch, tmp_path):
    monkeypatch.setenv("VIDEO_GEN_GROUP_ANCHOR", "1")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_grouped_sb(), task_dir=tmp_path)
    assert len(provider.calls) == 2
    # 星型：组首（帧0）无参考；组员（帧1）带组锚
    assert provider.calls[0]["image_urls"] == []
    assert provider.calls[1]["image_urls"] == [str(tmp_path / "anchor_g0.jpg")]


@pytest.mark.asyncio
async def test_ts2_mutual_exclusion_with_character_refs(monkeypatch, tmp_path):
    """角色三视图在场 -> 组锚让位（冲突保角色一致性，同预览图继承先例）。"""
    monkeypatch.setenv("VIDEO_GEN_GROUP_ANCHOR", "1")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    _patch_localize(monkeypatch, tmp_path)
    provider = _AnchorFakeProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_grouped_sb(), task_dir=tmp_path, reference_image_paths=["/proj/front.png"])
    assert provider.calls[0]["image_urls"] == ["/proj/front.png"]
    assert provider.calls[1]["image_urls"] == ["/proj/front.png"], "角色图优先，组锚不叠加"


def test_extract_anchor_missing_file_false(tmp_path):
    from app.gateway.video_gen.visual_sources.ai_video import _extract_anchor_image

    assert _extract_anchor_image(tmp_path / "nope.mp4", tmp_path / "a.jpg") is False


# ── T6（2026-08-18 拍板③）：T-S2 对 H3 经真实包裹链（Throttle 真链）─────────
# 记忆教训 provider-capability-must-test-through-wrappers：能力/接线测试必须走
# 真实包裹链（Throttle/Fallback 无 __getattr__ 兜底，裸 fake 会假绿生产失效）。
# _CapturingSubmitClient 与 test_ai_video_first_frame_inheritance.py 同款孪生。


class _CapturingSubmitClient:
    """记录 submit POST body 的假 httpx client（首 poll 即 succeeded）。"""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list = []

    async def post(self, url, **kw):  # noqa: ANN001, ANN003
        from unittest.mock import MagicMock

        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"data": {"id": "task_h3"}})

    async def get(self, url, **kw):  # noqa: ANN001, ANN003
        from unittest.mock import MagicMock

        return MagicMock(
            status_code=200,
            json=lambda: {"data": {"status": "succeeded", "url": "http://v/h3.mp4"}},
        )


@pytest.mark.asyncio
async def test_ts2_real_h3_through_throttle_delivers_anchor(monkeypatch, tmp_path):
    """T6 端到端：默认开的组锚经真实「Throttle(QihuVideoProvider H3)」包裹链生效——
    组首（帧0）纯 t2v 无图；组员（帧1）锚图 data-URI 化下发（360 远端抓不到本地
    路径，官方「URL 确保可访问或 Base64」口径）、H3 首帧形态无 role。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    monkeypatch.delenv("VIDEO_GEN_GROUP_ANCHOR", raising=False)
    _patch_localize(monkeypatch, tmp_path)

    inner = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)
    client = _CapturingSubmitClient()
    inner._client = client  # type: ignore[assignment]
    provider = ThrottledAIVideoProvider(inner, qps=1000.0, max_retries=0)

    src = AIVideoVisualSource(provider, concurrency=1, provider_name="minimax-h3")
    await src.render(_grouped_sb(), task_dir=tmp_path)

    assert len(client.bodies) == 2, f"两帧各一次 submit，实际 {len(client.bodies)}"
    first_imgs = [c for c in client.bodies[0]["content"] if c.get("type") == "image_url"]
    second_imgs = [c for c in client.bodies[1]["content"] if c.get("type") == "image_url"]
    assert first_imgs == [], "组首帧必须先行无参考（锚图从它的成片抽取）"
    assert len(second_imgs) == 1, f"组员应恰带一张锚图，实际 content={client.bodies[1]['content']}"
    assert second_imgs[0]["image_url"]["url"].startswith("data:image/"), "本地锚图须 data-URI 化（远端抓不到本地路径）"
    assert "role" not in second_imgs[0], "H3 组锚形态无 role（组锚=首帧 i2v 语义，官方示例9 口径）"
