import time

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod
from app.gateway.video_gen.storyboard import StoryboardConfig


class _FakeLLM:
    def __init__(self, content):
        self._content = content

    async def ainvoke(self, messages):
        class _Resp:
            content = self._content

        return _Resp()


@pytest.mark.asyncio
async def test_plan_ai_image_builds_frames_with_image_prompts():
    llm = _FakeLLM('{"image_prompts": ["a golden sunrise over calm ocean", "a person climbing a mountain with determination"]}')
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    narrations = ["每一天都是新的开始", "坚持不懈才能成功"]
    sb = await planner_mod.plan(llm, title="奋斗", narrations=narrations, config=cfg)
    assert len(sb.frames) == 2
    assert sb.frames[0].narration == "每一天都是新的开始"
    assert sb.frames[0].image_prompt == "a golden sunrise over calm ocean"
    assert sb.frames[1].image_prompt == "a person climbing a mountain with determination"
    assert sb.frames[0].search_term is None


@pytest.mark.asyncio
async def test_plan_ai_image_falls_back_to_narration_on_parse_error():
    llm = _FakeLLM("not json at all")
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = await planner_mod.plan(llm, title="t", narrations=["hello world"], config=cfg)
    assert sb.frames[0].image_prompt == "hello world"


@pytest.mark.asyncio
async def test_plan_ai_video_builds_frames_with_video_prompts():
    llm = _FakeLLM('{"video_prompts": ["a cat running through a sunlit garden, tracking shot", "water flowing over smooth stones, slow motion close-up"]}')
    cfg = StoryboardConfig(video_type="ai_video", width=1080, height=1920)
    narrations = ["猫咪在花园奔跑", "水流过石头"]
    sb = await planner_mod.plan(llm, title="自然之美", narrations=narrations, config=cfg)
    assert len(sb.frames) == 2
    assert sb.frames[0].image_prompt == "a cat running through a sunlit garden, tracking shot"
    assert sb.frames[1].image_prompt == "water flowing over smooth stones, slow motion close-up"


@pytest.mark.asyncio
async def test_plan_ai_image_ignores_doubao_reasoning_content():
    """Stage 1.3 regression: doubao returns content + reasoning_content (separate field,
    in additional_kwargs). plan() uses .content only — thinking must never leak into
    image_prompt. See BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 1.3 验收闸门."""
    thinking = "I'll generate two image prompts evoking dawn and perseverance — this is thinking, not output."

    class _DoubaoLLM:
        def __init__(self, content):
            self._content = content

        async def ainvoke(self, messages):
            class _Resp:
                content = self._content
                additional_kwargs = {"reasoning_content": thinking}

            return _Resp()

    llm = _DoubaoLLM('{"image_prompts": ["a golden sunrise over calm ocean", "a person climbing a mountain"]}')
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = await planner_mod.plan(llm, title="奋斗", narrations=["每一天", "坚持"], config=cfg)
    assert sb.frames[0].image_prompt == "a golden sunrise over calm ocean"
    assert sb.frames[1].image_prompt == "a person climbing a mountain"
    assert all(thinking not in (f.image_prompt or "") for f in sb.frames), "reasoning_content leaked into image_prompt"


class _TrackingLLM:
    """LLM that tracks whether ainvoke was called."""

    def __init__(self):
        self.called = False

    async def ainvoke(self, messages):
        self.called = True

        class _Resp:
            content = ""

        return _Resp()


@pytest.mark.asyncio
async def test_plan_remotion_builds_frames_with_narrations_only():
    llm = _TrackingLLM()
    cfg = StoryboardConfig(video_type="remotion", width=1080, height=1920)
    narrations = ["开场白", "正文", "结语"]
    sb = await planner_mod.plan(llm, title="测试", narrations=narrations, config=cfg)
    assert len(sb.frames) == 3
    assert sb.frames[0].narration == "开场白"
    assert sb.frames[0].search_term is None
    assert sb.frames[0].image_prompt is None
    assert not llm.called  # LLM should not be invoked for remotion


def test_generate_image_prompts_uses_parse_json_prompts():
    """_generate_image_prompts should use _parse_json_prompts like the other generators."""
    import inspect

    source = inspect.getsource(planner_mod._generate_image_prompts)
    assert "_parse_json_prompts" in source, "_generate_image_prompts should use _parse_json_prompts"


# ---------------------------------------------------------------------------
# plan_course — course-aware planning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_plan_course_ai_image_with_style_context():
    """plan_course should generate image prompts with style context injected."""
    from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

    llm = _FakeLLM('{"frames": [{"image_prompt": "flat illustration, warm palette, a teacher explaining concepts at a whiteboard"}, {"image_prompt": "flat illustration, warm palette, students collaborating in a bright room"}]}')
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    sb = Storyboard(
        title="领导力课程",
        config=cfg,
        frames=[
            StoryboardFrame(index=0, narration="领导力是影响他人的能力"),
            StoryboardFrame(index=1, narration="团队管理需要信任"),
        ],
        scene_groups=[SceneGroup(index=0, title="核心概念", frame_indices=[0, 1])],
    )
    await planner_mod.plan_course(
        llm,
        storyboard=sb,
        style_context={"style_prefix": "flat illustration, warm palette"},
    )
    assert sb.frames[0].image_prompt is not None
    assert "flat illustration" in sb.frames[0].image_prompt


@pytest.mark.asyncio
async def test_plan_course_uses_payload_style_when_no_course_project():
    """无 CourseProject 时，payload style (cinematic) 翻译成 style_prefix 注入每个 image_prompt 开头。

    守护 service._build_style_context_from_payload + plan_course 的注入链路：
    service else 分支（无 CourseProject）现在调 plan_course（不再调 plan()），
    style_context 来自 _build_style_context_from_payload(payload) 查 STYLE_PREFIXES。

    注意：FakeLLM 不响应 style_instruction（直接返回预设值），所以这里只验证
    plan_course 把 style_prefix 放进了发给 LLM 的 system prompt（style_instruction）。
    LLM 是否遵循指令是真实模型的行为，不在单测范围。
    """
    from app.gateway.video_gen.orchestration import storyboard_planner as planner
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    captured_messages: list = []

    class _CapturingLLM:
        async def ainvoke(self, messages):
            captured_messages.append(messages)
            from unittest.mock import MagicMock

            resp = MagicMock()
            resp.content = '{"frames": [{"image_prompt": "x", "visual_source": "ai_image"}]}'
            return resp

    sb = Storyboard(
        title="孤独",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="城市霓虹下的独行")],
    )
    style_prefix = "cinematic film still, dramatic lighting"
    await planner.plan_course(
        _CapturingLLM(),
        storyboard=sb,
        style_context={"style_prefix": style_prefix},
    )

    # plan_course 应把 style_prefix 放进 system prompt 的 style_instruction
    all_content = " ".join(str(m.get("content", "")) for m in captured_messages[0])
    assert style_prefix in all_content, f"style_prefix should appear in the system prompt sent to LLM, got: {all_content!r}"


def test_build_style_context_from_payload_maps_dropdown_values():
    """_build_style_context_from_payload 把前端 style 下拉值翻译成 style_context。

    守护 STYLE_PREFIXES 查表逻辑（service 层新方法，与 _load_course_project_style 对称）。
    """
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import STYLE_PREFIXES, VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir="/tmp", sandbox_provider=AsyncMock())

    # cinematic / professional / casual → {"style_prefix": ...}
    for key in ("cinematic", "professional", "casual"):
        ctx = svc._build_style_context_from_payload({"style": key})
        assert ctx == {"style_prefix": STYLE_PREFIXES[key]}, f"{key} should map to its prefix"

    # default / missing / unknown → None (no injection)
    assert svc._build_style_context_from_payload({"style": "default"}) is None
    assert svc._build_style_context_from_payload({}) is None
    assert svc._build_style_context_from_payload({"style": "unknown"}) is None


def _make_style_svc():
    """Shared service factory for the Task 1 style-table tests below."""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir="/tmp", sandbox_provider=AsyncMock())


def test_style_prefixes_cover_all_new_preset_keys():
    """08-01 旧清单 UI key 每个都要能查到非空 prefix（存量承载，只读兼容）。

    前端 STYLE_PRESETS 定 key，后端 STYLE_PREFIXES 给 prefix。
    任何一个 key 查不到 → 用户选了该风格却毫无效果（静默失效）。
    （T1，2026-08-18：原注释自称「与前端 9 项一致」已漂移——前端现为 15 项，
    五风格 7 key 由 test_phase2_style_rules.py 的 test_video_style_keys_all_
    resolvable 单独锁，前端全量清单由前端 style-presets.test.ts 快照锁，
    此处只锁 08-01 旧 8 key 的可解析性，不重复维护全量清单。）
    """
    from app.gateway.video_gen.service import STYLE_PREFIXES

    svc = _make_style_svc()
    # 08-01 旧清单 8 key（education_flat 已退役=UI 不给新选，但后端只读兼容）
    legacy_ui_keys = (
        "corporate",
        "education_flat",
        "education_3d",
        "minimalist",
        "photorealistic",
        "documentary",
        "cinematic",
        "warm_casual",
    )
    for key in legacy_ui_keys:
        assert key in STYLE_PREFIXES, f"新风格 key {key} 未在 STYLE_PREFIXES 里"
        assert STYLE_PREFIXES[key].strip(), f"{key} 的 prefix 不得为空"
        ctx = svc._build_style_context_from_payload({"style": key})
        assert ctx is not None, f"{key} 应返回 style_context"
        assert ctx["style_prefix"] == STYLE_PREFIXES[key]

    # default 仍不注入
    assert svc._build_style_context_from_payload({"style": "default"}) is None


def test_style_prefixes_keep_legacy_values_working():
    """存量兼容：旧值仍须查得到，否则老草稿/老项目退化成无风格前缀。

    professional/casual 是任务级旧下拉值（存量草稿 payload 仍在用）；
    watercolor 实测存量项目 cp_a9bd1420219b 就存着它（UI 已移除但查表必须留）。
    """
    from app.gateway.video_gen.service import STYLE_PREFIXES

    svc = _make_style_svc()
    for legacy in ("professional", "casual", "watercolor"):
        assert legacy in STYLE_PREFIXES, f"旧值 {legacy} 被删会导致存量退化"
        assert STYLE_PREFIXES[legacy].strip(), f"{legacy} 的 prefix 不得为空"
        assert svc._build_style_context_from_payload({"style": legacy}) is not None


def test_resolve_style_key_maps_legacy_english_literals():
    """存量项目把 visual_style_preset 存成英文字面量，须能解析成新 key。

    实测：cp_f3d9cc794a25='3D render' / cp_d11fb1691f4f='flat illustration'
    / cp_a9bd1420219b='watercolor'。这些值原样进 prompt 正是反馈 5
    「提示词变英文」的根因之一，故必须能被识别并翻译（只读兼容，不写库）。
    """
    from app.gateway.video_gen.service import resolve_style_key

    assert resolve_style_key("3D render") == "education_3d"
    assert resolve_style_key("flat illustration") == "education_flat"
    assert resolve_style_key("minimalist") == "minimalist"
    assert resolve_style_key("photorealistic") == "photorealistic"
    # 新 key 原样返回
    assert resolve_style_key("corporate") == "corporate"
    # 未知值不猜，原样返回（由调用方决定如何处理）
    assert resolve_style_key("某种没见过的风格") == "某种没见过的风格"
    assert resolve_style_key("") == ""
    assert resolve_style_key(None) == ""


@pytest.mark.asyncio
async def test_plan_course_topic_fallback_on_parse_error():
    """QP1-6 新语义：plan_course 在 LLM 输出完全不可解析时绝不回退 narration——主题意象兜底。"""
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    llm = _FakeLLM("not valid json")
    cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="fallback text")],
    )
    await planner_mod.plan_course(llm, storyboard=sb, style_context={})
    assert sb.frames[0].image_prompt, "漏帧不允许留空"
    assert sb.frames[0].image_prompt != "fallback text", "绝不回退 narration"
    # 2026-08-01（Task 2）：兜底 prompt 已中文化（原 "…theme of {title}…"）。
    # 意图不变：主题词须出现在兜底画面词里，且绝不回退 narration。
    assert "「t」" in sb.frames[0].image_prompt or "主题" in sb.frames[0].image_prompt
    assert "theme of" not in sb.frames[0].image_prompt


def test_build_style_context_resolves_legacy_english_literals():
    """任务级 payload.style 若存了英文字面量，也须能解析到中文 prefix。

    审查发现（2026-08-02）：`_build_style_context_from_payload` 原先直查
    `STYLE_PREFIXES`，未过 `resolve_style_key`。任务级历史下拉值只有
    default/professional/casual/cinematic 四个 key，正常不会出现英文字面量，
    但一旦出现（例如从项目级复制粘贴的 payload、或第三方 API 调用方传入），
    会**静默失掉风格前缀**——不报错、画面无风格，属难排查的静默降级。

    与项目级 `course_models.style_prefix` 保持同一套解析语义。
    """
    from app.gateway.video_gen.service import STYLE_PREFIXES

    svc = _make_style_svc()

    # 英文字面量须被翻译（此前静默返回 None）
    ctx = svc._build_style_context_from_payload({"style": "3D render"})
    assert ctx is not None, "英文字面量不得静默失掉风格前缀"
    assert "3D 渲染" in ctx["style_prefix"], ctx["style_prefix"]

    ctx2 = svc._build_style_context_from_payload({"style": "flat illustration"})
    assert ctx2 is not None
    assert "扁平插画" in ctx2["style_prefix"], ctx2["style_prefix"]

    # 回归护栏：既有行为一律不变
    assert svc._build_style_context_from_payload({"style": "default"}) is None
    assert svc._build_style_context_from_payload({}) is None
    assert svc._build_style_context_from_payload({"style": "unknown"}) is None
    assert svc._build_style_context_from_payload({"style": "corporate"})["style_prefix"] == STYLE_PREFIXES["corporate"]


# ---------------------------------------------------------------------------
# T3（2026-08-14）：逐帧兜底并发上限（Semaphore 8）+ 总量封顶（300s）+ 增量收集。
# 修前缺失帧在单个无界 gather 里一次性并发（120 帧 = 120 个并发 LLM 瞬时打向网关），
# 且结果靠 gather 返回值收集——总量封顶的取消潮会丢掉已完成帧的成果。
# ---------------------------------------------------------------------------


def _bulk_down_fallback_llm(single_behavior):
    """bulk 调用全部失败（触发逐帧兜底）；单帧调用（user content 含 "context"）走 single_behavior。

    判别依据：单帧兜底 payload 含 adjacent_narrations（"context" 键），bulk 不含。
    """

    class _LLM:
        def __init__(self):
            self.single_calls = 0

        async def ainvoke(self, messages):
            content = messages[1]["content"]
            if '"context"' in content:
                self.single_calls += 1
                return await single_behavior(self, content)
            raise RuntimeError("bulk down")

    return _LLM()


@pytest.mark.asyncio
async def test_per_frame_fallback_caps_concurrency_and_total_budget(monkeypatch):
    """20 帧缺失 + LLM 全部 hang → 预算内返回、并发峰值恰为 8（Semaphore 生效）、全帧有非空 prompt。"""
    import asyncio

    monkeypatch.setattr(planner_mod, "_FALLBACK_TOTAL_BUDGET", 0.2)

    in_flight = {"n": 0, "peak": 0}

    async def _hang(llm, content):
        in_flight["n"] += 1
        in_flight["peak"] = max(in_flight["peak"], in_flight["n"])
        try:
            await asyncio.sleep(30)
        finally:
            in_flight["n"] -= 1
        raise RuntimeError("unreachable")

    llm = _bulk_down_fallback_llm(_hang)
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = planner_mod.Storyboard(
        title="t",
        config=cfg,
        frames=[planner_mod.StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(20)],
    )
    start = time.monotonic()
    await planner_mod._fill_image_prompts_robust(llm, sb, "", chunk_size=8)  # E8: 单帧兜底预算属批量路径
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"总量封顶应让函数在预算（测试 0.2s）内返回，实际 {elapsed:.1f}s"
    assert all(f.image_prompt for f in sb.frames), "全部帧必须有非空 prompt（主题兜底）"
    assert in_flight["peak"] <= 8, f"并发峰值必须 ≤8，实际 {in_flight['peak']}"
    assert in_flight["peak"] == 8, "20 帧全 hang 时并发峰值应恰为 8（Semaphore 生效，无界 gather 会到 20）"


@pytest.mark.asyncio
async def test_per_frame_fallback_keeps_completed_frames_on_budget_exhaustion(monkeypatch):
    """部分帧快速成功、其余 hang → 预算耗尽取消潮不得丢弃已完成帧的成果（增量收集）。"""
    import asyncio
    import json

    monkeypatch.setattr(planner_mod, "_FALLBACK_TOTAL_BUDGET", 0.2)

    async def _mixed(llm, content):
        payload = json.loads(content)
        narration = payload["narrations"][0]
        if narration.startswith("快"):

            class _Resp:
                content = '{"image_prompts": ["快速成功画面"]}'

            return _Resp()
        await asyncio.sleep(30)  # 其余 hang → 预算耗尽被取消
        raise RuntimeError("unreachable")

    llm = _bulk_down_fallback_llm(_mixed)
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = planner_mod.Storyboard(
        title="t",
        config=cfg,
        frames=[planner_mod.StoryboardFrame(index=i, narration=f"快{i}" if i < 3 else f"慢{i}") for i in range(6)],
    )
    await planner_mod._fill_image_prompts_robust(llm, sb, "", chunk_size=8)  # E8: 单帧兜底预算属批量路径
    for i in range(3):
        assert "快速成功画面" in (sb.frames[i].image_prompt or ""), f"已完成帧的成果不得被取消潮丢弃（frame {i}）"
    for i in range(3, 6):
        assert sb.frames[i].image_prompt, f"未完成帧必须落主题兜底（frame {i}）"
        assert "主题" in sb.frames[i].image_prompt, f"frame {i} 应为 _topic_fallback_prompt 产物"


@pytest.mark.asyncio
async def test_per_frame_fallback_single_frame_timeout_is_bounded(monkeypatch):
    """单帧 LLM 超时收紧为 60s（测试 0.1s）——hang 的帧在单帧窗口内截断落主题兜底，不等总量预算。"""
    import asyncio

    monkeypatch.setattr(planner_mod, "_FALLBACK_PER_FRAME_TIMEOUT", 0.1)
    monkeypatch.setattr(planner_mod, "_FALLBACK_TOTAL_BUDGET", 5)

    async def _hang(llm, content):
        await asyncio.sleep(30)  # 远长于单帧窗口 0.1s
        raise RuntimeError("unreachable")

    llm = _bulk_down_fallback_llm(_hang)
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = planner_mod.Storyboard(
        title="t",
        config=cfg,
        frames=[planner_mod.StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(3)],
    )
    start = time.monotonic()
    await planner_mod._fill_image_prompts_robust(llm, sb, "")
    elapsed = time.monotonic() - start
    assert elapsed < 5, f"单帧 60s(测试 0.1s) 超时后帧应落主题兜底而非等 30s，实际 {elapsed:.1f}s"
    assert all(f.image_prompt for f in sb.frames)


@pytest.mark.asyncio
async def test_plan_course_batched_total_budget(monkeypatch):
    """审查 Minor 3：>50 帧有 scene_groups 的批路径总量预算——LLM 全面 hang 时
    ≤预算+一次在途内返回、全部帧有非空 prompt（主题兜底），不串行烧 ~2h。"""
    import asyncio

    monkeypatch.setattr(planner_mod, "_BATCHED_TOTAL_BUDGET", 0.2)

    n = 60
    frames = [planner_mod.StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)]
    groups = [planner_mod.SceneGroup(index=0, title="g", frame_indices=list(range(n)))]
    cfg = StoryboardConfig(video_type="ai_image", width=1080, height=1920)
    sb = planner_mod.Storyboard(title="t", config=cfg, frames=frames, scene_groups=groups)

    class _SlowLLM:
        """组调用 hang 1s 后返空（misaligned → 60 帧落串行重试）；单帧调用 hang 0.1s。"""

        async def ainvoke(self, messages):
            content = messages[1]["content"]
            if '"context"' in content:  # 单帧重试调用
                await asyncio.sleep(0.1)

                class _Resp:
                    content = '{"image_prompts": ["p"]}'

                return _Resp()
            await asyncio.sleep(1)  # 组调用（修前：1s + 60×0.1s ≈ 7s 无预算）

            class _Resp:
                content = "{}"

            return _Resp()

    start = time.monotonic()
    await planner_mod._plan_course_batched(_SlowLLM(), sb, "")
    elapsed = time.monotonic() - start
    assert elapsed < 3, f"总量预算（测试 0.2s）+ 一次在途组调用后必须返回，实际 {elapsed:.1f}s"
    assert all(f.image_prompt for f in sb.frames), "全部帧必须有非空 prompt"
    assert all("主题" in (f.image_prompt or "") for f in sb.frames), "预算耗尽后的帧必须落主题兜底（不串行烧 LLM）"


# ── T5（2026-08-17 H3 修复批）：_topic_fallback_prompt 视频感知 + 标题清洗 ──


def _sb(video_type: str, title: str):
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    return Storyboard(
        title=title,
        config=StoryboardConfig(video_type=video_type, width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="n")],
    )


def test_topic_fallback_video_variant_has_motion():
    """ai_video 兜底必须含动作+镜头语--纯静态画面词是事故 4 帧的病灶（成片全程无动作）。"""
    out = planner_mod._topic_fallback_prompt(_sb("ai_video", "设备点检流程"))
    assert "动作" in out and "镜头" in out, f"video 变体缺动作/镜头语: {out}"


def test_topic_fallback_image_variant_unchanged():
    """ai_image 变体不注入镜头语（静态画面不需要运镜）。"""
    out = planner_mod._topic_fallback_prompt(_sb("ai_image", "设备点检流程"))
    assert "镜头" not in out and "动作" not in out
    assert "「设备点检流程」" in out


def test_topic_fallback_flattens_title_whitespace():
    """标题含换行/多空格必须压平（事故实测：带换行的完整 title 嵌进「」内）。"""
    out = planner_mod._topic_fallback_prompt(_sb("ai_video", "设备点检\n流程与\n要点"))
    assert "\n" not in out
    assert "设备点检 流程与 要点" in out
