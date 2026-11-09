from app.gateway.video_gen.config import VideoGenConfig


def test_tts_defaults():
    cfg = VideoGenConfig()
    assert cfg.tts_voice == "preset:146"
    assert cfg.tts_speed == 1.2
    assert cfg.default_aspect == "16:9"
    assert cfg.frame_concurrency == 3


def test_qihu_defaults():
    cfg = VideoGenConfig()
    assert cfg.qihu_api_key == ""
    assert cfg.qihu_base_url == "https://api.360.cn"
    assert cfg.qihu_image_model == "google/gemini-3.1-flash-image-preview"
    assert cfg.qihu_video_model == "minimax/MiniMax-Hailuo-2.3-no-safety_checker"
    assert cfg.ai_image_provider == "gpt-image-2"
    assert cfg.ai_video_provider == "seedance"


# ── E10（2026-08-21 360 主力切换）：新字段默认值 = 计划口径 ───────────────────


def test_360_switch_defaults():
    """360 主力切换批的 code defaults（与 config.example.yaml 示例一致）：
    360 seedance 主力 + Ark 兜底在途闸 3 + 分镜逐帧化 + 官方模型 id。"""
    cfg = VideoGenConfig()
    assert cfg.ai_video_provider == "seedance"  # 360 主力（Ark 兜底；回滚=改回 seedance-ark）
    assert cfg.ai_image_provider == "gpt-image-2"  # 保持不变（决策 5）
    assert cfg.seedance_video_model == "bytedance/seedance-2-0"  # 2026-08-21 探针在册 id
    assert cfg.seedance_video_resolution is None  # 未配置不发，交 360 默认
    assert cfg.seedance_fast_video_model == "bytedance/seedance-2-0-fast"
    assert cfg.seedance_mini_video_model == "bytedance/doubao-seedance-2-0-mini"
    assert cfg.ark_video_max_inflight == 3  # Ark 个人档 3 并发
    assert cfg.prompt_chunk_size == 1  # 逐帧（批量回退 = 改 >1）
    assert cfg.prompt_frame_concurrency == 8


def test_360_switch_overrides():
    """新字段均可经 config.yaml 覆盖（yaml → VideoGenConfig(**yaml)）。"""
    cfg = VideoGenConfig(
        ai_video_provider="seedance",
        seedance_video_model="bytedance/seedance-2-5",
        seedance_video_resolution="720p",
        ark_video_max_inflight=5,
        prompt_chunk_size=8,
        prompt_frame_concurrency=4,
        script_model="qwen/qwen3.8-max",
    )
    assert cfg.seedance_video_model == "bytedance/seedance-2-5"
    assert cfg.seedance_video_resolution == "720p"
    assert cfg.ark_video_max_inflight == 5
    assert cfg.prompt_chunk_size == 8
    assert cfg.prompt_frame_concurrency == 4
    assert cfg.script_model == "qwen/qwen3.8-max"


# ── Stage 1.2/4.1/5.1 (v4): Byte Ark fields ──────────────────────────────────
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段1.2/4.1/5.1.
# ark_* fields are the Byte Ark credentials (shared by ViMax LLM 1.2, seedream
# image 4.1, seedance-ark video 5.1). Defaults empty/360 → rolled back to 360
# by NOT setting ark_api_key in config.yaml.


def test_ark_api_key_default_empty():
    """ark_api_key defaults empty — Ark not active unless config.yaml sets it."""
    cfg = VideoGenConfig()
    assert cfg.ark_api_key == ""


def test_ark_base_url_default():
    cfg = VideoGenConfig()
    assert cfg.ark_base_url == "https://ark.cn-beijing.volces.com/api/v3"


def test_seedream_image_model_default():
    """Seedream 5.0 Pro (Ark-direct image gen, Stage 4.1)."""
    cfg = VideoGenConfig()
    assert cfg.seedream_image_model == "doubao-seedream-5-0-pro-260628"


def test_seedance_ark_video_model_default():
    """Seedance 2.0 (Ark-direct video, Stage 5.1)."""
    cfg = VideoGenConfig()
    assert cfg.seedance_ark_video_model == "doubao-seedance-2-0-260128"


def test_vimax_llm_defaults_to_360_for_rollback_safety():
    """ViMax LLM defaults stay on 360 gateway (Stage 1.2 flips via config.yaml,
    NOT code default) — so rolling back = unsetting vimax_llm_* in config.yaml.
    The flip to Ark happens in config.yaml (user-private), verified separately.
    2026-10-12 花名册批：默认 id 由悬空值 qwen/qwen3.6-plus 对齐为在册
    qwen/qwen3.8-max（roster 测试另有代码默认值 pin，此处锁 360 通道归属不变）。"""
    cfg = VideoGenConfig()
    assert cfg.vimax_llm_model == "qwen/qwen3.8-max"
    assert cfg.vimax_llm_base_url == "https://api.360.cn/v1"


def test_ark_fields_accept_override():
    """Ark fields can be set via config.yaml (env-resolved)."""
    cfg = VideoGenConfig(ark_api_key="ark-xxx", ark_base_url="https://ark.cn-beijing.volces.com/api/v3")
    assert cfg.ark_api_key == "ark-xxx"


# ── Stage 1.2 (b): ViMax LLM credential routing ─────────────────────────────
# vimax_llm_* defaults stay 360 (rollback-safe), so the flip to Ark happens in
# config.yaml (set vimax_llm_base_url=Ark + ark_api_key). The API key passed to
# ViMax MUST match the base_url: Ark endpoint → ark_api_key, 360 → qihu_api_key.
# Otherwise a flipped-to-Ark ViMax LLM would call ark.cn-beijing with the 360 key
# and 401. See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 1.2.


def test_vimax_llm_api_key_routes_to_ark_when_base_url_is_ark():
    """Ark base_url → ViMax LLM uses ark_api_key."""
    cfg = VideoGenConfig(
        vimax_llm_base_url="https://ark.cn-beijing.volces.com/api/v3",
        ark_api_key="ark-xxx",
        qihu_api_key="qihu-yyy",
    )
    assert cfg.vimax_llm_api_key() == "ark-xxx"


def test_vimax_llm_api_key_routes_to_qihu_when_base_url_is_360():
    """360 base_url (default) → ViMax LLM uses qihu_api_key (no behavior change)."""
    cfg = VideoGenConfig(
        vimax_llm_base_url="https://api.360.cn/v1",
        ark_api_key="ark-xxx",
        qihu_api_key="qihu-yyy",
    )
    assert cfg.vimax_llm_api_key() == "qihu-yyy"


# ── Stage 2a (breadth): doubao TTS config fields ─────────────────────────────
# See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
# tts_fallback_voice is the 360 fallback when doubao fails (NOT the default —
# the doubao flip happens via resolve_default_voice + env, not code default).
# It is wired into tts.synthesize(fallback_voice=...) at the service call sites.
# (doubao_tts_resource_id was deleted 2026-07-28 — dead config, the resource id
# is fixed inside DoubaoTTSClient.)


def test_tts_fallback_voice_default():
    """tts_fallback_voice defaults to preset:146 (360, rollback-safe fallback)."""
    cfg = VideoGenConfig()
    assert cfg.tts_fallback_voice == "preset:146"


def test_tts_fallback_voice_override():
    """tts_fallback_voice can be overridden via config.yaml."""
    cfg = VideoGenConfig(tts_fallback_voice="preset:158")
    assert cfg.tts_fallback_voice == "preset:158"


# ── Task 0 (闸0): Gateway orchestration concurrency ──────────────────────────
# BaseTaskService._concurrency caps in-flight orchestration (script→storyboard→
# TTS→image/video gen) at max_concurrent_tasks. Default raised 5→8 for the real
# 10-user load (10 users x 2-3 tasks = 20-30 concurrent); at 5 the surplus tasks
# blocked in _wrap_pipeline's `async with self._concurrency`, sitting in pending
# — the most likely cause of "submitted but nothing happening" under multi-user
# load. 8 is conservative (orchestration is IO-bound: LLM/TTS/Ark image gen);
# raise to 10 after monitoring (Task 8) shows headroom.
#
# The semaphore is built from config.max_concurrent_tasks (VideoGenService.__init__
# passes it to BaseTaskService; service.py:622 → task_framework service.py:55).
# config.yaml's video_gen section explicitly sets the value, which overrides this
# code default at runtime via VideoGenConfig(**yaml) — so BOTH must agree on 8
# (only changing config.py leaves the deployed gate at 5).


def test_default_max_concurrent_tasks_is_20():
    """T-G4（D5，2026-09-09）：默认 8→20 / 3→6——与 config.yaml、D11 口径三处一致。"""
    cfg = VideoGenConfig()
    assert cfg.max_concurrent_tasks == 20
    assert cfg.max_tasks_per_user == 6


def test_concurrency_semaphore_built_from_config(tmp_path):
    """The global _concurrency semaphore is sized from config.max_concurrent_tasks,
    not hardcoded — proves the configured value actually reaches the gate.

    This is the mechanism that makes config.yaml's explicit override effective:
    app.py loads VideoGenConfig(**yaml), so a yaml-set 8 (or 5, if someone reverts
    only the yaml) flows straight into _concurrency. Asserting both 8 and 3 shows
    the semaphore tracks the config value rather than any baked-in constant.
    """
    from unittest.mock import MagicMock

    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    for limit in (8, 3):
        cfg = VideoGenConfig(qihu_api_key="t", max_concurrent_tasks=limit)
        providers = ProviderRegistry(cfg)
        svc = VideoGenService(
            repo=MagicMock(),
            config=cfg,
            providers=providers,
            storage_dir=str(tmp_path),
            sandbox_provider=MagicMock(),
        )
        assert svc._concurrency._value == limit, f"_concurrency should be sized from config.max_concurrent_tasks={limit}, got {svc._concurrency._value}"


# ── Task 1 (闸1): global upstream-AI-API concurrency ─────────────────────────
# ai_image/ai_video used per-instance semaphores with no process-wide cap, so
# 20-30 tasks x 3 frames = 60-90 concurrent calls hammered 字节 Ark/360 → 429 +
# cascade timeouts. Now a process-global singleton (default 20) caps the total.


def test_default_ai_api_global_concurrency_is_20():
    """Task 1: process-wide upstream API cap default is 20 (stays under Ark quota)."""
    cfg = VideoGenConfig()
    assert cfg.ai_api_global_concurrency == 20
