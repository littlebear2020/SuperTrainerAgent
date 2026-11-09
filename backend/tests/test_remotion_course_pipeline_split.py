"""remotion_course 独立管线 T3：service 分流（P1–P4/P5a）+ 工厂分支（P5b）+ P6 守卫。

覆盖计划 §7「service 分流 / 工厂与单帧重生」：
- remotion_course 断言**未调用** plan_course / 拆帧守卫 / _has_mixed_sources 分支（直走工厂）
- ai_image 路径行为快照不变（plan_course 照旧调用）
- parse 失败/校验不过 → units_from_paragraphs 兜底 + course_parse_fidelity_fallback 留痕
- make_visual_source("remotion_course") 返回 RemotionCourseVisualSource
- _regenerate_frame：嵌入图/上传帧视觉重生被拒（ValueError）且 local_visual_path 不丢；
  AI 素材帧重生放行；audio_only 不受限
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.orchestration.course_script import CourseUnit
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_factory import make_visual_source
from app.gateway.video_gen.visual_sources.remotion_course_visuals import RemotionCourseVisualSource

_SCRIPT = (
    "机器学习是人工智能的一个分支，通过数据训练模型获得预测能力。"
    "已在金融、医疗等多个行业落地，成为数字化转型的核心引擎，价值持续放大。"
    "模型训练依赖大规模数据集与算力基础设施，评估指标覆盖准确率与召回率。"
    "工程实践中需要持续监控数据漂移，并定期重新训练以维持线上效果稳定。"
)


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="test-llm")
    providers = ProviderRegistry(cfg)
    return VideoGenService(repo=MagicMock(), config=cfg, providers=providers, storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


@pytest.fixture
def _no_llm():
    """stub create_script_llm——测试 config 无 models[] 注册，真实 create_chat_model 会炸。"""
    with patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()):
        yield


async def _progress(**kw) -> None:  # noqa: ANN003
    pass


def _llm_units() -> list[CourseUnit]:
    return [
        CourseUnit(layout="CoverPage", narration="机器学习是人工智能的一个分支。"),
        CourseUnit(layout="ConceptPage", narration="通过数据训练模型获得预测能力。", content={"title": "数据驱动"}, chapter="基础"),
    ]


# ── P1/P2：解析段分流 + plan_course 跳过 ───────────────────────────────────


@pytest.mark.asyncio
async def test_generate_draft_remotion_course_skips_planner(tmp_path, _no_llm):
    svc = _make_service(tmp_path)
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_llm_units())) as m_parse,
        patch("app.gateway.video_gen.orchestration.storyboard_planner.plan_course", new=AsyncMock()) as m_plan,
    ):
        await svc._generate_draft("remotion_course", {"mode": "fixed", "script": _SCRIPT}, tmp_path, _progress)

    m_parse.assert_awaited_once()
    m_plan.assert_not_called()
    sb = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert sb["frames"][0]["layout"] == "CoverPage"
    assert sb["frames"][1]["layout"] == "ConceptPage"
    assert sb["frames"][1]["layout_content"] == {"title": "数据驱动"}
    assert sb["scene_groups"][0]["title"] == "基础"


@pytest.mark.asyncio
async def test_generate_draft_remotion_course_fallback_records_degradation(tmp_path, _no_llm):
    svc = _make_service(tmp_path)
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=None)),
        patch("app.gateway.video_gen.orchestration.storyboard_planner.plan_course", new=AsyncMock()) as m_plan,
    ):
        await svc._generate_draft("remotion_course", {"mode": "fixed", "script": _SCRIPT}, tmp_path, _progress)

    m_plan.assert_not_called()
    sb = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    # 兜底 = 段落直切 narration-only 帧（版式走确定性规则表）
    assert sb["frames"] and all("narration" in f for f in sb["frames"])
    assert "【图片" not in json.dumps(sb, ensure_ascii=False)
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "course_parse_fidelity_fallback" for i in deg["items"])


@pytest.mark.asyncio
async def test_generate_draft_ai_image_unchanged_calls_planner(tmp_path, _no_llm):
    """回归：ai_image 路径行为不变（parse_script_to_frames → plan_course 照旧）。"""
    svc = _make_service(tmp_path)
    with (
        patch("app.gateway.video_gen.orchestration.script.parse_script_to_frames", new=AsyncMock(return_value=None)),
        patch("app.gateway.video_gen.orchestration.script.normalize_script", new=AsyncMock(return_value=(_SCRIPT, False))),
        patch("app.gateway.video_gen.orchestration.storyboard_planner.plan_course", new=AsyncMock()) as m_plan,
    ):
        await svc._generate_draft("ai_image", {"mode": "fixed", "script": _SCRIPT}, tmp_path, _progress)
    m_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_plan_course_remotion_course_early_return():
    """P3 双保险：plan_course 内部对 remotion_course 早退（不发起任何 LLM 调用）。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as planner

    llm = MagicMock()
    llm.ainvoke = AsyncMock()
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="x")],
    )
    await planner.plan_course(llm, storyboard=sb)
    llm.ainvoke.assert_not_called()


# ── P4：拆帧守卫跳过 + 标记防御网 ──────────────────────────────────────────

_SEG = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
_LONG = f"{_SEG}。{_SEG}。{_SEG}"  # 92 字 > 预算 round(12×3.33×1.2)=48 → 旧行为必拆


@pytest.mark.asyncio
async def test_synthesize_audio_remotion_course_skips_split_guard(tmp_path):
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration=_LONG + "【图片1】")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    counter = {"n": 0}

    async def _fake_synth(text, **kwargs):
        counter["n"] += 1
        out = tmp_path / f"tts_{counter['n']}.mp3"
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
    ):
        await svc._synthesize_audio(sb, tmp_path, "preset:146")

    # 版式单元帧 15-40s 是正常节奏：不拆帧
    assert len(sb.frames) == 1
    # 防御网：进 TTS 的旁白无图片标记
    assert "【图片" not in sb.frames[0].narration
    assert "【图片" not in counter.get("last_text", "")


@pytest.mark.asyncio
async def test_synthesize_audio_ai_image_still_splits(tmp_path):
    """回归：ai_image 长旁白帧照旧拆分（P4 只对 remotion_course 生效）。"""
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration=_LONG)],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    counter = {"n": 0}

    async def _fake_synth(text, **kwargs):
        counter["n"] += 1
        out = tmp_path / f"tts_{counter['n']}.mp3"
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
    ):
        await svc._synthesize_audio(sb, tmp_path, "preset:146")

    assert len(sb.frames) > 1  # 旧行为保留


# ── P5a：主管线绕过 mixed 判定直走工厂 ────────────────────────────────────


@pytest.mark.asyncio
async def test_prepare_storyboard_video_remotion_course_direct_factory(tmp_path, _no_llm):
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    factory_calls: list[str] = []

    def _record_factory(task_type, *a, **kw):
        factory_calls.append(task_type)
        m = MagicMock()
        m.render = AsyncMock()
        return m

    svc._make_visual_source = _record_factory
    svc._make_visual_source_mixed = MagicMock(side_effect=AssertionError("mixed 不应被调用"))

    counter = {"n": 0}

    async def _fake_synth(text, **kwargs):
        counter["n"] += 1
        out = tmp_path / f"tts_{counter['n']}.mp3"
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    units = _llm_units() + [CourseUnit(layout="ImageTextRight", narration="应用广泛。", visual_prompt="本页要点的并列关系示意图")]
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=units)),
        patch("app.gateway.video_gen.orchestration.storyboard_planner.plan_course", new=AsyncMock()),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
        patch("app.gateway.video_gen.service._reconcile_durations_with_audio", return_value=False),
        patch("app.gateway.video_gen.worker_probe.probe_worker_capabilities", new=AsyncMock(return_value={})),
    ):
        result = await svc._prepare_storyboard_video("remotion_course", {"mode": "fixed", "script": _SCRIPT}, tmp_path, _progress)

    # storyboard 源集合 = {None, ai_image}——旧行为会被误判 mixed；新行为直走工厂
    assert factory_calls == ["remotion_course"]
    payload_out = result.payload if hasattr(result, "payload") else result["payload"]
    assert payload_out["pipeline_version"] == "remotion_course"
    assert payload_out["video_type"] == "remotion_course"


# ── P5b：工厂分支 ─────────────────────────────────────────────────────────


def test_make_visual_source_remotion_course_branch():
    providers = MagicMock()
    config = SimpleNamespace(
        frame_concurrency=3,
        ai_api_global_concurrency=20,
        ai_image_max_gen=None,
        ai_video_max_gen=None,
        ai_visual_budget_cny=None,
        ai_image_provider="seedream",
        ai_video_provider="seedance",
        video_extend_enabled=False,
        video_extend_min_gap=0,
        video_extend_max_segments=0,
        duration_safety_factor=1.0,
    )
    src = make_visual_source("remotion_course", providers=providers, config=config)
    assert isinstance(src, RemotionCourseVisualSource)
    providers.make_ai_image.assert_called_once()
    providers.make_ai_video.assert_called_once()


# ── P6：单帧重生守卫 ──────────────────────────────────────────────────────


def _task_row(task_type="remotion_course", status="ready"):
    t = MagicMock()
    t.task_type = task_type
    t.status = status
    t.payload = json.dumps({"mode": "fixed"})
    return t


def _regen_sb(frame_kwargs: dict) -> Storyboard:
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=[StoryboardFrame(index=0, narration="旁白。", **frame_kwargs)],
    )
    return sb


async def _run_regen(svc, tmp_path, sb, mode="all"):
    svc.repo.get = AsyncMock(return_value=_task_row())
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    return await svc._regenerate_frame("vg_t", "u1", 0, tmp_path, mode=mode)


@pytest.mark.asyncio
async def test_regenerate_frame_embedded_image_rejected(tmp_path):
    svc = _make_service(tmp_path)
    sb = _regen_sb({"local_visual_path": "visuals/embedded/img_1.png", "visual_type": "image"})
    with pytest.raises(ValueError, match="嵌入|上传"):
        await _run_regen(svc, tmp_path, sb)
    # 素材不丢：磁盘 storyboard 的 local_visual_path 原样保留
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["local_visual_path"] == "visuals/embedded/img_1.png"


@pytest.mark.asyncio
async def test_regenerate_frame_user_upload_rejected(tmp_path):
    svc = _make_service(tmp_path)
    sb = _regen_sb({"local_visual_path": "visuals/frame_000.png", "visual_type": "image"})
    with pytest.raises(ValueError, match="嵌入|上传"):
        await _run_regen(svc, tmp_path, sb)


@pytest.mark.asyncio
async def test_regenerate_frame_ai_material_proceeds(tmp_path, _no_llm):
    """AI 素材帧重生放行——P0-A1 清空不受阻；None 源帧走新工厂分支（P5b 覆盖
    _regenerate_frame：修前 None 帧直调工厂必抛 ValueError）。"""
    svc = _make_service(tmp_path)
    # None 源 + 配图词帧（parser 产出的默认形态）
    sb = _regen_sb({"image_prompt": "示意图"})

    class _Sentinel(Exception):
        pass

    fake = MagicMock()
    fake.render = AsyncMock(side_effect=_Sentinel())
    svc._make_visual_source = MagicMock(return_value=fake)
    with pytest.raises(_Sentinel):
        await _run_regen(svc, tmp_path, sb)
    # 走到工厂 = 守卫未拦截；None 源不触发 M7 翻转 → 工厂收到 remotion_course
    svc._make_visual_source.assert_called_once()
    assert svc._make_visual_source.call_args.args[0] == "remotion_course"


@pytest.mark.asyncio
async def test_regenerate_frame_explicit_ai_image_source_keeps_course_factory(tmp_path, _no_llm):
    """2026-09-11 二轮审查 M1：显式 visual_source=ai_image 帧也**不翻转**工厂——
    帧源改派裸 AIImageVisualSource 会架空课程源（覆盖率闸按全 storyboard 计分母
    误杀 + roster/锚图注入通道与批量渲染不一致）。"""
    svc = _make_service(tmp_path)
    sb = _regen_sb({"visual_source": "ai_image", "image_prompt": "示意图"})

    class _Sentinel(Exception):
        pass

    fake = MagicMock()
    fake.render = AsyncMock(side_effect=_Sentinel())
    svc._make_visual_source = MagicMock(return_value=fake)
    with pytest.raises(_Sentinel):
        await _run_regen(svc, tmp_path, sb)
    assert svc._make_visual_source.call_args.args[0] == "remotion_course"


@pytest.mark.asyncio
async def test_regenerate_frame_audio_only_not_restricted(tmp_path):
    """audio_only 模式不受 P6 守卫限制（嵌入图帧改旁白重配音合法）。"""
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = _regen_sb({"local_visual_path": "visuals/embedded/img_1.png", "visual_type": "image"})

    async def _fake_synth(text, **kwargs):
        out = tmp_path / "tts_new.mp3"
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
    ):
        await _run_regen(svc, tmp_path, sb, mode="audio_only")
    # 素材未被清
    assert sb.frames[0].local_visual_path == "visuals/embedded/img_1.png"


# ── 2026-09-11 审查修复回归 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_course_storyboard_no_reclaim_when_image_paths_given(tmp_path, _no_llm):
    """B1：传入 image_paths（reparse 路径）时不得重认领 staging——payload 残留的
    script_extract_id 在 create 认领后 staging 已删，重认领会把重建映射覆盖成 {}
    并误写 embedded_images_expired。"""
    svc = _make_service(tmp_path)
    payload = {"script": _SCRIPT, "script_extract_id": "01JCOURSEEXTRACT01"}
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_llm_units())),
        patch("app.gateway.video_gen.orchestration.course_staging.claim_script_images") as m_claim,
    ):
        frames, _groups = await svc._build_course_storyboard(_SCRIPT, payload, tmp_path, image_paths={"img_1": "visuals/embedded/img_1.png"})
    m_claim.assert_not_called()
    assert "embedded_images_expired" not in payload


@pytest.mark.asyncio
async def test_build_course_storyboard_claims_when_image_paths_none(tmp_path, _no_llm):
    """B1 对侧：create 路径（image_paths=None + extract_id）仍认领。"""
    svc = _make_service(tmp_path)
    payload = {"script": _SCRIPT, "script_extract_id": "01JCOURSEEXTRACT01"}
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_llm_units())),
        patch("app.gateway.video_gen.orchestration.course_staging.claim_script_images", return_value=({"img_1": "visuals/embedded/img_1.png"}, False)) as m_claim,
    ):
        await svc._build_course_storyboard(_SCRIPT, payload, tmp_path, user_id="u1")
    m_claim.assert_called_once()  # claim_script_images 是同步函数
    assert m_claim.call_args.kwargs["user_id"] == "u1"


@pytest.mark.asyncio
async def test_build_course_storyboard_defers_extra_images_with_trace(tmp_path, _no_llm):
    """D1（§3.6）：一单元多图 → 首图进槽、其余顺延 + degradation 留痕 + 帧 image_note。"""
    svc = _make_service(tmp_path)
    units = [
        CourseUnit(layout="ImageTextLeft", narration="多图单元对照讲解。", image_ref="img_1", image_refs_extra=["img_2"]),
        CourseUnit(layout="ConceptPage", narration="顺延目标单元内容。"),
    ]
    with patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=units)):
        frames, _groups = await svc._build_course_storyboard(_SCRIPT, {"script": _SCRIPT}, tmp_path, image_paths={"img_1": "visuals/embedded/img_1.png", "img_2": "visuals/embedded/img_2.png"})
    assert frames[1].local_visual_path == "visuals/embedded/img_2.png"
    assert frames[1].image_note == "来自邻近单元的文档配图"
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "embedded_image_deferred" for i in deg["items"])
    # m17（2026-09-11 二轮审查）：留痕编号 1-based（编辑器口径）+ narration 片段定位
    deferred_detail = next(i["detail"] for i in deg["items"] if i["kind"] == "embedded_image_deferred")
    assert "单元 1" in deferred_detail and "单元 2" in deferred_detail
    assert "多图单元" in deferred_detail


# ── 2026-09-12 缺陷批条目 3/13 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_regenerate_frame_residual_preview_image_rejected(tmp_path):
    """条目 3·缺陷 A（service 层兜底）：ai_video + 残留预览图 → ValueError + 素材不丢。"""
    svc = _make_service(tmp_path)
    sb = _regen_sb({"layout": "VideoClipPage", "visual_source": "ai_video", "image_prompt": "视频提示", "local_visual_path": "visuals/img_0001.png", "visual_type": "image"})
    with pytest.raises(ValueError, match="移除旧配图"):
        await _run_regen(svc, tmp_path, sb)
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["local_visual_path"] == "visuals/img_0001.png"


@pytest.mark.asyncio
async def test_regenerate_frame_variant_b_residual_ai_image_rejected(tmp_path):
    """条目 3·变体 B（service 层兜底）：ai_image 残留 AI 图 + VideoClipPage → ValueError
    （修前 _keep_preview_for_i2v=False 静默清 local 再空目标 return——素材被删且 toast 成功）。"""
    svc = _make_service(tmp_path)
    sb = _regen_sb({"layout": "VideoClipPage", "visual_source": "ai_image", "image_prompt": "旧图提示", "local_visual_path": "visuals/img_0002.png", "visual_type": "image"})
    with pytest.raises(ValueError, match="移除旧配图"):
        await _run_regen(svc, tmp_path, sb)
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["local_visual_path"] == "visuals/img_0002.png"


@pytest.mark.asyncio
async def test_build_course_storyboard_claim_failure_degrades(tmp_path, _no_llm):
    """条目 13：claim_script_images 抛 OSError（磁盘满/权限）→ 修前只 warning log、
    嵌入图整批静默丢失；修后 degradation 留痕（单列 kind）+ payload 标记、任务继续。"""
    svc = _make_service(tmp_path)
    payload = {"script": _SCRIPT, "script_extract_id": "01JCOURSEEXTRACT01"}
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_llm_units())),
        patch("app.gateway.video_gen.orchestration.course_staging.claim_script_images", side_effect=OSError("disk full")),
    ):
        frames, _groups = await svc._build_course_storyboard(_SCRIPT, payload, tmp_path)
    deg = json.loads((tmp_path / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "embedded_images_claim_failed" for i in deg["items"])
    assert payload["embedded_images_expired"] is True
    # 任务继续：解析照常产出帧（嵌入图映射为空，不阻塞）
    assert frames
