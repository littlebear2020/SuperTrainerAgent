"""Tests for VimaxEngine (阶段 2.4) — the thin bridge that runs the vendored
ViMax pipeline as a video-gen engine.

config_factory + pipeline_factory are DI'd so the engine is unit-testable without
real LLM/image/video calls. The factory wiring (TrainerAI config → ViMax config,
and Script2VideoPipeline.init_from_config) lands in the registry (2.6).
"""

from unittest.mock import AsyncMock

import pytest
import yaml

from app.gateway.video_gen.engines.base import EngineContext
from app.gateway.video_gen.engines.vimax.engine import VimaxEngine


def _ctx(tmp_path, **overrides):
    base = dict(user_id="u", task_id="t", task_dir=tmp_path, voice_id="v", style="Cartoon", request={"script": "INT 房间 - 小明走进来，坐下。"})
    base.update(overrides)
    return EngineContext(**base)


@pytest.mark.asyncio
async def test_vimax_engine_writes_config_and_runs_pipeline(tmp_path):
    captured = {}

    def config_factory(working_dir, request):
        captured["working_dir"] = working_dir
        captured["request"] = request
        return {"working_dir": str(working_dir), "chat_model": {"init_args": {"model": "qwen"}}, "image_generator": {"class_path": "x"}}

    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=config_factory, pipeline_factory=lambda cfg_path: (captured.setdefault("cfg_path", cfg_path), pipeline)[1])

    outcome = await engine.render(_ctx(tmp_path))

    # config_factory got the working_dir under the task dir + the request
    assert captured["working_dir"] == tmp_path / "vimax_working"
    assert captured["request"]["script"].startswith("INT")

    # config YAML persisted to disk (ViMax init_from_config reads a file path)
    written = yaml.safe_load((tmp_path / "vimax_working" / "config.yaml").read_text(encoding="utf-8"))
    assert written["chat_model"]["init_args"]["model"] == "qwen"
    assert captured["cfg_path"] == str(tmp_path / "vimax_working" / "config.yaml")

    # pipeline invoked with the script + the agent's chosen style
    pipeline.assert_awaited_once()
    kwargs = pipeline.call_args.kwargs
    assert kwargs["script"].startswith("INT 房间")
    assert kwargs["style"] == "Cartoon"

    # outcome points at ViMax's final_video.mp4 (under working_dir)
    assert outcome.mode == "finished"
    assert outcome.finished_video_path == str(tmp_path / "vimax_working" / "final_video.mp4")


@pytest.mark.asyncio
async def test_vimax_engine_defaults_style_to_realistic(tmp_path):
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    await engine.render(_ctx(tmp_path, style=None))

    assert pipeline.call_args.kwargs["style"] == "realistic movie style"


# ── idea2video pipeline (旋钮①: idea → story → script → render) ──────────────
# Regression (bug2): a chat request selecting idea2video carries an `idea` (one
# line) but no `script`. The old engine required `script` for everyone (it only
# knew Script2VideoPipeline) → raised ValueError "no script". Fix: route by the
# pipeline knob — idea2video uses Idea2VideoPipeline, called with idea=.


@pytest.mark.asyncio
async def test_vimax_engine_idea2video_mode_runs_pipeline_with_idea_not_script(tmp_path):
    """An idea2video request has an `idea` and NO `script`. The engine must not
    raise "no script"; it must invoke the pipeline with idea= (not script=)."""
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    outcome = await engine.render(
        _ctx(
            tmp_path,
            request={"idea": "一个快递员送包裹的温馨故事", "pipeline": "idea2video"},
        )
    )

    pipeline.assert_awaited_once()
    kwargs = pipeline.call_args.kwargs
    # idea2video is invoked with idea= (Idea2VideoPipeline.__call__ signature)
    assert kwargs.get("idea") == "一个快递员送包裹的温馨故事"
    # and NOT with script= (that's the script2video path)
    assert "script" not in kwargs
    assert outcome.mode == "finished"


@pytest.mark.asyncio
async def test_vimax_engine_idea2video_forwards_progress_when_ctx_has_it(tmp_path):
    """idea2video must forward a non-None progress callback, just like script2video.

    Regression: the idea2video branch in VimaxEngine.render called the pipeline
    with NO progress kwarg, so the whole per-scene render (the bulk of the work)
    never advanced the task progress — the bar sat dead at 75% (the prep-stage
    cap) until the final_video appeared at 95%. With ctx.progress set, idea2video
    must receive a progress wrapper so per-scene/per-shot stages actually move.
    """
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    ctx = _ctx(
        tmp_path,
        request={"idea": "一个快递员送包裹的温馨故事", "pipeline": "idea2video"},
    )
    ctx.progress = AsyncMock()  # an async ProgressUpdater

    await engine.render(ctx)

    assert pipeline.call_args.kwargs["progress"] is not None


@pytest.mark.asyncio
async def test_vimax_engine_idea2video_routes_through_idea2video_pipeline_factory():
    """The default pipeline_factory picks the pipeline class by config['pipeline']:
    idea2video → Idea2VideoPipeline, otherwise Script2VideoPipeline. Assert on the
    class resolution (not instantiation — that needs a real LLM)."""
    from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    from app.gateway.video_gen.engines.vimax.engine import (
        _resolve_pipeline_class,
    )

    # _resolve_pipeline_class maps the knob to the class without instantiating
    assert _resolve_pipeline_class({"pipeline": "idea2video"}) is Idea2VideoPipeline
    assert _resolve_pipeline_class({"pipeline": "script2video"}) is Script2VideoPipeline
    assert _resolve_pipeline_class({}) is Script2VideoPipeline  # default


@pytest.mark.asyncio
async def test_novel2movie_removed_raises_loud_error(tmp_path):
    """novel2movie 已于 2026-10-16 拆除（T1，用户拍板决策①：langchain 1.x 断裂 +
    摘牌期零消费，不修直接删）。render 守卫必须 fail-fast 抛用户能看懂的人话
    错误并指向收口 plan——不静默换管线（静默落 script2video = 提交长篇小说拿回
    30 镜短片的静默错版）。"""
    engine = VimaxEngine(config_factory=lambda wd, req: {"pipeline": "novel2movie"})
    with pytest.raises(RuntimeError, match="2026-10-16 拆除"):
        await engine.render(
            _ctx(
                tmp_path,
                request={"novel_text": "小明在森林里迷路了，遇到一只会说话的狐狸…", "pipeline": "novel2movie"},
            )
        )


# ── ViMax progress forwarding (阶段 3.3 prerequisite: sync callback → async) ─


def test_stage_to_percent_maps_vimax_stages():
    """Coarse ViMax stage → progress percent (refine with meta at 2.8)."""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    assert _stage_to_percent("extract_characters") == 5
    assert _stage_to_percent("character_portrait_start") == 15
    assert _stage_to_percent("camera_frames_start") == 30
    assert _stage_to_percent("frame_prompt_start") == 40
    assert _stage_to_percent("frame_start") == 40  # frames band start (granular w/ meta)
    assert _stage_to_percent("frames_start") == 60  # i2v band start (granular w/ meta)
    assert _stage_to_percent("concat_start") == 90
    assert _stage_to_percent("final_video_exists") == 95
    assert _stage_to_percent("unknown_stage") is None  # unmapped → skip


def test_stage_to_percent_granular_during_per_shot_video_generation():
    """The i2v phase (frames + video clips, each ~120s) is 80% of render time but
    emitted only stage_start once, so progress stalled at a flat number the whole
    time (the 'stuck at 75%' bug). With per-shot meta {idx,total}, the percent must
    advance across the frames(40→60)/video(60→90) bands so the bar actually moves."""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    total = 9
    # video_clip_done per-shot: band 60%→90%, advancing as shots complete.
    # idx is 0-based; frac = (idx+1)/total so the last shot (idx=8/9) reaches 90%.
    assert _stage_to_percent("video_clip_done", {"idx": 0, "total": total}) > 60
    assert _stage_to_percent("video_clip_done", {"idx": 4, "total": total}) > _stage_to_percent("video_clip_done", {"idx": 0, "total": total})
    assert _stage_to_percent("video_clip_done", {"idx": 8, "total": total}) == 90

    # frame_done per-shot: band 40%→60%
    assert _stage_to_percent("frame_done", {"idx": 0, "total": total}) > 40
    assert _stage_to_percent("frame_done", {"idx": 8, "total": total}) == 60

    # without meta, falls back to the coarse band start (no regression for unupdated ViMax)
    assert _stage_to_percent("video_clip_done") == 60


@pytest.mark.asyncio
async def test_render_forwards_progress_when_ctx_has_it(tmp_path):
    """When ctx.progress is set, VimaxEngine wraps ViMax's sync progress callback
    into a fire-and-forget async ProgressUpdater update (阶段 3.3 prerequisite)."""
    from unittest.mock import AsyncMock

    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    ctx = _ctx(tmp_path)
    ctx.progress = AsyncMock()  # an async ProgressUpdater

    await engine.render(ctx)

    # pipeline got a non-None progress wrapper (ViMax stages get bridged)
    assert pipeline.call_args.kwargs["progress"] is not None


@pytest.mark.asyncio
async def test_vimax_progress_wrapper_forwards_stage_message_and_meta_to_result():
    """3.3: the progress wrapper must not only map stage→percent; it must also
    forward ViMax's stage/message/meta into the task result JSON so the frontend
    can render the current stage + per-shot meta (shot_count etc.). Previously the
    wrapper dropped all of stage/message/meta and only sent progress=pct."""
    from app.gateway.video_gen.engines.vimax.engine import _wrap_vimax_progress

    forwarded: dict = {}

    class FakeProgress:
        async def __call__(self, **kwargs):
            forwarded.update(kwargs)

        async def set_result(self, **kwargs):
            forwarded.setdefault("set_result", []).append(kwargs)

    cb = _wrap_vimax_progress(FakeProgress())
    # ViMax calls progress(stage, message, meta). Use a stage that maps to a percent
    # (extract_characters → 5) AND carries meta, to verify both paths forward.
    cb("extract_characters", "Extracting characters", {"count": 2})

    # give the create_task(s) ticks to run (fire-and-forget tasks need loop yields)
    import asyncio

    for _ in range(5):
        await asyncio.sleep(0)

    # percent forwarded (rescaled: extract_characters raw=5 → 75 + int(5*24/95) = 76)
    assert forwarded.get("progress") == 76
    # stage/message/meta forwarded into result JSON via set_result
    set_result_calls = forwarded.get("set_result", [])
    assert set_result_calls, "stage/message/meta was dropped — not forwarded to result"
    meta_payload = set_result_calls[-1]
    assert meta_payload.get("render_stage") == "extract_characters"
    assert meta_payload.get("render_message") == "Extracting characters"
    assert meta_payload.get("render_meta") == {"count": 2}


@pytest.mark.asyncio
async def test_render_passes_none_progress_when_unset(tmp_path):
    """No ctx.progress (draft/preview path) → ViMax runs with progress=None."""
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    await engine.render(_ctx(tmp_path))  # ctx.progress defaults to None

    assert pipeline.call_args.kwargs["progress"] is None


# ── idea2video multi-scene global progress (regression: progress 'jumping back') ─
# A 2-scene idea2video render reuses the same 40→60 (frames) / 60→90 (video) band
# in EVERY scene. So when scene_0 reached ~87% and scene_1 started, the bar DROPPED
# back to 60% (vg_4eea9dfd7215: "时而80%、又掉到40%"). Fix: tag every shot meta with
# scene_idx/scene_total, and _stage_to_percent rescales the band across scenes so the
# bar only ever moves forward (scene_0 covers the first half of the band, scene_1 the
# second half).


def test_scoped_idea2video_progress_injects_scene_meta():
    """_scoped_idea2video_progress must inject scene_idx/scene_total into EVERY emit's
    metadata (not just its own coarse emits). Without this, _stage_to_percent can't
    tell scene_0's shot 0 from scene_1's shot 0 → both map to the same percent."""
    from vimax.pipelines.idea2video_pipeline import _scoped_idea2video_progress

    captured = []

    def parent_cb(stage, message, meta):
        captured.append((stage, message, meta))

    scoped = _scoped_idea2video_progress(parent_cb, scene_idx=1, scene_total=2)
    assert scoped is not None

    # a per-shot emit from Script2VideoPipeline (carries shot info but NO scene info)
    scoped("video_clip_done", "scene 1 shot 0 done", {"idx": 0, "total": 6})

    assert len(captured) == 1
    stage, message, meta = captured[0]
    assert stage == "video_clip_done"
    assert meta["scene_idx"] == 1  # ← injected
    assert meta["scene_total"] == 2  # ← injected
    # the original per-shot metadata must survive (not clobbered)
    assert meta["idx"] == 0
    assert meta["total"] == 6


def test_stage_to_percent_idea2video_multi_scene_global_progress():
    """With scene info, the band is split across scenes so the bar moves forward only.

    2 scenes, each 6 shots. video_clip_done (band 60→90):
      - scene_0 shot 0 → start of first half of band (~62%)
      - scene_1 shot 0 → start of second half (~77%, NOT 62%)
      - scene_1 last shot → end of band (90%)
    Without scene info, scene_0 shot 0 and scene_1 shot 0 map to the SAME percent."""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    # scene_0 (idx 0 of 2), shot 0 of 6
    s0_shot0 = _stage_to_percent("video_clip_done", {"idx": 0, "total": 6, "scene_idx": 0, "scene_total": 2})
    # scene_1 (idx 1 of 2), shot 0 of 6
    s1_shot0 = _stage_to_percent("video_clip_done", {"idx": 0, "total": 6, "scene_idx": 1, "scene_total": 2})
    # scene_1 last shot
    s1_last = _stage_to_percent("video_clip_done", {"idx": 5, "total": 6, "scene_idx": 1, "scene_total": 2})

    # scene_1 shot 0 must be HIGHER than scene_0 shot 0 (the bar moves forward, not back)
    assert s1_shot0 > s0_shot0, f"scene_1 shot0 ({s1_shot0}) should be > scene_0 shot0 ({s0_shot0}) — bar dropped backward!"
    # last shot of last scene reaches ~90%
    assert s1_last == 90
    # first shot of first scene is still in the video band (> 60)
    assert s0_shot0 > 60


@pytest.mark.asyncio
async def test_vimax_progress_wrapper_is_monotonic_across_interleaved_bands():
    """ViMax gathers frame gen (40-60 band) and video gen (60-90 band) concurrently,
    so their events interleave. The progress wrapper must clamp to a high-water mark
    so the bar never moves backward (the 40↔60 flicker)."""
    import asyncio

    from app.gateway.video_gen.engines.vimax.engine import _wrap_vimax_progress

    async_progress = AsyncMock()
    cb = _wrap_vimax_progress(async_progress)
    # video event (~67) → frame event (~55, lower band) → video event (~75)
    cb("video_clip_done", "v1/4", {"idx": 0, "total": 4})
    cb("frame_done", "f3/4", {"idx": 2, "total": 4})
    cb("video_clip_done", "v2/4", {"idx": 1, "total": 4})
    await asyncio.sleep(0)  # let scheduled progress tasks run

    forwarded = [c.kwargs["progress"] for c in async_progress.call_args_list if "progress" in c.kwargs]
    assert forwarded == sorted(forwarded), f"progress must be monotonic, got {forwarded}"
    assert 55 not in forwarded, "a 40-60 band event after a 60-90 band event must be clamped"


@pytest.mark.asyncio
async def test_render_passes_characters_to_narration(tmp_path):
    """P1/P2: VimaxEngine.render reads working_dir/characters.json and forwards
    it to generate_vimax_narration so dialogue mode activates."""
    from unittest.mock import patch

    pipeline = AsyncMock()  # matches existing test_vimax_engine.py fixture convention
    engine = VimaxEngine(
        config_factory=lambda wd, req: {"working_dir": str(wd)},
        pipeline_factory=lambda _: pipeline,
        narration_llm_factory=lambda: object(),
    )

    ctx = _ctx(
        tmp_path,
        voice_id="doubao:zh_male_A",  # Plan B：ViMax 禁 preset，改用授权豆包男声
        style="Cartoon",
        request={"script": "a script", "mode": "fixed"},
    )
    # Pre-create the working_dir + files ViMax's pipeline would write.
    working = tmp_path / "vimax_working"
    working.mkdir(parents=True)
    (working / "characters.json").write_text('[{"identifier_in_scene":"菲利普","static_features":"年轻男性"}]')
    (working / "storyboard.json").write_text("[]")
    (working / "final_video.mp4").write_bytes(b"x")

    captured = {}

    async def fake_narration(**kwargs):
        captured.update(kwargs)
        return tmp_path / "vimax_narration.mp3"

    async def fake_overlay(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
        return video_path

    engine._overlay_fn = fake_overlay
    # generate_vimax_narration is imported locally inside render, so patch at source.
    with patch("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", new=fake_narration):
        await engine.render(ctx)

    assert "characters" in captured, "render must forward characters to narration"
    assert captured["characters"][0]["identifier_in_scene"] == "菲利普"


@pytest.mark.asyncio
async def test_render_writes_warning_when_characters_empty(tmp_path):
    """2026-07-27 评审 E-1：characters.json 解析成功但为空表 [] 时，
    narration_generator 静默回退 legacy 单音色旁白（`if not characters` 重读仍空
    → voice_map=None）——用户看到"配音只有旁白"却无任何 warning，
    正是 T16 要消除的盲区（解析异常/文件缺失已覆盖，空表漏了）。"""
    from unittest.mock import patch

    pipeline = AsyncMock()
    engine = VimaxEngine(
        config_factory=lambda wd, req: {"working_dir": str(wd)},
        pipeline_factory=lambda _: pipeline,
        narration_llm_factory=lambda: object(),
    )

    ctx = _ctx(tmp_path, voice_id="doubao:zh_male_A", style="Cartoon", request={"script": "a script", "mode": "fixed"})
    working = tmp_path / "vimax_working"
    working.mkdir(parents=True)
    (working / "characters.json").write_text("[]")  # 空表——degenerate 编剧输出
    (working / "storyboard.json").write_text("[]")
    (working / "final_video.mp4").write_bytes(b"x")

    captured: dict = {}

    async def fake_narration(**kwargs):
        captured.update(kwargs)
        return tmp_path / "vimax_narration.mp3"

    async def fake_overlay(video_path, task_dir, *, audio_override=None, subtitle_style="default"):
        return video_path

    engine._overlay_fn = fake_overlay
    with patch("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", new=fake_narration):
        await engine.render(ctx)

    warning_file = tmp_path / "voice_mode_warning.txt"
    assert warning_file.exists(), "空角色表回退单音色旁白必须写用户可见 warning"
    assert "单音色旁白" in warning_file.read_text(encoding="utf-8")
    # 评审二轮 FU：engine 已确知空表，下传 None 而非 []——避免 narration_generator
    # 冗余重读一次 characters.json（`if not characters` 重读分支结果仍是 []）。
    assert captured.get("characters") is None, "空表应下传 None 短路下游重读"


@pytest.mark.asyncio
async def test_engine_forwards_request_tts_speed_to_narration(tmp_path):
    """engine 必须把 ctx.request 的 tts_speed 传给 generate_vimax_narration——
    否则用户在创建请求里设的速度在 vimax 管线静默无效。"""
    from unittest.mock import MagicMock, patch

    class _FakePipeline:
        async def __call__(self, **kwargs):
            (tmp_path / "vimax_working" / "final_video.mp4").write_bytes(b"x")

    engine = VimaxEngine(
        config_factory=lambda wd, req: {"pipeline": "script2video"},
        pipeline_factory=lambda path: _FakePipeline(),
        overlay_fn=None,
        narration_llm_factory=lambda: MagicMock(),
    )
    ctx = _ctx(tmp_path, request={"script": "x", "tts_speed": 1.0})
    with patch(
        "app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration",
        new=AsyncMock(return_value=tmp_path / "narration.mp3"),
    ) as mock_gen:
        await engine.render(ctx)
    assert mock_gen.call_args.kwargs["speed"] == 1.0


def test_stage_to_percent_maps_idea2video_text_stages():
    """2026-07-22: idea2video 的 develop_story/write_script 文本规划阶段此前既不发
    事件也无映射——故事扩写 LLM 调用（可达数分钟）期间进度条停在 75 假死（与 E1
    修的 78% storyboard 假死同类）。映射值必须与 idea2video 实际执行序单调：
    develop_story(2) < extract(5) < portrait(15) < write_script(18) < storyboard(20)。"""
    from app.gateway.video_gen.engines.vimax.engine import _stage_to_percent

    assert _stage_to_percent("develop_story_start") == 2
    assert _stage_to_percent("develop_story_done") == 2
    assert _stage_to_percent("write_script_start") == 18
    assert _stage_to_percent("write_script_done") == 18
    # 与相邻阶段的单调性（防未来改动把顺序搞反）
    assert _stage_to_percent("develop_story_start") < _stage_to_percent("extract_characters_start")
    assert _stage_to_percent("write_script_start") < _stage_to_percent("design_storyboard_start")


# ── Plan B Task 2（2026-10-19）：付费前 preset 防漏 + voice_id 空的 ViMax 默认 ──


@pytest.mark.asyncio
async def test_engine_rejects_preset_voice_before_pipeline_factory(tmp_path):
    """engine 收到 preset 时在 pipeline factory（图像/视频生成入口）前即 ValueError。"""
    factory_called = {"n": 0}

    def factory(cfg_path):
        factory_called["n"] += 1
        return AsyncMock()

    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=factory)
    with pytest.raises(ValueError, match="ViMax"):
        await engine.render(_ctx(tmp_path, voice_id="preset:332"))
    assert factory_called["n"] == 0, "preset 不得触达 pipeline factory（付费生成入口）"


@pytest.mark.asyncio
@pytest.mark.parametrize("voice", ["doubao:zh_male_A", "zh-CN-YunjianNeural", "clone:xyz", "v"])
async def test_engine_allows_non_preset_voices(tmp_path, voice):
    """doubao/Edge/clone/其他非 preset 值不因此被错误拒绝。"""
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    outcome = await engine.render(_ctx(tmp_path, voice_id=voice))
    assert outcome.mode == "finished"
    pipeline.assert_awaited_once()


@pytest.mark.asyncio
async def test_engine_empty_voice_id_uses_vimax_default_by_lang(tmp_path, monkeypatch):
    """ctx.voice_id 为空时按 request lang 走 ViMax 默认解析（不再落 preset:146 残余兜底）。"""
    from unittest.mock import patch

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")  # P1-1：无 key 时 doubao 候选被跳过
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_A")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    pipeline = AsyncMock()
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline, narration_llm_factory=lambda: AsyncMock())
    with patch("app.gateway.video_gen.engines.vimax.narration_generator.generate_vimax_narration", new=AsyncMock(return_value=None)) as ng_mock:
        await engine.render(_ctx(tmp_path, voice_id="", request={"script": "剧本", "lang": "zh"}))
    assert ng_mock.call_args.kwargs["voice_id"] == "doubao:zh_male_A"


# ── 边界收口 Task 4（P1-3）：拆批剥离整剧 scene 约束 ───────────────────────


@pytest.mark.asyncio
async def test_engine_batching_strips_whole_scene_extra(tmp_path):
    """拆批开启（预算>12）：传给 pipeline 的 user_requirement 不含「exactly N scene」
    整剧约束，改携带全局镜头预算；≤12 单批保留原 extra。"""
    calls: list[dict] = []
    pipeline = AsyncMock()

    async def capture(**kw):
        calls.append(kw)

    pipeline.side_effect = capture

    def make_engine():
        return VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    extra = "Generate exactly 2 scene. Not more than 12 shots per scene."
    # 拆批（19 > 12）：剥离整剧 scene 约束、保留全局预算
    await make_engine().render(_ctx(tmp_path, request={"script": "剧本", "vimax_global_shot_budget": 19, "user_requirement_extra": extra}))
    ur = calls[-1]["user_requirement"]
    assert "exactly" not in ur.lower(), f"拆批时不得注入整剧 scene 约束：{ur!r}"
    assert "19" in ur, "全局预算须保留在 requirement"

    # 单批（12 ≤ 12）：原 extra 保留
    calls.clear()
    await make_engine().render(_ctx(tmp_path, request={"script": "剧本", "vimax_global_shot_budget": 12, "user_requirement_extra": extra}))
    ur2 = calls[-1]["user_requirement"]
    assert "exactly 2 scene" in ur2, "单批路径的既有 extra 语义不变"


# ── 边界热修复（2026-10-20 P1）：拆批判定与预算阈值同源 ────────────────────


@pytest.mark.asyncio
async def test_engine_derived_budget_batches_and_strips_scene_extra(tmp_path):
    """[Warning④] UI 派生路径（无显式预算 + target_duration）与显式预算路径须同一
    拆批判定：派生 24 > 阈值 → 拆批且剥离整剧 scene 约束。修前剥离判据只读显式
    vimax_global_shot_budget——派生路径拆批但不剥离，「exactly N scene」逐批注入
    与「只产本批片段」语义冲突。"""
    calls: list[dict] = []
    pipeline = AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw)
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    extra = "Generate exactly 2 scene. Not more than 12 shots per scene."
    await engine.render(_ctx(tmp_path, request={"script": "剧本", "target_duration": 180, "user_requirement_extra": extra}))
    kw = calls[-1]
    assert kw.get("global_shot_budget") == 24, f"派生预算须透传 pipeline（180s → 24），实际 {kw.get('global_shot_budget')}"
    ur = kw["user_requirement"]
    assert "exactly" not in ur.lower(), f"派生拆批路径同样不得注入整剧 scene 约束：{ur!r}"
    assert "at most 24 shots" in ur, f"改注入全局预算：{ur!r}"


@pytest.mark.asyncio
async def test_engine_threshold_boundary_follows_vendored_constant(tmp_path):
    """阈值边界钉死 vendored 常量（防 engine 再长出第二个硬编码 12）：
    budget=THRESHOLD 单批保留 extra；THRESHOLD+1 剥离。"""
    from vimax.utils.text import SINGLE_BATCH_SHOT_THRESHOLD

    calls: list[dict] = []
    pipeline = AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw)

    def make_engine():
        return VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)

    extra = "Generate exactly 2 scene. Not more than 12 shots per scene."
    calls.clear()
    await make_engine().render(_ctx(tmp_path, request={"script": "剧本", "vimax_global_shot_budget": SINGLE_BATCH_SHOT_THRESHOLD, "user_requirement_extra": extra}))
    assert "exactly 2 scene" in calls[-1]["user_requirement"], "≤阈值单批保留原 extra"
    calls.clear()
    await make_engine().render(_ctx(tmp_path, request={"script": "剧本", "vimax_global_shot_budget": SINGLE_BATCH_SHOT_THRESHOLD + 1, "user_requirement_extra": extra}))
    assert "exactly" not in calls[-1]["user_requirement"].lower(), ">阈值拆批剥离整剧约束"


@pytest.mark.asyncio
async def test_engine_no_budget_and_idea2video_unchanged(tmp_path):
    """无预算/无 target_duration 与 idea2video 行为不变：前者单批 + extra 保留 +
    global_shot_budget=None；后者不剥离、不传结构化预算。"""
    calls: list[dict] = []
    pipeline = AsyncMock()
    pipeline.side_effect = lambda **kw: calls.append(kw)
    engine = VimaxEngine(config_factory=lambda wd, req: {"working_dir": str(wd)}, pipeline_factory=lambda _: pipeline)
    extra = "Generate exactly 2 scene."
    await engine.render(_ctx(tmp_path, request={"script": "剧本", "user_requirement_extra": extra}))
    assert calls[-1].get("global_shot_budget") is None, "无预算 = 单批（None 透传）"
    assert "exactly 2 scene" in calls[-1]["user_requirement"]
    calls.clear()
    await engine.render(_ctx(tmp_path, request={"pipeline": "idea2video", "idea": "一句话想法", "target_duration": 180, "vimax_global_shot_budget": 19, "user_requirement_extra": extra}))
    assert "global_shot_budget" not in calls[-1], "idea2video 不传结构化预算（既有语义）"
    assert "exactly 2 scene" in calls[-1]["user_requirement"], "idea2video 不剥离 extra（既有语义）"
