"""Task 12: 内容审核失败 → 中性化提示词重试一次。"""

from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.providers.content_review import is_content_review_rejection, neutralize_prompt


def test_detects_content_review_errors() -> None:
    assert is_content_review_rejection(RuntimeError("green net check failed"))
    assert is_content_review_rejection(RuntimeError("InputImageSensitiveContentDetected"))
    assert is_content_review_rejection(RuntimeError("inappropriate content detected"))


def test_ignores_unrelated_errors() -> None:
    assert not is_content_review_rejection(RuntimeError("connection reset"))
    assert not is_content_review_rejection(TimeoutError("timed out"))


def test_neutralize_strips_risky_terms() -> None:
    out = neutralize_prompt("一名警察持枪追捕嫌犯，血迹斑斑")
    assert "枪" not in out
    assert "血" not in out
    assert out.strip(), "中性化后不得为空"


def test_neutralize_keeps_benign_prompt_usable() -> None:
    original = "一位工程师在白板前讲解架构图"
    out = neutralize_prompt(original)
    assert "工程师" in out and "白板" in out


def test_neutralize_is_idempotent() -> None:
    once = neutralize_prompt("持枪对峙")
    assert neutralize_prompt(once) == once


def test_vimax_bridge_reuses_shared_predicate() -> None:
    """正则单一来源——vimax 侧不得保留自己的副本（两份会漂移）。"""
    from app.gateway.video_gen.engines.vimax import provider_bridge as pb

    assert pb._is_content_review_rejection is is_content_review_rejection


# ---------------------------------------------------------------------------
# ai_video 接入：同 provider 换中性化说法重试一次
# ---------------------------------------------------------------------------


def _sb(prompt: str = "一名警察持枪追捕嫌犯", narration: str = "n"):  # noqa: ANN202
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration=narration, duration=5.0, image_prompt=prompt)])


class _FakeVideoProvider:
    """手写 fake provider（BUG-B 纪律：裸 MagicMock 未声明属性自动 truthy，
    测不出能力声明缺失/接线断裂——先例见 test_phase3_anchoring.py 的
    _AnchorFakeProvider）。只声明 AIVideoVisualSource 实际读取的能力属性。"""

    def __init__(self, generate_video):  # noqa: ANN001
        self.generate_video = generate_video
        self.max_video_duration = 15.0
        self.min_video_duration = 4.0
        self.integer_seconds_only = True
        self.supports_image_urls = True


def _src(fake_gen):  # noqa: ANN001,ANN202
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider(fake_gen)
    return AIVideoVisualSource(provider, concurrency=1)


@pytest.fixture()
def _no_localize(monkeypatch):
    from app.gateway.video_gen.visual_sources import ai_video as mod

    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)


@pytest.mark.asyncio
async def test_content_review_triggers_neutralized_retry(tmp_path, _no_localize) -> None:
    """首次审核拒绝 → 换中性化提示词重试一次并成功，用户无需手改词。"""
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        if len(seen) == 1:
            raise RuntimeError("green net check failed: inappropriate content")
        return MagicMock(url="http://v/1")

    sb = _sb()
    await _src(fake_gen).render(sb, task_dir=tmp_path)
    assert len(seen) == 2, f"应重试一次，实际调用 {len(seen)} 次"
    assert "枪" not in seen[1], f"重试应使用中性化提示词，实际 {seen[1]}"
    assert sb.frames[0].visual_url == "http://v/1", "重试成功后必须写回结果"


@pytest.mark.asyncio
async def test_non_review_error_not_retried(tmp_path, _no_localize) -> None:
    """基础设施故障不该借道本机制重试（成本翻倍且无助于通过审核）。"""
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        raise RuntimeError("connection reset by peer")

    # 单帧全失败 → 覆盖率 0% 触发既有的响亮失败（B2/Task 9），与本 task 无关，
    # 但本测试关心的是「调了几次 generate_video」，故容许该异常。
    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src(fake_gen).render(_sb(), task_dir=tmp_path)
    assert len(seen) == 1, f"非审核错误不得重试，实际调用 {len(seen)} 次"


@pytest.mark.asyncio
async def test_nothing_to_neutralize_keeps_surfacing(tmp_path, _no_localize) -> None:
    """无可中性化处**且旁白为空** → 保持原有「上浮给用户」行为，不做无意义重试。

    2026-08-19（P1）起旁白非空时改走旁白直述兜底重试（见
    test_false_positive_retries_with_narration_plain_prompt）；「上浮」语义
    由空旁白变体继续守护。
    """
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        raise RuntimeError("content policy violation")

    # 同上：单帧全失败触发既有的覆盖率响亮失败，本测试只关心调用次数。
    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src(fake_gen).render(_sb("一位工程师在白板前讲解", narration=""), task_dir=tmp_path)
    assert len(seen) == 1, f"无可中性化且无旁白时不重试，实际调用 {len(seen)} 次"


@pytest.mark.asyncio
async def test_retry_records_disclosure_sidecar(tmp_path, _no_localize) -> None:
    """P0-2（2026-08-03）：改词成功后必须写 sidecar 告知用户。

    系统自动把「持枪」改成「持械」再生成，成片画面与用户所写提示词不一致；
    只记 logger 就是又一次「默默降级不告知」（Task 4/5 反复在修的正是这类缺陷）。
    """
    from app.gateway.video_gen import degradations as deg

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        if "枪" in prompt:
            raise RuntimeError("green net check failed")
        return MagicMock(url="http://v/1")

    await _src(fake_gen).render(_sb(), task_dir=tmp_path)
    items = deg.read_degradations(tmp_path)
    kinds = [i["kind"] for i in items]
    assert deg.KIND_PROMPT_NEUTRALIZED in kinds, f"应记 prompt_neutralized，实际 {kinds}"
    entry = next(i for i in items if i["kind"] == deg.KIND_PROMPT_NEUTRALIZED)
    assert entry["frame_index"] == 0
    assert "内容审核" in entry["detail"]


@pytest.mark.asyncio
async def test_no_retry_no_disclosure(tmp_path, _no_localize) -> None:
    """未发生改词就不该有该提示（噪音）。"""
    from app.gateway.video_gen import degradations as deg

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        return MagicMock(url="http://v/1")

    await _src(fake_gen).render(_sb(), task_dir=tmp_path)
    assert deg.KIND_PROMPT_NEUTRALIZED not in [i["kind"] for i in deg.read_degradations(tmp_path)]


def test_router_has_chinese_label_for_neutralized() -> None:
    """kind→文案表是硬编码白名单，不登记就把英文 kind 泄漏给终端用户。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS
    from app.gateway.video_gen.degradations import KIND_PROMPT_NEUTRALIZED

    assert KIND_PROMPT_NEUTRALIZED in _DEGRADATION_LABELS


@pytest.mark.asyncio
async def test_retry_is_charged_to_budget(tmp_path, _no_localize) -> None:
    """重试是真实的一次生成调用，必须计费（成本闸不能被绕过）。"""

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        if "枪" in prompt:
            raise RuntimeError("green net check failed")
        return MagicMock(url="http://v/1")

    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    budget = MagicMock()
    provider = _FakeVideoProvider(fake_gen)
    src = AIVideoVisualSource(provider, concurrency=1, budget=budget)
    await src.render(_sb(), task_dir=tmp_path)
    assert budget.charge.call_count == 2, f"首次 + 重试各扣一次，实际 {budget.charge.call_count}"


# ---------------------------------------------------------------------------
# P1（2026-08-19）：审核误判但词表无可中性化 → 旁白直述兜底重试一次
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_false_positive_retries_with_narration_plain_prompt(tmp_path, _no_localize) -> None:
    """审核误判但词表无可中性化 → 用旁白直述 prompt 重试一次并落 sidecar。"""
    from app.gateway.video_gen import degradations as deg

    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        if len(seen) == 1:
            raise RuntimeError('360 video submit failed (400): {"error":{"code":12020,"message":"命中敏感[色情:女裸露:女胸裸露]"}}')
        return MagicMock(url="http://v/1")

    sb = _sb("穿连帽衫的程序员将极光绿人形光影拖入UI界面框", narration="程序员把光影效果拖入界面")
    await _src(fake_gen).render(sb, task_dir=tmp_path)
    assert len(seen) == 2, f"应旁白直述重试一次，实际调用 {len(seen)} 次"
    assert seen[1] == "程序员把光影效果拖入界面", f"重试应使用旁白原文，实际 {seen[1]}"
    assert sb.frames[0].visual_url == "http://v/1", "重试成功后必须写回结果"
    items = deg.read_degradations(tmp_path)
    kinds = [i["kind"] for i in items]
    assert deg.KIND_PROMPT_FALLBACK in kinds, f"应记 prompt_fallback_narration，实际 {kinds}"
    entry = next(i for i in items if i["kind"] == deg.KIND_PROMPT_FALLBACK)
    assert entry["frame_index"] == 0


@pytest.mark.asyncio
async def test_false_positive_without_narration_still_raises(tmp_path, _no_localize) -> None:
    """旁白也为空时维持原上抛（进 HR-T1 静态占位兜底），不重试。"""
    seen: list[str] = []

    async def fake_gen(prompt, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        raise RuntimeError("360 image API 400: 内容审核不通过")

    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src(fake_gen).render(_sb("穿连帽衫的程序员将极光绿人形光影拖入UI界面框", narration=""), task_dir=tmp_path)
    assert len(seen) == 1, f"旁白为空不得重试，实际调用 {len(seen)} 次"


def test_router_has_chinese_label_for_prompt_fallback() -> None:
    """kind→文案表是硬编码白名单，不登记就把英文 kind 泄漏给终端用户。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS
    from app.gateway.video_gen.degradations import KIND_PROMPT_FALLBACK

    assert KIND_PROMPT_FALLBACK in _DEGRADATION_LABELS


# ---------------------------------------------------------------------------
# 2026-08-19：图片输入内容检测拒绝（10940）→ preview/group_anchor 丢图降级纯 t2v
# ---------------------------------------------------------------------------

_ERR_10940_SUBMIT = '360 seedance submit failed (400): {"error":{"code":10940,"message":"InputImageSensitiveContentDetected.PrivacyInformation"}}'
# 轮询期形态（P2-1）：failure_reason 只透传文案、无 code
_ERR_10940_POLL = "Video task cgt-xxx failed: InputImageSensitiveContentDetected.PrivacyInformation"


def _sb_preview(tmp_path):  # noqa: ANN001, ANN202
    """F3 构造要点：预览图帧（visual_type=image + tmp_path 下真实文件）+ visual_ir
    非空 + seedance 方言——否则 legacy 透传忽略 reference 参数，「图片1」断言恒真
    空转，测不出修正 B（prompt 重翻译）。"""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    visuals = tmp_path / "visuals"
    visuals.mkdir(exist_ok=True)
    (visuals / "frame_0000.png").write_bytes(b"x" * 2048)
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    f = StoryboardFrame(
        index=0,
        narration="旁白原文",
        duration=5.0,
        image_prompt="城市天际线夜景航拍",
        local_visual_path="visuals/frame_0000.png",
        visual_type="image",
        visual_ir={"subject": "城市天际线", "action": "镜头缓缓推进", "environment": "夜晚霓虹灯光", "style": "电影感"},
    )
    return Storyboard(title="t", config=cfg, frames=[f])


def _src_seedance(fake_gen, budget=None):  # noqa: ANN001, ANN202
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider(fake_gen)
    return AIVideoVisualSource(provider, concurrency=1, budget=budget, provider_name="seedance")


@pytest.mark.asyncio
async def test_image_rejected_preview_drops_refs_and_retries_pure_t2v(tmp_path, _no_localize) -> None:
    """核心用例：10940 拦预览图 → 丢图降级纯 t2v 重试一次出片。

    断言集：恰好 2 次调用；第 2 次 image_urls/role 均 None（纯 t2v）；第 1 次
    prompt 含「图片1」（证明非 legacy 空转路径）；第 2 次 prompt 不含（修正 B——
    重翻译不残留指代）；sidecar 留痕；重试计费（F6）。若图片分支误置于文本审核
    判断之后，10940 报文会命中 is_content_review_rejection 走中性化/旁白梯子，
    重试仍带着图（image_urls 非 None）——本用例即红。
    """
    from app.gateway.video_gen import degradations as deg

    calls: list[dict] = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, role="__unset__", **kw):  # noqa: ANN001,ANN003
        calls.append({"prompt": prompt, "image_urls": image_urls, "role": role})
        if len(calls) == 1:
            raise RuntimeError(_ERR_10940_SUBMIT)
        return MagicMock(url="http://v/1")

    budget = MagicMock()
    sb = _sb_preview(tmp_path)
    await _src_seedance(fake_gen, budget=budget).render(sb, task_dir=tmp_path)

    assert len(calls) == 2, f"应丢图重试一次，实际调用 {len(calls)} 次"
    assert calls[0]["image_urls"] and calls[0]["image_urls"][0].endswith("frame_0000.png"), f"首次应带预览图首帧，实际 {calls[0]}"
    assert "图片1" in calls[0]["prompt"], f"v2 方言应注入参考图指代（证明非 legacy 空转），实际 {calls[0]['prompt']}"
    assert calls[1]["image_urls"] is None, f"重试应丢图纯 t2v，实际 {calls[1]}"
    assert calls[1]["role"] is None, f"重试 role 应随 reference_kind=None 归 None，实际 {calls[1]}"
    assert "图片1" not in calls[1]["prompt"], f"重试 prompt 须按 reference_count=0 重翻译（修正 B），实际 {calls[1]['prompt']}"
    assert sb.frames[0].visual_type == "video", "重试成功后应写回 video 而非占位"
    items = deg.read_degradations(tmp_path)
    entry = next((i for i in items if i["kind"] == deg.KIND_PREVIEW_IMAGE_REJECTED), None)
    assert entry is not None, f"应记 preview_image_rejected，实际 {[i['kind'] for i in items]}"
    assert entry["frame_index"] == 0
    assert budget.charge.call_count == 2, f"首次 + 重试各扣一次（成本闸不得绕过），实际 {budget.charge.call_count}"


@pytest.mark.asyncio
async def test_image_rejected_character_raises_to_placeholder_with_hint(tmp_path, _no_localize) -> None:
    """修正 A + 修订③：character 被拦不丢图不重试（丢图=静默放弃角色一致性承诺），
    直接上抛落 HR-T1 占位；留痕可行动指引（更换角色形象后可重试本镜）。"""
    from app.gateway.video_gen import degradations as deg

    calls: list[dict] = []

    async def fake_gen(prompt, *, duration=5.0, image_urls=None, role="__unset__", **kw):  # noqa: ANN001,ANN003
        calls.append({"prompt": prompt, "image_urls": image_urls, "role": role})
        raise RuntimeError(_ERR_10940_SUBMIT)

    # M（2026-08-31）：无预览图帧才是纯 character 形态（有预览图走 preview_first
    # 混合梯子——首拒丢首帧保角色，另见 test_ai_video_preview_first_frame.py）。
    sb = _sb()
    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src_seedance(fake_gen).render(sb, task_dir=tmp_path, reference_image_paths=["/f.png"])

    assert len(calls) == 1, f"同图重试必再败（判定确定），不得重试，实际调用 {len(calls)} 次"
    assert calls[0]["image_urls"] == ["/f.png"], "character 参考图应原样下发（不丢图）"
    assert calls[0]["role"] == "reference"
    items = deg.read_degradations(tmp_path)
    kinds = [i["kind"] for i in items]
    assert deg.KIND_PREVIEW_IMAGE_REJECTED not in kinds, "character 不走丢图降级 sidecar"
    entry = next((i for i in items if i["kind"] == deg.KIND_CHARACTER_IMAGE_REJECTED), None)
    assert entry is not None, f"应记 character_image_rejected 指引，实际 {kinds}"
    assert entry["frame_index"] == 0
    assert "更换角色形象" in entry["detail"]


@pytest.mark.asyncio
async def test_image_rejected_retry_fails_again_falls_to_placeholder(tmp_path, _no_localize) -> None:
    """重试再败：异常上抛外层 HR-T1 占位，不进中性化第二级（「重试一次」口径）。

    报文用轮询期形态（P2-1 surface：仅字面量、无 code 10940）。"""
    calls: list[dict] = []

    async def fake_gen(prompt, *, image_urls=None, **kw):  # noqa: ANN001,ANN003
        calls.append({"prompt": prompt, "image_urls": image_urls})
        raise RuntimeError(_ERR_10940_POLL)

    with pytest.raises(RuntimeError, match="coverage too low"):
        await _src_seedance(fake_gen).render(_sb_preview(tmp_path), task_dir=tmp_path)

    assert len(calls) == 2, f"首次 + 丢图重试各一次后应停（不进第二级梯子），实际调用 {len(calls)} 次"
    assert calls[1]["image_urls"] is None, f"重试须丢图纯 t2v（判别图片分支前置），实际 {calls[1]}"


def test_router_has_chinese_label_for_preview_image_rejected() -> None:
    """F1 + P2-2：不登记就把英文 kind 泄漏给终端用户；label 须中性措辞——
    组锚帧同用此 label，写「预览图」会让组锚场景看到与事实不符的文案。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS
    from app.gateway.video_gen.degradations import KIND_PREVIEW_IMAGE_REJECTED

    assert KIND_PREVIEW_IMAGE_REJECTED in _DEGRADATION_LABELS
    label = _DEGRADATION_LABELS[KIND_PREVIEW_IMAGE_REJECTED]
    assert "预览图" not in label
    assert "参考图" in label


def test_router_has_chinese_label_for_character_image_rejected() -> None:
    """修订③：character 占位指引文案登记（含可行动指引，否则走兜底泄漏英文 kind）。"""
    from app.gateway.routers.video_gen import _DEGRADATION_LABELS
    from app.gateway.video_gen.degradations import KIND_CHARACTER_IMAGE_REJECTED

    assert KIND_CHARACTER_IMAGE_REJECTED in _DEGRADATION_LABELS
    assert "更换角色形象" in _DEGRADATION_LABELS[KIND_CHARACTER_IMAGE_REJECTED]
