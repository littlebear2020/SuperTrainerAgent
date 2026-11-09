"""Phase 3 Task 10-11: 拆镜预防层测试。

Task 10: 分镜生成 prompt 增加硬约束（单镜旁白 ≤40 字）
Task 11: pre-TTS 递归拆镜守卫（无显式 spf 时拆帧；2026-08-14 C1 起预算随 tts_speed 动态）

C1 修复测试：用真实 StoryboardFrame 调 service 方法，验证不崩溃 + 字段正确。
C2 修复测试：续写片文件名不撞原片。
"""

from pathlib import Path

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


async def _fake_run_ffmpeg(*args, **_kwargs):
    """run_ffmpeg 替身：在末位参数处落一个占位文件（静音/合成路径用）。"""
    out = args[-1]
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).write_bytes(b"\x00\x00")


async def _fake_llm_chunks(storyboard, config, task_dir):
    """_precompute_llm_chunks 替身：关闭 LLM 重切，直接空映射。"""
    return {}


def _fake_silent_audio(output, seconds=1.0):
    Path(output).parent.mkdir(parents=True, exist_ok=True)
    Path(output).write_bytes(b"\x00\x00")


class TestSplitLongNarration:
    """_split_long_narration: 在标点边界递归切分超长旁白。"""

    def test_short_narration_not_split(self):
        """≤ 预算的旁白不拆。"""
        from app.gateway.video_gen.orchestration.script import split_long_narration

        result = split_long_narration("短句。", budget_chars=40)
        assert result == ["短句。"]

    def test_long_narration_split_at_punctuation(self):
        """超长旁白在标点边界拆分。"""
        from app.gateway.video_gen.orchestration.script import split_long_narration

        # 50 chars with comma at ~25
        text = "这是第一段较长的旁白内容大概有二十五个字。" * 2  # ~50 chars
        result = split_long_narration(text, budget_chars=25)
        assert len(result) >= 2, f"应拆成 ≥2 段，实际 {len(result)}"
        # 每段 ≤ 预算（除最后一段可能略超如果没有更多标点）
        for seg in result[:-1]:
            assert len(seg) <= 30, f"段 '{seg[:15]}...' 过长 ({len(seg)} chars)"

    def test_split_does_not_cut_mid_sentence(self):
        """不在句中切割——只在标点边界。"""
        from app.gateway.video_gen.orchestration.script import split_long_narration

        text = "第一句话结束。第二句话也结束了。第三句话。"
        result = split_long_narration(text, budget_chars=10)
        for seg in result:
            # 每段应以标点结尾（或最后一段）
            assert seg.rstrip()[-1] in "。！？；，、!?;," or seg == result[-1], f"段 '{seg}' 不在标点边界结束"

    def test_recursive_split(self):
        """递归拆分：30s 旁白 → 多段。"""
        from app.gateway.video_gen.orchestration.script import split_long_narration

        text = "第一句。第二句。第三句。第四句。第五句。第六句。"
        result = split_long_narration(text, budget_chars=8)
        assert len(result) >= 3, f"应拆成 ≥3 段（递归），实际 {len(result)}"

    def test_no_punctuation_returns_as_is(self):
        """无标点的超长文本——原样返回（无法安全拆分）。"""
        from app.gateway.video_gen.orchestration.script import split_long_narration

        text = "一二三四五六七八九十" * 10
        result = split_long_narration(text, budget_chars=5)
        # 无标点 → 不拆（不切词中间）
        assert len(result) == 1


class TestFrameSplitGuard:
    """pre-TTS 拆镜守卫（Phase 3）：无显式 spf（seconds_per_frame）→ 超长旁白预拆帧。

    2026-08-14 核实修正：拆镜发生在 TTS **之前**（_split_long_narration_frames 在
    _synthesize_audio_locked 的 TTS 循环前调用）；post-TTS 的缺口由 worker 分层
    降级链（Ken Burns/tpad）兜底。spf 门在调用方：`if not seconds_per_frame`。
    """

    def test_spf_set_no_split_real_gate(self, tmp_path, monkeypatch):
        """spf 门真用例：显式 seconds_per_frame → _split_long_narration_frames 不被调用。

        旧 test_spf_set_no_split 是摆设（budget_chars=10000 直调纯函数，不经过门）。
        本用例经真实 _synthesize_audio_locked 路径：空旁白帧走静音占位免 TTS，
        monkeypatch 拆镜守卫为 spy + ffmpeg/LLM 重切为 no-op。
        """
        from types import SimpleNamespace

        from app.gateway.video_gen.service import VideoGenService

        svc = VideoGenService.__new__(VideoGenService)
        svc._config = SimpleNamespace(qihu_api_key=None, tts_fallback_voice=None)
        calls: list = []

        async def _spy(sb, td):
            calls.append(td)

        monkeypatch.setattr(svc, "_split_long_narration_frames", _spy)
        monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _fake_run_ffmpeg)
        monkeypatch.setattr(
            "app.gateway.video_gen.service._precompute_llm_chunks",
            _fake_llm_chunks,
        )
        monkeypatch.setattr("app.gateway.video_gen.service._write_silent_audio", _fake_silent_audio)
        storyboard = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_video", width=1280, height=720),
            frames=[StoryboardFrame(index=0, narration="", image_prompt="p")],
        )
        import asyncio

        async def _run():
            await svc._synthesize_audio_locked(storyboard, tmp_path, voice_id="preset:146", seconds_per_frame=5.0)

        asyncio.run(_run())
        assert calls == [], "spf 显式设置（seconds_per_frame）不得触发拆镜守卫"

    def test_no_spf_calls_split_guard(self, tmp_path, monkeypatch):
        """对照：无 seconds_per_frame → 拆镜守卫被调用。"""
        from types import SimpleNamespace

        from app.gateway.video_gen.service import VideoGenService

        svc = VideoGenService.__new__(VideoGenService)
        svc._config = SimpleNamespace(qihu_api_key=None, tts_fallback_voice=None)
        calls: list = []

        async def _spy(sb, td):
            calls.append(td)

        monkeypatch.setattr(svc, "_split_long_narration_frames", _spy)
        monkeypatch.setattr("app.gateway.video_gen.service.run_ffmpeg", _fake_run_ffmpeg)
        monkeypatch.setattr(
            "app.gateway.video_gen.service._precompute_llm_chunks",
            _fake_llm_chunks,
        )
        monkeypatch.setattr("app.gateway.video_gen.service._write_silent_audio", _fake_silent_audio)
        storyboard = Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_video", width=1280, height=720),
            frames=[StoryboardFrame(index=0, narration="", image_prompt="p")],
        )
        import asyncio

        async def _run():
            await svc._synthesize_audio_locked(storyboard, tmp_path, voice_id="preset:146")

        asyncio.run(_run())
        assert len(calls) == 1, "无 spf 时应调用拆镜守卫"


class TestServiceSplitLongNarrationFrames:
    """C1 修复测试：用真实 StoryboardFrame 调 service 方法。

    评审 C1：_split_long_narration_frames 有三连错（NameError/AttributeError/TypeError），
    原测试只测纯函数 split_long_narration 从未用真实帧调 service → 全绿但生产必崩。
    """

    def _make_service(self):
        """构造一个最小 VideoGenService 实例（绕过 __init__ 的重依赖）。"""
        from app.gateway.video_gen.service import VideoGenService

        svc = VideoGenService.__new__(VideoGenService)
        return svc

    def _run_split(self, svc, storyboard, task_dir):
        """T5（2026-08-14）起 _split_long_narration_frames 为 async（拆分后落盘）。"""
        import asyncio

        asyncio.run(svc._split_long_narration_frames(storyboard, task_dir))

    def test_long_frame_does_not_crash(self, tmp_path):
        """超长旁白帧 → 拆分不崩溃，产出多帧。"""
        svc = self._make_service()
        # 构造 >40 字的旁白，含多个句号（确保在标点边界可拆）
        long_narration = "这是一个非常长的旁白句子用于测试拆分功能是否正常工作。" * 3  # ~75 chars
        storyboard = Storyboard(
            title="test",
            config=StoryboardConfig(width=1280, height=720, video_type="ai_video"),
            frames=[
                StoryboardFrame(
                    index=0,
                    narration=long_narration,
                    image_prompt="test prompt",
                ),
            ],
        )
        self._run_split(svc, storyboard, tmp_path)
        assert len(storyboard.frames) >= 2, "超长旁白应被拆分"

    def test_split_preserves_visual_artifacts_on_first_segment(self, tmp_path):
        """拆分后第一段保留原帧的 visual_url/local_visual_path（C1 修法）。"""
        svc = self._make_service()
        long_narration = "这是一个非常长的旁白句子用于测试拆分功能是否正常工作。" * 3
        storyboard = Storyboard(
            title="test",
            config=StoryboardConfig(width=1280, height=720, video_type="ai_video"),
            frames=[
                StoryboardFrame(
                    index=0,
                    narration=long_narration,
                    image_prompt="prompt",
                    visual_url="http://example.com/v.mp4",
                    visual_type="video",
                    local_visual_path="visuals/shot_0000.mp4",
                    actual_visual_duration=5.0,
                ),
            ],
        )
        self._run_split(svc, storyboard, tmp_path)
        assert len(storyboard.frames) >= 2
        # 第一段应保留视觉产物
        assert storyboard.frames[0].visual_url == "http://example.com/v.mp4"
        assert storyboard.frames[0].local_visual_path == "visuals/shot_0000.mp4"

    def test_split_reindexes_frames(self, tmp_path):
        """拆分后帧序号连续（0,1,2,...）。"""
        svc = self._make_service()
        storyboard = Storyboard(
            title="test",
            config=StoryboardConfig(width=1280, height=720, video_type="ai_video"),
            frames=[
                StoryboardFrame(index=0, narration="短句。", image_prompt="p0"),
                StoryboardFrame(
                    index=1,
                    narration="超长旁白第一段结束。超长旁白第二段结束。超长旁白第三段结束。",
                    image_prompt="p1",
                ),
            ],
        )
        self._run_split(svc, storyboard, tmp_path)
        indices = [f.index for f in storyboard.frames]
        assert indices == list(range(len(indices))), f"帧序号不连续: {indices}"

    def test_budget_scales_down_with_low_tts_speed(self, tmp_path):
        """C1：tts_speed=0.8 → 预算 ≈ round(12×3.33×0.8)=32 → 40 字旁白被拆。"""
        svc = self._make_service()
        unit = "这是第一句，一共四十个字。"
        narration = unit * 3 + "啊" * (40 - len(unit) * 3)  # 恰好 40 字
        assert len(narration) == 40
        storyboard = Storyboard(
            title="test",
            config=StoryboardConfig(width=1280, height=720, video_type="ai_video", tts_speed=0.8),
            frames=[StoryboardFrame(index=0, narration=narration, image_prompt="p")],
        )
        self._run_split(svc, storyboard, tmp_path)
        assert len(storyboard.frames) >= 2, "低速预算 32 < 40 字，应拆分"

    def test_budget_scales_up_with_high_tts_speed(self, tmp_path):
        """C1：tts_speed=1.5 → 预算 ≈ round(12×3.33×1.5)=60 → 45 字旁白不拆。

        选 45 字使用例对旧代码（固定预算 40）可鉴别：45 > 40 旧代码会拆。
        """
        svc = self._make_service()
        unit = "这是第一句，一共四十五个字。"
        narration = unit * 3 + "啊" * (45 - len(unit) * 3)  # 恰好 45 字（旧固定预算 40 会拆）
        assert len(narration) == 45
        storyboard = Storyboard(
            title="test",
            config=StoryboardConfig(width=1280, height=720, video_type="ai_video", tts_speed=1.5),
            frames=[StoryboardFrame(index=0, narration=narration, image_prompt="p")],
        )
        self._run_split(svc, storyboard, tmp_path)
        assert len(storyboard.frames) == 1, "高速预算 60 ≥ 45 字，不应拆"

    def test_no_video_prompt_field_accessed(self, tmp_path):
        """C1: StoryboardFrame 无 video_prompt 字段——确认不访问它。

        AST 口径（与本类 test_split_budget_formula_locked 一致）：剥掉
        docstring/comment 后断言可执行代码不含 video_prompt。旧实现用行文本
        过滤，先把含 video_prompt 的行从待查列表里剔掉再断言——循环永远
        看不到违规行，守卫恒绿（inverted guard）。
        """
        import ast
        import inspect
        import textwrap

        from app.gateway.video_gen.service import VideoGenService

        src = textwrap.dedent(inspect.getsource(VideoGenService._split_long_narration_frames))
        tree = ast.parse(src)
        fn = tree.body[0]
        if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
            fn.body = fn.body[1:]
        src = ast.unparse(tree)  # unparse 不保留注释——comment 天然不计数
        assert "video_prompt" not in src, "可执行代码仍引用 video_prompt（StoryboardFrame 无此字段）"

    def test_split_budget_formula_locked(self, tmp_path):
        """B-1: 锁住动态预算公式——SHOT_MAX_NARRATION_SECONDS/3.33/tts_speed 任一
        被顺手删掉（退化为死常量，C1 修前形态）时本测试红。

        取代旧 test_mutation_verification_remove_import_crashes（只跑短旁白断言
        不拆，零鉴别力——真删 import 它仍绿）。断言只看**可执行代码**（ast 剥
        docstring）——docstring 里「预算口径」段的 tts_speed/3.33 措辞不计数
        （与同文件 video_prompt 检查的「非 docstring/comment」纪律一致）。
        """
        import ast
        import inspect
        import textwrap

        from app.gateway.video_gen.service import VideoGenService

        src = textwrap.dedent(inspect.getsource(VideoGenService._split_long_narration_frames))
        tree = ast.parse(src)
        fn = tree.body[0]
        if fn.body and isinstance(fn.body[0], ast.Expr) and isinstance(fn.body[0].value, ast.Constant):
            fn.body = fn.body[1:]
        src = ast.unparse(tree)
        assert "SHOT_MAX_NARRATION_SECONDS" in src, "动态预算常量被移除"
        assert "3.33" in src, "字/秒换算系数被移除"
        assert "tts_speed" in src, "语速因子被移除（退化回死常量 40）"
