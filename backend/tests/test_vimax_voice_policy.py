"""Plan B Task 1（2026-10-19）：可选 TTSPolicy 与 ViMax 私有默认音色解析。

设计决策 D3/D4/D5（docs/2026-10-17-vimax-reliability-optimization-design.md）：

- D4：**仅 ViMax 任务不使用 360 音色**。其他 video_gen 类型、voice-twin、360
  LLM/生图/生视频全部保持现状。ViMax 内显式 ``preset:*`` 不静默映射，提前给
  可行动错误。
- D3：字节限流/超时/传输/缺 key 等**可降级**故障直接同性别 Edge 并提示；其他
  4xx、speaker mismatch、clone 失败**不降级**；Edge 再失败直接上抛。
- D5：默认 speaker 解析顺序 = ``DOUBAO_TTS_DEFAULT_SPEAKER`` > 授权白名单首项
  （env speaker 不在白名单时告警丢弃）> Edge 语言默认 + 配置缺失告警。

测试形态：TTS 边界全 mock（DoubaoTTSClient 方法、_synthesize_qihu/_synthesize_edge
作为可观测桩）；「成功降级留痕」与「失败不留成功痕」用 record_tts_degradation
捕获器断言。共享默认路径（policy=None）的语义不变性由 test_orchestration_tts.py
/test_doubao_tts_retry.py 既有套件守护，本文件只补策略分支。
"""

from pathlib import Path

import httpx
import pytest

from app.gateway.video_gen.orchestration import tts as tts_mod
from app.gateway.video_gen.orchestration.tts import TTSResult


def _ts_adapter(fn):
    async def _wrapped(self, *a, **kw):
        return await fn(self, *a, **kw), []

    return _wrapped


@pytest.fixture
def qihu_spy(monkeypatch):
    called = []

    async def fake_qihu(text, vid, speed, out):
        called.append(vid)
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    return called


@pytest.fixture
def edge_spy(monkeypatch):
    called = []

    async def fake_edge(text, vid, speed, out):
        called.append(vid)
        return TTSResult(audio_path=str(out), srt="", duration=1.0, actual_voice_id=vid)

    monkeypatch.setattr(tts_mod, "_synthesize_edge", fake_edge)
    return called


@pytest.fixture
def degradation_capture(monkeypatch):
    captured = []
    monkeypatch.setattr(tts_mod, "record_tts_degradation", lambda **fields: captured.append(fields))
    return captured


# ── Step 1：策略路由 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_default_policy_preset_still_routes_qihu(tmp_path, monkeypatch, qihu_spy):
    """policy=None（缺省）：preset 仍走 360——共享调用方语义零变化。"""
    from app.gateway.video_gen.orchestration.tts import synthesize

    await synthesize("你好", voice_id="preset:332", output_path=str(tmp_path / "a.mp3"))
    assert qihu_spy == ["preset:332"]


@pytest.mark.asyncio
async def test_vimax_policy_preset_rejected_before_qihu(tmp_path, monkeypatch, qihu_spy):
    """ViMax policy：preset 在进入 360 前抛可读 ValueError（防漏底线，非静默换声）。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.orchestration.tts import synthesize

    with pytest.raises(ValueError, match="360"):
        await synthesize("你好", voice_id="preset:332", output_path=str(tmp_path / "a.mp3"), policy=VIMAX_TTS_POLICY)
    assert not qihu_spy, "preset 不得进入 360 调用"


@pytest.mark.asyncio
async def test_vimax_policy_doubao_429_falls_to_gender_matched_edge_not_qihu(tmp_path, monkeypatch, qihu_spy, edge_spy, degradation_capture):
    """ViMax policy：豆包 429（重试耗尽）→ 直接同性别 Edge，不经过 360。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    async def always_429(*a, **kw):
        raise DoubaoTTSHTTPError(429, "rate limited")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", always_429)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(always_429))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    result = await tts_mod._synthesize_doubao("你好", "doubao:male_speaker", 1.0, str(tmp_path / "f.mp3"), fallback_voice="preset:146", policy=VIMAX_TTS_POLICY)
    assert not qihu_spy, "ViMax 策略下豆包故障不得降 360"
    assert edge_spy == ["zh-CN-YunjianNeural"], f"男声豆包应降级 Yunjian，实际 {edge_spy}"
    assert result.actual_voice_id == "zh-CN-YunjianNeural"
    assert degradation_capture and degradation_capture[-1].get("actual_voice") == "zh-CN-YunjianNeural", "降级成功后须留痕实际音色"


@pytest.mark.asyncio
async def test_vimax_policy_doubao_female_speaker_edges_xiaoxiao(tmp_path, monkeypatch, qihu_spy, edge_spy, degradation_capture):
    """女声豆包 speaker → Edge Xiaoxiao（性别匹配）。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.orchestration.voices import CURATED_VOICES

    female_doubao = next(v["id"] for v in CURATED_VOICES if v.get("provider") == "doubao" and v.get("gender") == "Female")
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)  # 缺 key = 可降级故障

    await tts_mod._synthesize_doubao("你好", female_doubao, 1.0, str(tmp_path / "f.mp3"), fallback_voice="preset:146", policy=VIMAX_TTS_POLICY)
    assert not qihu_spy
    assert edge_spy == ["zh-CN-XiaoxiaoNeural"], f"女声豆包应降级 Xiaoxiao，实际 {edge_spy}"


@pytest.mark.asyncio
async def test_vimax_policy_doubao_missing_key_edges(tmp_path, monkeypatch, qihu_spy, edge_spy):
    """ViMax policy：缺字节 key（可降级故障）→ Edge，不走 360。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    await tts_mod._synthesize_doubao("你好", "doubao:male_speaker", 1.0, str(tmp_path / "f.mp3"), fallback_voice="preset:146", policy=VIMAX_TTS_POLICY)
    assert not qihu_spy, "缺 key 时 ViMax 不得落 360"
    assert edge_spy == ["zh-CN-YunjianNeural"]


@pytest.mark.asyncio
async def test_vimax_policy_doubao_4xx_still_raises(tmp_path, monkeypatch, qihu_spy, edge_spy):
    """ViMax policy：4xx（鉴权/配置）不降级——Edge 也不许进（不是可降级故障）。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    async def always_400(*a, **kw):
        raise DoubaoTTSHTTPError(400, "bad request")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", always_400)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(always_400))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    with pytest.raises(DoubaoTTSHTTPError):
        await tts_mod._synthesize_doubao("你好", "doubao:spk", 1.0, str(tmp_path / "f.mp3"), policy=VIMAX_TTS_POLICY)
    assert not qihu_spy and not edge_spy


@pytest.mark.asyncio
async def test_vimax_policy_speaker_mismatch_55000000_still_raises(tmp_path, monkeypatch, qihu_spy, edge_spy):
    """speaker mismatch（55000000 配置错）在任何 policy 下都不降级。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    async def mismatch(*a, **kw):
        raise RuntimeError("code 55000000: resource ID mismatched with speaker")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", mismatch)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(mismatch))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())

    with pytest.raises(RuntimeError, match="55000000"):
        await tts_mod._synthesize_doubao("你好", "doubao:spk", 1.0, str(tmp_path / "f.mp3"), policy=VIMAX_TTS_POLICY)
    assert not qihu_spy and not edge_spy


@pytest.mark.asyncio
async def test_vimax_policy_edge_failure_raises_without_success_record(tmp_path, monkeypatch, qihu_spy, edge_spy, degradation_capture):
    """Edge 兜底再失败 → 直接上抛；不得写下任何「已降级」成功留痕。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    async def always_429(*a, **kw):
        raise DoubaoTTSHTTPError(429, "rate limited")

    async def edge_boom(text, vid, speed, out):
        raise httpx.ConnectError("edge down")

    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse", always_429)
    monkeypatch.setattr(DoubaoTTSClient, "synthesize_sse_with_timestamps", _ts_adapter(always_429))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setattr(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none())
    monkeypatch.setattr(tts_mod, "_synthesize_edge", edge_boom)

    with pytest.raises(httpx.ConnectError):
        await tts_mod._synthesize_doubao("你好", "doubao:spk", 1.0, str(tmp_path / "f.mp3"), policy=VIMAX_TTS_POLICY)
    assert not qihu_spy
    assert not [c for c in degradation_capture if c.get("actual_voice")], "Edge 失败时不得留「已降级」成功记录"


@pytest.mark.asyncio
async def test_clone_failure_never_falls_back_under_policy(tmp_path, monkeypatch, qihu_spy, edge_spy):
    """clone 音色失败在任何 policy 下都不进任何 fallback（克隆红线）。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.orchestration.tts import synthesize

    async def clone_boom(*a, **kw):
        raise RuntimeError("clone synth failed")

    monkeypatch.setattr(tts_mod, "_synthesize_clone", clone_boom)
    with pytest.raises(RuntimeError, match="clone"):
        await synthesize("你好", voice_id="clone:foo", output_path=str(tmp_path / "a.mp3"), policy=VIMAX_TTS_POLICY)
    assert not qihu_spy and not edge_spy


# ── Step 2：ViMax 默认 speaker 解析（D5） ─────────────────────────────────


def test_resolve_vimax_default_env_speaker_wins(monkeypatch):
    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")  # 有 key 才有 doubao 候选资格
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    assert resolve_vimax_default_voice("zh") == "doubao:zh_male_A"


def test_resolve_vimax_default_whitelist_drops_unknown_env_speaker(monkeypatch, caplog):
    """白名单已配置且 env speaker 不在其中 → 告警并取白名单首项。"""
    import logging

    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_B,zh_male_C")
    with caplog.at_level(logging.WARNING):
        assert resolve_vimax_default_voice("zh") == "doubao:zh_female_B"
    assert any("白名单" in r.message or "whitelist" in r.message.lower() for r in caplog.records), "env speaker 被白名单丢弃时须告警"


def test_resolve_vimax_default_no_env_uses_whitelist_first(monkeypatch):
    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_B,zh_male_C")
    assert resolve_vimax_default_voice("zh") == "doubao:zh_female_B"


def test_resolve_vimax_default_nothing_zh_falls_to_edge_yunjian(monkeypatch, caplog):
    import logging

    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    with caplog.at_level(logging.WARNING):
        assert resolve_vimax_default_voice("zh") == "zh-CN-YunjianNeural"
    assert caplog.records, "无可用字节默认值时须告警配置缺失"


def test_resolve_vimax_default_non_zh_uses_default_voices_with_warning(monkeypatch, caplog):
    import logging

    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    with caplog.at_level(logging.WARNING):
        assert resolve_vimax_default_voice("en") == "en-US-ChristopherNeural"
    assert caplog.records, "非 zh 语言沿用 DEFAULT_VOICES 须告警（Edge 依赖）"


# ── ViMax 音色准入（D4 显式拒绝） ─────────────────────────────────────────


def test_ensure_vimax_voice_allowed_rejects_preset_with_actionable_message():
    from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

    with pytest.raises(ValueError, match="ViMax") as exc_info:
        ensure_vimax_voice_allowed("preset:332")
    assert "改选" in str(exc_info.value), f"错误文案须给可行动指引（改选豆包/Edge），实际：{exc_info.value}"


def test_ensure_vimax_voice_allowed_accepts_non_preset():
    from app.gateway.video_gen.engines.vimax.voice_policy import ensure_vimax_voice_allowed

    ensure_vimax_voice_allowed("doubao:zh_male_A")
    ensure_vimax_voice_allowed("zh-CN-YunjianNeural")
    ensure_vimax_voice_allowed(None)
    ensure_vimax_voice_allowed("clone:xxx")


# ── Plan B Task 2：正式 narration 接线 + 角色池 doubao→Edge ───────────────


@pytest.mark.asyncio
async def test_formal_narration_synthesize_carries_vimax_policy(tmp_path):
    """正式 narration 的每次 tts_mod.synthesize 调用都显式携带 ViMax policy。"""
    import json
    from pathlib import Path
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.video_gen.engines.vimax import narration_generator as ng
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(
        json.dumps(
            [
                {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "a", "audio_desc": '[Speaker] 主讲人: "第一点。"'},
                {"idx": 1, "is_last": True, "cam_idx": 0, "visual_desc": "b", "audio_desc": "普通旁白一句。"},
            ]
        )
    )
    characters = [{"identifier_in_scene": "主讲人", "static_features": "年轻女性"}]
    policies = []

    async def spy_synth(text, *, voice_id, speed, output_path, **kw):
        policies.append(kw.get("policy"))
        Path(output_path).write_bytes(b"mp3")
        return MagicMock(audio_path=output_path)

    with patch.object(ng.tts_mod, "synthesize", side_effect=spy_synth):
        with patch.object(ng, "run_ffmpeg", new=AsyncMock()):
            with patch.object(ng, "_align_chunk_to_shot", side_effect=lambda a, b, c, d: str(a)):
                await ng.generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="脚本",
                    llm=AsyncMock(),
                    voice_id="doubao:zh_male_A",
                    characters=characters,
                )

    assert policies, "应有 TTS 调用（对白 + 旁白）"
    assert all(p is VIMAX_TTS_POLICY for p in policies), f"每次调用都须带 VIMAX_TTS_POLICY，实际 {policies}"


def test_zh_voice_pool_vimax_is_doubao_then_edge_without_qihu():
    """ViMax 角色池 = doubao（有 key）→ zh-CN Edge 溢出，不含任何 360 preset。"""
    from app.gateway.video_gen.engines.vimax.dialogue import _zh_voice_pool

    with_key = _zh_voice_pool("Male", include_doubao=True)
    assert with_key, "池不得为空"
    assert not any(v.startswith("preset:") for v in with_key), f"ViMax 池不得含 preset，实际 {with_key}"
    assert with_key[0].startswith("doubao:"), f"有字节 key 时 doubao 优先，实际 {with_key[0]}"
    assert any(v.startswith("zh-CN-") for v in with_key), "Edge 为溢出槽位"

    without_key = _zh_voice_pool("Male", include_doubao=False)
    assert without_key and all(v.startswith("zh-CN-") for v in without_key), f"无 key 时纯 Edge 池，实际 {without_key}"
    assert not any(v.startswith("preset:") for v in without_key)


# ── Plan B Task 3：draft / regenerate 的策略隔离（按 task.task_type） ───────


def test_vimax_tts_policy_for_by_task_type():
    """_vimax_tts_policy_for：vimax → VIMAX_TTS_POLICY；其他任务类型 → None。"""
    from types import SimpleNamespace

    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.service import _vimax_tts_policy_for

    assert _vimax_tts_policy_for(SimpleNamespace(task_type="vimax")) is VIMAX_TTS_POLICY
    assert _vimax_tts_policy_for(SimpleNamespace(task_type="ai_image")) is None
    assert _vimax_tts_policy_for(SimpleNamespace(task_type="ai_video")) is None
    assert _vimax_tts_policy_for(SimpleNamespace(task_type="remotion_course")) is None


@pytest.mark.asyncio
async def test_draft_audio_vimax_task_passes_policy_to_synthesize(tmp_path):
    """ViMax 草稿：_synthesize_audio 收到 VIMAX_TTS_POLICY（stage_hook 仍并存）。"""
    import json
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()

    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    frames = [{"index": 0, "narration": "一句", "duration": 2.0}]
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {"video_type": "vimax", "width": 1080, "height": 1920, "voice_id": "doubao:zh_male_A", "tts_speed": 1.2}, "frames": frames}))

    captured: dict = {}

    async def fake_synth(sb, td, vid, **kw):
        captured.update(kw)
        return ("audio.mp3", "subtitle.srt")

    svc._synthesize_audio = fake_synth

    async def fake_merge(sb, td, **kw):
        return 1, []

    svc._merge_audio_into_storyboard = fake_merge

    async def fake_silence(*a, **kw):
        return None

    await svc._generate_draft_audio("t1", task_dir, user_id="u1")

    assert captured.get("policy") is VIMAX_TTS_POLICY, f"ViMax 草稿须传 VIMAX_TTS_POLICY，实际 {captured}"
    assert "stage_hook" in captured, "计划 A 的 stage_hook 语义须并存不回归"


@pytest.mark.asyncio
async def test_regenerate_all_audio_vimax_ctx_policy(tmp_path):
    """ViMax 批量重生：_SynthFrameCtx.policy = VIMAX_TTS_POLICY；ai_image 为 None。"""
    import json
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    async def _run(task_type: str):
        cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
        svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path / task_type), sandbox_provider=AsyncMock())
        task = MagicMock()
        task.task_type = task_type
        task.status = "ready"
        task.payload = "{}"
        svc.repo.get = AsyncMock(return_value=task)
        svc.repo.update = AsyncMock()
        svc.repo.clear_dirty_frames = AsyncMock()

        task_dir = tmp_path / task_type / "u1" / "tasks" / "t1"
        task_dir.mkdir(parents=True)
        (task_dir / "storyboard.json").write_text(
            json.dumps({"title": "t", "config": {"video_type": task_type, "width": 1080, "height": 1920, "voice_id": "doubao:zh_male_A", "tts_speed": 1.2}, "frames": [{"index": 0, "narration": "一句", "duration": 2.0}]})
        )

        policies = []

        async def spy_cas(ctx, frame):
            policies.append(ctx.policy)

        with (
            patch("app.gateway.video_gen.service._synth_frame_cas", spy_cas),
            patch("app.gateway.video_gen.service._get_audio_duration", new=lambda p: 2.0),
            patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
            patch("app.gateway.video_gen.service._write_storyboard_srt"),
            patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
        ):
            await svc.regenerate_all_audio("t1", "u1")
        return policies

    assert await _run("vimax") == [VIMAX_TTS_POLICY], "ViMax 重生 ctx 须带策略"
    assert await _run("ai_image") == [None], "ai_image 重生 ctx 必须保持 None（旧链路）"


# ── 推送前对抗评审修复（2026-10-19） ───────────────────────────────────────


def test_edge_fallback_voice_for_non_catalog_doubao_speaker_gender():
    """非目录 doubao speaker（DOUBAO_TTS_DEFAULT_SPEAKER 可为任意 seed-tts id）按
    speaker id 前缀判性别——女声不得落男声 Yunjian（评审确认项：历史「静默变男声」
    缺陷类在自由 speaker 源上复发）。"""
    from app.gateway.video_gen.orchestration.tts import _edge_fallback_voice_for

    assert _edge_fallback_voice_for("doubao:zh_female_tianmei_mars_bigtts") == "zh-CN-XiaoxiaoNeural", "非目录女声 speaker 须降级 Xiaoxiao"
    assert _edge_fallback_voice_for("doubao:zh_male_chunky_mars_bigtts") == "zh-CN-YunjianNeural"
    assert _edge_fallback_voice_for("doubao:无法判别的id") == "zh-CN-YunjianNeural", "性别不可判时默认男声（现状兜底）"


def test_record_tts_degradation_passes_structured_voices(monkeypatch):
    """record_tts_degradation 须把 requested_voice/actual_voice 结构化透传进
    sidecar（引擎正式渲染路径的降级也留结构化痕，不只 detail 文本）。"""
    from app.gateway.video_gen import degradations as deg_mod

    captured: dict = {}
    monkeypatch.setattr(deg_mod, "record_degradation", lambda td, **kw: captured.update(kw))
    tts_mod.set_tts_degradation_context("/tmp/fake-task-dir")
    tts_mod.record_tts_degradation(requested_voice="doubao:a", actual_voice="zh-CN-YunjianNeural", detail="豆包 TTS 传输错误，已降级")
    tts_mod.set_tts_degradation_context(None)
    assert captured.get("requested_voice") == "doubao:a"
    assert captured.get("actual_voice") == "zh-CN-YunjianNeural"


# ── 边界收口 Task 2（P1-1）：试听/默认音色缺 key 禁触 360 ──────────────────


def test_resolve_vimax_default_no_key_skips_doubao_candidates(monkeypatch):
    """[P1-1] 无 VOLCENGINE_VOICE_ACCESS_KEY：即使配了 DOUBAO_TTS_DEFAULT_SPEAKER
    也不返回 doubao: 候选——无 key 的 doubao 试听会经默认降级链落 360。"""
    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "zh_female_B,zh_male_C")
    assert resolve_vimax_default_voice("zh") == "zh-CN-YunjianNeural", "无 key 须直接 Edge，不得给出必落 360 的 doubao 候选"
    assert not resolve_vimax_default_voice("zh").startswith("doubao:")


@pytest.mark.asyncio
async def test_preview_voice_vimax_passes_policy_no_qihu(monkeypatch):
    """[P1-1] task_type=vimax 试听：synthesize 收到 VIMAX_TTS_POLICY（缺 key 的
    doubao 经策略直落 Edge，不调用 qihu）；无 task_type 时 policy 不传（缺省不变）。"""

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.gateway.routers import video_gen as vg_router
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

    captured: list[dict] = []
    qihu_called: list[str] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, policy=None, **kw):
        captured.append({"voice_id": voice_id, "policy": policy})
        from app.gateway.video_gen.orchestration.tts import TTSResult

        return TTSResult(audio_path=output_path, srt="", duration=1.0)

    async def fake_qihu(text, vid, speed, out):
        qihu_called.append(vid)

    import app.gateway.video_gen.orchestration.tts as tts_mod

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.setattr(tts_mod, "_synthesize_qihu", fake_qihu)
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)

    import uuid as _uuid

    _probe_text = f"P1-1-preview-probe-{_uuid.uuid4().hex[:8]}"  # 试听缓存按 voice+text 落盘且跨运行持久——每次唯一
    app = FastAPI()
    app.include_router(vg_router.router)
    app.state.video_gen_service = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.get("/api/video/voices/doubao%3Azh_male_A/preview", params={"task_type": "vimax", "text": _probe_text})
        assert r1.status_code == 200, r1.text
        assert captured[-1]["policy"] is VIMAX_TTS_POLICY, "vimax 试听须带策略（缺 key doubao 直落 Edge 不经 360）——qihu 拦截由策略分支保证（synthesize 整体换桩下 _synthesize_qihu 无独立调用路径，恒真断言已删）"

        await c.get("/api/video/voices/zh-CN-YunjianNeural/preview", params={"text": _probe_text + "-default"})
        assert captured[-1]["policy"] is None, "缺省 task_type 的试听行为不变（不传 policy）"


# ── 推送前对抗评审修复（2026-10-19） ──────────────────────────────────────


@pytest.mark.asyncio
async def test_preview_cache_key_isolated_by_task_type(monkeypatch, tmp_path):
    """[确认项] 缓存键须含 task_type——缺省策略预览（可落 360 音频）不得被
    vimax 试听命中（否则 VIMAX_TTS_POLICY 被缓存击穿，试听播出 360 声）。"""
    import uuid as _uuid

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    import app.gateway.video_gen.orchestration.tts as tts_mod
    from app.gateway.routers import video_gen as vg_router

    probe = f"cache-isolation-{_uuid.uuid4().hex[:8]}"
    calls: list[dict] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, policy=None, **kw):
        calls.append({"policy": policy, "out": output_path})
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    # 预览缓存目录指向隔离 tmp（防全局目录污染/依赖）
    from types import SimpleNamespace

    import app.gateway.routers.video_gen as vg_mod

    fake_paths = SimpleNamespace(base_dir=tmp_path)
    monkeypatch.setattr(vg_mod, "get_paths", lambda: fake_paths)

    app = FastAPI()
    app.include_router(vg_router.router)
    app.state.video_gen_service = None
    from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r1 = await c.get("/api/video/voices/doubao%3Azh_male_A/preview", params={"text": probe})  # 缺省（policy=None）
        assert r1.status_code == 200, r1.text
        r2 = await c.get("/api/video/voices/doubao%3Azh_male_A/preview", params={"text": probe, "task_type": "vimax"})  # vimax
        assert r2.status_code == 200, r2.text
    assert len(calls) == 2, f"同 voice+text 不同 task_type 须各自合成（缓存隔离），实际合成 {len(calls)} 次"
    assert calls[0]["policy"] is None
    assert calls[1]["policy"] is VIMAX_TTS_POLICY


def test_resolve_vimax_default_no_key_warning_names_real_missing_var(monkeypatch, caplog):
    """[minor 实修] 无 key 跳 doubao 后的告警不得误诊为「请配置
    DOUBAO_TTS_DEFAULT_SPEAKER」——真正缺的是 VOLCENGINE_VOICE_ACCESS_KEY。"""
    import logging

    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    with caplog.at_level(logging.WARNING):
        resolve_vimax_default_voice("zh")
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "VOLCENGINE_VOICE_ACCESS_KEY" in joined, f"告警须指向真正缺失的字节 key，实际：{joined}"


@pytest.mark.asyncio
async def test_preview_vimax_preset_rejected_readable(monkeypatch, tmp_path):
    """[minor 实修] vimax 试听 preset：ValueError 须转为可读 422（非吞成 503）。"""
    import uuid as _uuid

    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from app.gateway.routers import video_gen as vg_router

    probe = f"preset-422-{_uuid.uuid4().hex[:8]}"
    from types import SimpleNamespace

    fake_paths = SimpleNamespace(base_dir=tmp_path)
    monkeypatch.setattr(vg_router, "get_paths", lambda: fake_paths)
    app = FastAPI()
    app.include_router(vg_router.router)
    app.state.video_gen_service = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/preset%3A332/preview", params={"task_type": "vimax", "text": probe})
    assert resp.status_code == 422, f"vimax preset 试听须可读拒绝，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]


# ── 边界热修复（2026-10-20 P1）：preview 缓存键清洗 + 门先于缓存读写 ────────


def _preview_app(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from fastapi import FastAPI

    from app.gateway.routers import video_gen as vg_router

    fake_paths = SimpleNamespace(base_dir=tmp_path)
    monkeypatch.setattr(vg_router, "get_paths", lambda: fake_paths)
    app = FastAPI()
    app.include_router(vg_router.router)
    app.state.video_gen_service = None
    return app, tmp_path / "video-gen" / "voice_previews"


@pytest.mark.asyncio
async def test_preview_task_type_slash_sanitized_flat_cache(monkeypatch, tmp_path):
    """[Warning①] task_type 含 "/"：修前原样进缓存文件名 → move 穿目录失败 503
    （亦是路径注入面）；须白名单清洗为扁平文件名。"""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    import app.gateway.video_gen.orchestration.tts as tts_mod

    calls: list[str] = []

    async def fake_synthesize(text, *, voice_id, speed, output_path, policy=None, **kw):
        calls.append(voice_id)
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    app, previews = _preview_app(monkeypatch, tmp_path)
    probe = f"slash-scope-{_uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/zh-CN-YunjianNeural/preview", params={"task_type": "a/b", "text": probe})
    assert resp.status_code == 200, f"task_type=a/b 须照常试听（清洗后扁平落盘），实际 {resp.status_code}: {resp.text}"
    assert previews.exists() and previews.is_dir()
    entries = list(previews.iterdir())
    assert entries and all(e.is_file() for e in entries), f"缓存目录不得出现子目录（路径注入面）：{entries}"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_preview_overlong_task_type_bounded_filename(monkeypatch, tmp_path):
    """[Warning①] 超长 task_type：修前文件名超 255 字节 ENAMETOOLONG → 503；须截长。"""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    import app.gateway.video_gen.orchestration.tts as tts_mod

    async def fake_synthesize(text, *, voice_id, speed, output_path, policy=None, **kw):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    app, previews = _preview_app(monkeypatch, tmp_path)
    probe = f"long-scope-{_uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/zh-CN-YunjianNeural/preview", params={"task_type": "x" * 300, "text": probe})
    assert resp.status_code == 200, f"超长 task_type 须照常试听（文件名有界），实际 {resp.status_code}: {resp.text}"
    assert all(len(e.name.encode()) <= 255 for e in previews.iterdir()), "落盘文件名字节数须 ≤255"


@pytest.mark.asyncio
async def test_preview_vimax_preset_gate_before_cache_read(monkeypatch, tmp_path):
    """[Warning②] 门先于缓存读：即便旁路落盘了 vimax scope 的 preset 缓存文件
    （产线同式键名预置），preset 试听仍须 422——不得被缓存命中绕过拒绝。"""
    import hashlib
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    from app.gateway.routers.voice_twin_production import _safe_cache_voice_id

    app, previews = _preview_app(monkeypatch, tmp_path)
    previews.mkdir(parents=True, exist_ok=True)
    probe = f"gate-order-{_uuid.uuid4().hex[:8]}"
    key = f"{_safe_cache_voice_id('preset:332')}_vimax_{hashlib.md5(probe.encode()).hexdigest()[:8]}"
    (previews / f"{key}.mp3").write_bytes(b"bypassed-360-audio")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/preset%3A332/preview", params={"task_type": "vimax", "text": probe})
    assert resp.status_code == 422, f"旁路缓存文件不得绕过 preset 门，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_preview_non_vimax_preset_unchanged(monkeypatch, tmp_path):
    """[兼容性] 非 vimax（无 task_type）preset 试听照常 200——本批不改其他类型行为。"""
    import uuid as _uuid

    from httpx import ASGITransport, AsyncClient

    import app.gateway.video_gen.orchestration.tts as tts_mod

    async def fake_synthesize(text, *, voice_id, speed, output_path, policy=None, **kw):
        Path(output_path).write_bytes(b"mp3")
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=1.0)

    monkeypatch.setattr(tts_mod, "synthesize", fake_synthesize)
    app, _ = _preview_app(monkeypatch, tmp_path)
    probe = f"non-vimax-{_uuid.uuid4().hex[:8]}"
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/preset%3A332/preview", params={"text": probe})
    assert resp.status_code == 200, f"非 vimax preset 试听行为不得变化，实际 {resp.status_code}: {resp.text}"


def test_resolve_vimax_default_whitespace_key_skips_doubao(monkeypatch):
    """边界热修复（2026-10-20 P2 卫生）：VOLCENGINE_VOICE_ACCESS_KEY 为纯空白时
    不得视作已配置（bool("  ") 为真）——须跳过 doubao 候选回落 Edge，否则无 key
    的 doubao 试听/合成仍会经降级链触 360。"""

    from app.gateway.video_gen.engines.vimax.voice_policy import resolve_vimax_default_voice

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "   ")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    voice = resolve_vimax_default_voice("zh")
    assert not voice.startswith("doubao:"), f"纯空白 key 不得当已配置——须跳过 doubao 候选，实际 {voice!r}"
    assert voice == "zh-CN-YunjianNeural"
