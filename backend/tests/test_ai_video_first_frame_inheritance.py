"""Task 5: 预览图作 i2v 首帧继承构图。"""

import base64
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_video as mod
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _sb(local: str | None) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    f = StoryboardFrame(index=0, narration="n", duration=5.0, image_prompt="p", local_visual_path=local, visual_type="image")
    return Storyboard(title="t", config=cfg, frames=[f])


@pytest.fixture()
def _no_localize(monkeypatch):
    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)


# ── P2-1（2026-08-31）：下列 3 测试由裸 MagicMock provider 迁移到真实包裹链 ──
# BUG-B 铁律：M 的核心新断言（预览图首位 + 混合 role）恰好落在这 3 处，裸 mock
# 声明 supports_image_urls 等属性会绕过 Throttle/Fallback 包裹层——测的是生产
# 里不存在的配置。现借道 tests/test_ai_video_preview_first_frame.py 的
# _fallback_chain()（生产 E3 同款 Fallback(Throttled(ark), Throttled(qihu))，
# 捕获点在假 httpx client 的 submit body；tests/ 无 __init__.py，同目录裸模块
# 名经 pytest basedir sys.path 插入可导）。断言语义逐条保留（byte 级比对强化）。


def _img_bytes(entry: dict) -> bytes:
    """data-URI image_url 条目 → 原始字节（byte 级比对哪张图被下发）。"""
    return base64.b64decode(entry["image_url"]["url"].split(",", 1)[1])


@pytest.mark.asyncio
async def test_preview_image_passed_as_first_frame(tmp_path, _no_localize) -> None:
    from test_ai_video_preview_first_frame import PNG, _fallback_chain

    provider, ark_client, qihu_client = _fallback_chain()
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(PNG)
    sb = _sb("visuals/frame_0000.png")
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(sb, task_dir=tmp_path)
    assert ark_client.bodies and not qihu_client.bodies, "主力成功不应走 360 兜底"
    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 1 and imgs[0]["role"] == "first_frame", f"预览图应作首帧传入，实际 {imgs}"
    assert _img_bytes(imgs[0]) == PNG, "首帧字节 = 预览图文件内容（byte 级确认传的就是 frame_0000.png）"


@pytest.mark.asyncio
async def test_character_refs_merge_with_preview_first(tmp_path, _no_localize) -> None:
    """机制 M（2026-08-31，取代 08-17 互斥决策）：预览图恒放数组首位、角色参考随后
    追加——修前有角色参考时预览图被整体跳过（构图继承丢失）。角色图不会再被
    _assign_roles(2) 错配成尾帧：混合形态走 role="first_frame_reference"（Ark
    first_frame+reference_image；360 探针结果 a 全量下发；H3 取首图=预览图）。
    P2-1 迁移：role kwarg 在真链上不可见，其可观察契约 = Ark submit body 的
    first_frame + reference_image 混合映射（byte 级确认两图各归其位）。"""
    from test_ai_video_preview_first_frame import PNG, _fallback_chain

    provider, ark_client, qihu_client = _fallback_chain()
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(PNG)
    ref_bytes = PNG + b"-ref"  # 与预览图字节可区分，byte 比对才具判别力
    (tmp_path / "chars").mkdir()
    (tmp_path / "chars" / "f.png").write_bytes(ref_bytes)
    sb = _sb("visuals/frame_0000.png")
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(sb, task_dir=tmp_path, reference_image_paths=[str(tmp_path / "chars" / "f.png")])
    assert len(ark_client.bodies) == 1 and not qihu_client.bodies, f"应恰好一次 submit 且不走兜底，实际 ark={len(ark_client.bodies)} qihu={len(qihu_client.bodies)}"
    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 2 and imgs[0]["role"] == "first_frame" and imgs[1]["role"] == "reference_image", f"预览图（首位，first_frame）+ 角色参考（随后，reference_image），实际 {imgs}"
    assert _img_bytes(imgs[0]) == PNG and _img_bytes(imgs[1]) == ref_bytes, "两图 byte 级确认：首位=预览图、第二=角色参考"


@pytest.mark.asyncio
async def test_no_preview_no_refs_stays_t2v(tmp_path, _no_localize) -> None:
    from test_ai_video_preview_first_frame import _fallback_chain

    provider, ark_client, qihu_client = _fallback_chain()
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="seedance-ark")
    await src.render(_sb(None), task_dir=tmp_path)
    assert len(ark_client.bodies) == 1 and not qihu_client.bodies
    imgs = [c for c in ark_client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert imgs == [], "无预览无参考 → 纯 t2v，不得下发任何 image_url"


@pytest.mark.asyncio
async def test_provider_without_image_urls_ignores_preview(tmp_path, _no_localize) -> None:
    """不支持 image_urls 的 provider（360-only 部署）不得收到首帧。"""
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(image_urls)
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=8.0, supports_image_urls=False), concurrency=1)
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)
    assert seen == [None]


@pytest.mark.asyncio
async def test_unsupported_provider_records_degradation(tmp_path, _no_localize) -> None:
    """不支持 i2v 的 provider → 写 sidecar 告知，而非静默回退（R3/QP1-4 教训）。"""

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=8.0, supports_image_urls=False), concurrency=1)
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)
    blob = " ".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "i2v" in blob or "图生视频" in blob, "provider 不支持 i2v 时应写降级 sidecar"


@pytest.mark.asyncio
async def test_supported_provider_records_nothing(tmp_path, _no_localize) -> None:
    """默认 provider（seedance-ark）支持 i2v → 不该给多数用户制造无意义提示。"""

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True), concurrency=1)
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)
    blob = " ".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "图生视频" not in blob


@pytest.mark.asyncio
async def test_missing_preview_file_not_passed(tmp_path, _no_localize) -> None:
    """local_visual_path 指向不存在的文件（任务目录被清理）→ 不传，走纯 t2v。"""
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(image_urls)
        return MagicMock(url="http://v/1")

    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True), concurrency=1)
    await src.render(_sb("visuals/gone.png"), task_dir=tmp_path)
    assert seen == [None]


@pytest.mark.asyncio
async def test_already_video_frame_not_used_as_first_frame(tmp_path, _no_localize) -> None:
    """visual_type=video 的既有本地视频不是预览图，不得当图片首帧下发。"""
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(image_urls)
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "shot_0000.mp4").write_bytes(b"x" * 2048)
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    f = StoryboardFrame(index=0, narration="n", duration=5.0, image_prompt="p", local_visual_path="visuals/shot_0000.mp4", visual_type="video")
    sb = Storyboard(title="t", config=cfg, frames=[f])
    src = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True), concurrency=1)
    await src.render(sb, task_dir=tmp_path)
    assert seen == [None]


# ── T3（2026-08-18）：真实 QihuVideoProvider(H3) 经 Throttle 包裹链（生产形态）──
# 记忆教训 provider-capability-must-test-through-wrappers：能力/接线测试必须走
# 真实包裹链，裸 MagicMock 声明属性会假绿生产失效。


class _CapturingSubmitClient:
    """记录 submit POST body 的假 httpx client（首 poll 即 succeeded）。"""

    is_closed = False

    def __init__(self) -> None:
        self.bodies: list = []

    async def post(self, url, **kw):  # noqa: ANN001,ANN003
        from unittest.mock import MagicMock

        self.bodies.append(kw.get("json") or {})
        return MagicMock(status_code=200, json=lambda: {"data": {"id": "task_h3"}})

    async def get(self, url, **kw):  # noqa: ANN001,ANN003
        from unittest.mock import MagicMock

        return MagicMock(
            status_code=200,
            json=lambda: {"data": {"status": "succeeded", "url": "http://v/h3.mp4"}},
        )


@pytest.mark.asyncio
async def test_real_h3_provider_through_throttle_inherits_preview(tmp_path, _no_localize) -> None:
    """T3 端到端：H3 声明+image_urls 消费+data-URI 归一三段齐活后，预览图继承
    经真实「Throttle(QihuVideoProvider)」包裹链生效（生产 provider 一律被包裹）。"""
    from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
    from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider

    inner = QihuVideoProvider(api_key="k", base_url="http://b", model="minimax/MiniMax-H3", poll_interval=0)
    client = _CapturingSubmitClient()
    inner._client = client  # type: ignore[assignment]
    provider = ThrottledAIVideoProvider(inner, qps=1000.0, max_retries=0)

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 2048)
    src = AIVideoVisualSource(provider, concurrency=1, provider_name="minimax-h3")
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)

    assert client.bodies, "应发出 submit POST"
    imgs = [c for c in client.bodies[0]["content"] if c.get("type") == "image_url"]
    assert len(imgs) == 1, f"预览图应作首帧下发，实际 content={client.bodies[0]['content']}"
    assert imgs[0]["image_url"]["url"].startswith("data:image/"), "本地预览图应 data-URI 化（远端抓不到本地路径）"
    assert "role" not in imgs[0], "H3 首帧形态无 role（官方示例9）"


# ── T3（2026-08-18）：seedance-360 role 语义映射；2026-08-19 探针后 preview 开闸 ──


@pytest.mark.asyncio
async def test_role_mapped_by_reference_kind(tmp_path, _no_localize) -> None:
    """T3 role 语义映射（360 端 role 是 extra_body 全局生成类型标识，无 per-image
    role）：character→role="reference"（r2v 多参考，现状）；preview→role=None
    （首帧 i2v，官方 seedance 示例2 口径——修前 preview 会被错标 r2v）。"""
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, role="__unset__", **kw):  # noqa: ANN001,ANN003
        seen.append((image_urls, role))
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)

    # character 路径：角色三视图 → r2v 参考（M 起无预览图的帧才是纯 character 形态；
    # 有预览图时走 preview_first 合并，见 test_character_refs_merge_with_preview_first）
    src_char = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True), concurrency=1)
    await src_char.render(_sb(None), task_dir=tmp_path, reference_image_paths=["/f.png"])

    # preview 路径：预览图继承 → 首帧模式
    src_prev = AIVideoVisualSource(MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True), concurrency=1)
    await src_prev.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)

    assert seen[0] == (["/f.png"], "reference"), f"character 应传 role=reference，实际 {seen[0]}"
    assert seen[1][1] is None, f"preview 应不设 role（首帧模式），实际 {seen[1]}"
    assert seen[1][0] and seen[1][0][0].endswith("frame_0000.png")


@pytest.mark.asyncio
async def test_seedance_360_preview_inheritance_on_after_probe(tmp_path, _no_localize) -> None:
    """2026-08-19 实网探针（任务 cgt-20260819215346-fz2cw）验证 360 seedance
    role=None 首帧继承成立（首帧人物/构图/配色与预览图一致）——「预览即所得」
    开闸，探针前默认关的排除条件移除；图片被误拦由丢图降级梯子兜底。"""
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, role="__unset__", **kw):  # noqa: ANN001,ANN003
        seen.append((image_urls, role))
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    src = AIVideoVisualSource(
        MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True),
        concurrency=1,
        provider_name="seedance",
    )
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)
    assert len(seen) == 1 and seen[0][0] and seen[0][0][0].endswith("frame_0000.png"), f"开闸后 seedance-360 预览图应作首帧下发，实际 {seen}"
    assert seen[0][1] is None, f"preview 应不设 role（首帧 i2v），实际 {seen[0]}"
    from app.gateway.video_gen import degradations as deg

    assert deg.KIND_PREVIEW_I2V_UNSUPPORTED not in [i["kind"] for i in deg.read_degradations(tmp_path)], "开闸后不该再误报 preview_i2v_unsupported"


@pytest.mark.asyncio
async def test_seedance_360_preview_inheritance_env_zero_rolls_back(tmp_path, _no_localize, monkeypatch) -> None:
    """F5 回退位：VIDEO_GEN_PREVIEW_INHERIT=0 → 预览继承一键关断（不发版全量
    回退），维持既有「诚实降级 + sidecar 告知」路径，不得静默丢图。"""
    monkeypatch.setenv("VIDEO_GEN_PREVIEW_INHERIT", "0")
    seen: list = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(image_urls)
        return MagicMock(url="http://v/1")

    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    src = AIVideoVisualSource(
        MagicMock(generate_video=fake_gen, max_video_duration=15.0, supports_image_urls=True),
        concurrency=1,
        provider_name="seedance",
    )
    await src.render(_sb("visuals/frame_0000.png"), task_dir=tmp_path)
    assert seen == [None], "env=0：预览继承关断，不得下发首帧"
    from app.gateway.video_gen import degradations as deg

    assert deg.KIND_PREVIEW_I2V_UNSUPPORTED in [i["kind"] for i in deg.read_degradations(tmp_path)], "关断也须走既有降级提示（诚实告知，不静默）"
