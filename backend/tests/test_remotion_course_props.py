"""T4 守卫测试：remotion_course props 构建 + 品牌包 + handoff 放行 + task_type 落库。

覆盖计划验证条目：
- props 构建（帧→layout、字幕全局帧、优先级链三态）
- 品牌包三态（无项目默认 dark-blue / 有项目覆写 / 缺字段安全降级）
- handoff 放行（_should_handoff_to_worker 含 remotion_course，既有三值不动）
- worker payload 形状（pipeline_version=remotion_course + remotion_props 下挂）
"""

from __future__ import annotations

from app.gateway.video_gen.brand_pack import resolve_brand
from app.gateway.video_gen.course_models import CourseProject
from app.gateway.video_gen.pipelines.remotion_course_props import (
    FPS,
    _parse_srt_cues,
    build_remotion_props,
    build_remotion_worker_payload,
    build_still_props,
)
from app.gateway.video_gen.storyboard import (
    REMOTION_LAYOUTS,
    SceneGroup,
    Storyboard,
    StoryboardConfig,
    StoryboardFrame,
)


def _sb(frames: list[StoryboardFrame], *, groups: list[SceneGroup] | None = None, title: str = "测试课程") -> Storyboard:
    return Storyboard(
        title=title,
        config=StoryboardConfig(video_type="remotion_course", width=1920, height=1080),
        frames=frames,
        scene_groups=groups or [],
    )


def _frame(i: int, narration: str = "这是一个测试旁白，用来验证版式分配逻辑。", duration: float = 4.0, **kw) -> StoryboardFrame:
    return StoryboardFrame(index=i, narration=narration, duration=duration, **kw)


class TestDeterministicLayout:
    def test_first_and_last_frame_cover_endcard(self, tmp_path):
        sb = _sb([_frame(0), _frame(1), _frame(2), _frame(3)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["layout"] == "CoverPage"
        assert props["scenes"][-1]["layout"] == "EndCard"

    def test_graphic_big_number_maps_chart(self, tmp_path):
        sb = _sb(
            [
                _frame(0),
                _frame(1, visual_source="graphic", graphic_spec={"type": "big_number", "number": "37", "unit": "%", "description": "转化率提升"}),
                _frame(2),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] == "ChartPage"
        lp = props["scenes"][1]["layoutProps"]
        assert lp["headline"] == "37%"
        assert lp["chart"][0]["value"] == 37

    def test_graphic_steps_maps_process(self, tmp_path):
        sb = _sb(
            [
                _frame(0),
                _frame(1, visual_source="graphic", graphic_spec={"type": "steps", "steps": ["第一步", "第二步", "第三步"]}),
                _frame(2),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] == "ProcessPage"
        assert len(props["scenes"][1]["layoutProps"]["steps"]) == 3

    def test_ai_video_maps_videoclippage(self, tmp_path):
        sb = _sb([_frame(0), _frame(1, visual_source="ai_video"), _frame(2)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] == "VideoClipPage"

    def test_ai_image_alternates_imagetext(self, tmp_path):
        sb = _sb([_frame(i, narration="这一帧的旁白比较长，应该走图文版式而不是全幅视觉页。" * 2) for i in range(6)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        mid = [s["layout"] for s in props["scenes"][1:-1]]
        assert "ImageTextLeft" in mid and "ImageTextRight" in mid
        # 交替不重复相邻
        pairs = list(zip(mid, mid[1:]))
        assert all(a != b for a, b in pairs if a in ("ImageTextLeft", "ImageTextRight"))

    def test_group_start_maps_chapter_divider(self, tmp_path):
        groups = [
            SceneGroup(index=0, title="第一章", frame_indices=[0, 1]),
            SceneGroup(index=1, title="第二章", frame_indices=[2, 3]),
        ]
        sb = _sb([_frame(i) for i in range(4)], groups=groups)
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][2]["layout"] == "ChapterDivider"
        assert props["scenes"][2]["layoutProps"]["title"] == "第二章"


class TestPriorityChain:
    """D14：用户手改 > planner hint > 确定性规则——三者共享 frame.layout 字段，
    写入纪律（planner 只写 None 帧）保证用户胜出；非法值剔除。"""

    def test_frame_layout_wins_over_deterministic(self, tmp_path):
        sb = _sb(
            [
                _frame(0),
                _frame(1, visual_source="graphic", graphic_spec={"type": "big_number", "number": "5"}, layout="QuotePage"),
                _frame(2),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] == "QuotePage"

    def test_invalid_layout_falls_back(self, tmp_path):
        sb = _sb([_frame(0), _frame(1, layout="NoSuchLayout"), _frame(2)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] in REMOTION_LAYOUTS
        assert props["scenes"][1]["layout"] != "NoSuchLayout"


class TestEndFrameSemantics:
    """T-B1（F5/F6+隐-1，2026-09-09 feedback-round1）：末帧版式语义判定 + 追加式片尾。"""

    def test_mixed_end_frame_gets_content_layout_plus_appended_endcard(self, tmp_path):
        """混合末帧（内容+收尾语）：按内容版式渲染 + 追加无旁白 EndCard（90 帧、
        无 audioFile）；场景数 +1；不拆音频（混合帧音频整段属内容场景）。"""
        sb = _sb(
            [
                _frame(0),
                _frame(1),
                _frame(2, narration="第三，信任是团队协作的基石。感谢观看。", duration=5.0, visual_url="http://cdn/x.png", visual_type="image"),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        content_last = props["scenes"][2]
        assert content_last["layout"] != "EndCard", "混合末帧必须按内容版式渲染（F5 根修）"
        assert content_last["audioFile"] or content_last["narration"], "内容帧保留旁白"
        assert len(props["scenes"]) == 4, "追加 EndCard 场景（数 +1）"
        tail = props["scenes"][3]
        assert tail["layout"] == "EndCard"
        assert tail["audioFile"] is None, "追加片尾无旁白（安静品牌页）"
        assert tail["durationInFrames"] == 90
        assert tail["captions"] == []
        assert tail["visual"] == {"kind": "none"}

    def test_pure_closing_end_frame_single_endcard_no_double_tail(self, tmp_path):
        """纯收尾末帧：单一 EndCard（不追加成双尾）。"""
        sb = _sb([_frame(0), _frame(1), _frame(2, narration="感谢观看", duration=2.0)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][-1]["layout"] == "EndCard"
        assert len(props["scenes"]) == 3, "不追加（纯收尾帧自带片尾语义）"

    def test_short_closing_with_word_hit(self, tmp_path):
        """≤12 字含收尾词（如"我们下期再见"）→ 纯收尾。"""
        sb = _sb([_frame(0), _frame(1, narration="我们下期再见")])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] == "EndCard"

    def test_long_content_end_not_closing(self, tmp_path):
        """末帧长内容（无收尾词）→ 内容版式 + 追加片尾。"""
        sb = _sb([_frame(0), _frame(1, narration="最后我们总结一下本节课的三个核心要点与落地步骤" * 2)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["layout"] != "EndCard"
        assert props["scenes"][-1]["layout"] == "EndCard", "无 outro → 追加式片尾恒在"

    def test_endcard_consumes_brand_outro_no_double_tail(self, tmp_path):
        """末帧 EndCard × 品牌 outro：EndCard 消费 outro 文案 + brand.outro 摘除
        （修前模板 BrandOutro 再渲一遍 → 双重"感谢观看"）。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(
            user_id="u1",
            title="品牌课",
            intro_outro={"outro": {"headline": "下期预告更精彩", "follow": "关注 @教研组"}},
        )
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")], title="品牌课")
        task_dir = tmp_path / "u1" / "tasks" / "vg_b1_outro"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert props["scenes"][-1]["layout"] == "EndCard"
        assert props["scenes"][-1]["layoutProps"]["title"] == "下期预告更精彩"
        assert props["scenes"][-1]["layoutProps"]["follow"] == "关注 @教研组"
        assert "outro" not in props["brand"], "outro 已被 EndCard 消费（防 BrandOutro 双尾）"

    def test_mixed_end_with_brand_outro_no_append(self, tmp_path):
        """混合末帧 × 品牌 outro：不追加 EndCard（BrandOutro 即片尾）。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(
            user_id="u1",
            title="品牌课2",
            intro_outro={"outro": {"headline": "谢谢", "follow": ""}},
        )
        sb = _sb([_frame(0), _frame(1, narration="第三点是执行落地的方法与节奏，感谢观看。")], title="品牌课2")
        task_dir = tmp_path / "u1" / "tasks" / "vg_b1_mix_outro"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert props["scenes"][1]["layout"] != "EndCard"
        assert len(props["scenes"]) == 2, "有 outro 不追加"
        assert "outro" in props["brand"], "BrandOutro 保留（模板渲染片尾）"

    def test_endcard_visual_suppressed_with_trace(self, tmp_path):
        """F6 止血：无视觉槽版式（EndCard）带已生成素材 → 不写 visual.path +
        degradations 留痕 visual_unused。"""
        sb = _sb([_frame(0), _frame(1, narration="感谢观看", visual_url="http://cdn/wasted.mp4", visual_type="video")])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][-1]["visual"] == {"kind": "none"}
        from app.gateway.video_gen.degradations import read_degradations

        recs = read_degradations(tmp_path)
        assert any(r["kind"] == "visual_unused" for r in recs), f"须留 visual_unused 留痕，实际 {recs}"


class TestCaptions:
    def test_srt_word_cues_become_global_ms(self, tmp_path):
        srt = "1\n00:00:00,100 --> 00:00:00,200\n你\n\n2\n00:00:01,000 --> 00:00:01,500\n好\n"
        sb = _sb(
            [
                _frame(0, srt=srt),
                _frame(1, srt=srt),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        # 第二场景 offset = 4000ms（帧0 duration=4.0s → 120 帧 → round(120*1000/30)）
        s1 = props["scenes"][1]["captions"]
        assert s1[0]["startMs"] == 4000 + 100
        assert s1[0]["word"] == "你"

    def test_intro_project_captions_start_after_intro(self, tmp_path):
        """T-A3（R3）：挂 intro 项目全片字幕不再提前 3000ms——offset 起算含 intro 帧。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(
            user_id="u1",
            title="品牌课程",
            intro_outro={"intro": {"headline": "开学第一课", "subtitle": "欢迎加入"}},
        )
        srt = "1\n00:00:00,100 --> 00:00:00,200\n你\n"
        sb = _sb([_frame(0, srt=srt), _frame(1)])
        task_dir = tmp_path / "u1" / "tasks" / "vg_intro_cap"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert props["brand"]["intro"]["durationInFrames"] == 90
        caps = props["scenes"][0]["captions"]
        assert caps and caps[0]["startMs"] == 3000 + 100, "首场景首词须晚于 intro（90 帧 = 3000ms）"
        # 第二场景累计偏移同样含 intro：3000 + 120帧/4000ms
        assert props["scenes"][1]["captions"][0]["startMs"] >= 3000 + 4000

    def test_no_intro_project_captions_start_at_zero(self, tmp_path):
        """无 intro 品牌时 offset=0（既有行为不变）。"""
        srt = "1\n00:00:00,100 --> 00:00:00,200\n你\n"
        sb = _sb([_frame(0, srt=srt), _frame(1)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert "intro" not in props["brand"]
        assert props["scenes"][0]["captions"][0]["startMs"] == 100

    def test_no_srt_synthesizes_sentence_captions(self, tmp_path):
        sb = _sb([_frame(0, narration="这句话没有字级时间戳应该合成句级字幕", speech_duration=3.0, srt="")])
        props = build_remotion_props(sb, task_dir=tmp_path)
        caps = props["scenes"][0]["captions"]
        assert caps, "无 srt 帧应有合成句级 captions"
        assert caps[0]["startMs"] == 0
        assert caps[-1]["endMs"] <= 3000 + 50

    def test_subtitle_style_none_disables_captions(self, tmp_path):
        sb = _sb([_frame(0, srt="1\n00:00:00,100 --> 00:00:00,200\n字")])
        props = build_remotion_props(sb, task_dir=tmp_path, task_payload={"subtitle_style": "none"})
        assert props["scenes"][0]["captions"] == []

    def test_srt_parser(self):
        cues = _parse_srt_cues("1\n00:00:01,500 --> 00:00:02,250\n世\n界\n")
        assert cues == [(1500, 2250, "世界")]


class TestPropsShape:
    def test_duration_in_frames_from_audio(self, tmp_path):
        sb = _sb([_frame(0, duration=2.5), _frame(1, duration=3.2)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["durationInFrames"] == round(2.5 * FPS)
        assert props["scenes"][1]["durationInFrames"] == round(3.2 * FPS)

    def test_local_visual_path_preferred(self, tmp_path):
        sb = _sb(
            [
                _frame(0),
                _frame(1, visual_source="ai_image", local_visual_path="visuals/dl_001.png", visual_type="image", visual_url="https://cdn.example.com/x.png"),
                _frame(2),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        v = props["scenes"][1]["visual"]
        assert v["path"] == "visuals/dl_001.png"
        assert v["kind"] == "image"

    def test_audio_relative_path(self, tmp_path):
        audio = tmp_path / "audio" / "abc.mp3"
        audio.parent.mkdir(parents=True, exist_ok=True)
        audio.write_bytes(b"x")
        sb = _sb([_frame(0, audio_path=str(audio))])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["audioFile"] == "audio/abc.mp3"

    def test_all_layouts_in_valid_set(self, tmp_path):
        sb = _sb([_frame(i) for i in range(10)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert all(s["layout"] in REMOTION_LAYOUTS for s in props["scenes"])


class TestBrandPackThreeStates:
    def test_no_project_default_darkblue(self, tmp_path):
        sb = _sb([_frame(0)])
        props = build_remotion_props(sb, task_dir=tmp_path, course_project_id=None)
        assert props["brand"]["themeName"] == "dark-blue"  # D12
        assert props["brand"]["subtitleStyle"]["karaokeEnabled"] is True  # D6

    def test_project_overrides(self, tmp_path):
        sb = _sb([_frame(0)])
        build_remotion_props(sb, task_dir=tmp_path, course_project_id=None)
        proj = CourseProject(id="cp_1", user_id="default", title="品牌项目")
        proj.remotion_theme = "light"
        proj.font_family = "MyFont"
        brand = resolve_brand(proj, title=sb.title)
        assert brand["themeName"] == "light"
        assert brand["fontFamily"] == "MyFont"
        assert brand["brandName"] == "品牌项目"

    def test_missing_fields_fall_back(self):
        proj = CourseProject(id="cp_2", user_id="default", title="存量项目")
        brand = resolve_brand(proj)
        assert brand["themeName"] == "dark-blue"
        assert "logoPath" not in brand
        assert "intro" not in brand

    def test_intro_outro_and_logo(self):
        proj = CourseProject(id="cp_3", user_id="default", title="全量品牌")
        proj.intro_outro = {
            "intro": {"headline": "开学第一课", "subtitle": "欢迎加入"},
            "outro": {"headline": "下次见", "follow": "关注公众号"},
        }
        proj.logo_path = "brand_assets/logo.png"
        brand = resolve_brand(proj)
        assert brand["intro"]["headline"] == "开学第一课"
        assert brand["outro"]["follow"] == "关注公众号"
        assert brand["logoPath"] == "brand_assets/logo.png"


class TestHandoffAndPayload:
    def test_should_handoff_includes_remotion_course(self):
        from app.gateway.video_gen.service import VideoGenService

        svc = VideoGenService.__new__(VideoGenService)  # 不跑 __init__（重资产）
        assert svc._should_handoff_to_worker("remotion_course") is True
        # 既有三值不动（A6：只增不改）
        for t in ("ai_image", "ai_video", "ai_visual"):
            assert svc._should_handoff_to_worker(t) is True
        assert svc._should_handoff_to_worker("vimax") is False

    def test_worker_payload_shape(self, tmp_path):
        sb = _sb([_frame(0), _frame(1)])
        props = build_remotion_props(sb, task_dir=tmp_path)
        payload = build_remotion_worker_payload(sb, props)
        assert payload["pipeline_version"] == "remotion_course"
        assert payload["remotion_props"] is props
        assert payload["video_type"] == "remotion_course"
        assert len(payload["frames"]) == 2  # 调试字段

    def test_video_type_literal_accepts_remotion_course(self):
        from app.gateway.video_gen.schemas import CreateVideoRequest

        req = CreateVideoRequest(video_type="remotion_course", mode="fixed", script="一二三")
        assert req.video_type == "remotion_course"


class TestStillPreviewFixes:
    """T-D3（R9+R10，2026-09-09 feedback-round1）：静帧预览修正。"""

    def test_still_brand_strips_intro_outro(self, tmp_path):
        """R9：静帧品牌剔除 intro/outro——修前 still_frame=30 落在 intro（90 帧）
        窗内，预览渲出片头帧而非所选版式。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(
            user_id="u1",
            title="静帧课",
            intro_outro={
                "intro": {"headline": "片头", "subtitle": ""},
                "outro": {"headline": "片尾", "follow": ""},
            },
        )
        sb = _sb([_frame(0), _frame(1, narration="正文帧内容较长应走图文版式" * 2), _frame(2)])
        task_dir = tmp_path / "u1" / "tasks" / "vg_still_fix"
        task_dir.mkdir(parents=True)
        props, key = build_still_props(
            sb,
            1,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert "intro" not in props["brand"], "静帧品牌须剔除 intro（R9）"
        assert "outro" not in props["brand"]
        assert props["scenes"][0]["layout"] in REMOTION_LAYOUTS, "still_frame=30 须落在所选版式场景"
        assert key  # cache_key 存在

    def test_still_group_first_frame_chapter_divider(self, tmp_path):
        """R10：组首帧补 ChapterDivider（与成片 D14 规则一致）。"""
        groups = [
            SceneGroup(index=0, title="第一章", frame_indices=[0, 1]),
            SceneGroup(index=1, title="第二章", frame_indices=[2, 3]),
        ]
        sb = _sb([_frame(i, narration="这一帧的旁白比较长，应该走图文版式而不是全幅视觉页。" * 2) for i in range(4)], groups=groups)
        props, _ = build_still_props(sb, 2, task_dir=tmp_path)
        assert props["scenes"][0]["layout"] == "ChapterDivider"
        assert props["scenes"][0]["layoutProps"]["title"] == "第二章"

    def test_still_non_group_frame_unchanged(self, tmp_path):
        """非组首帧版式走确定性规则表（回归锚）。"""
        sb = _sb([_frame(0), _frame(1, narration="长旁白" * 10), _frame(2)])
        props, _ = build_still_props(sb, 1, task_dir=tmp_path)
        assert props["scenes"][0]["layout"] != "ChapterDivider"

    def test_still_no_project_brand_name_empty(self, tmp_path):
        """无项目任务的 still 预览 brandName == ""（与成片 build_remotion_props
        同口径——修前无条件传 storyboard.title，任务标题（旁白前缀）被当品牌名
        显示在预览图顶栏，而成片不显示品牌行）。"""
        sb = _sb([_frame(0), _frame(1, narration="正文帧内容较长走图文版式" * 2), _frame(2)], title="任务标题前缀")
        task_dir = tmp_path / "u1" / "tasks" / "vg_still_brand"
        task_dir.mkdir(parents=True)
        props, _ = build_still_props(sb, 1, task_dir=task_dir, user_id="u1", course_project_id=None, storage_dir=tmp_path)
        assert props["brand"].get("brandName") == "", "无项目任务的 still 预览不得把任务标题当品牌名"

    def test_still_project_brand_name_kept(self, tmp_path):
        """挂项目任务的 still 预览 brandName = 项目标题（品牌主体语义不变）。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(user_id="u1", title="品牌课项目")
        sb = _sb([_frame(0), _frame(1, narration="正文帧内容较长走图文版式" * 2), _frame(2)], title="任务标题前缀")
        task_dir = tmp_path / "u1" / "tasks" / "vg_still_brand2"
        task_dir.mkdir(parents=True)
        props, _ = build_still_props(
            sb,
            1,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert props["brand"].get("brandName") == "品牌课项目"


class TestD2SlotAndBrand:
    """T-D2（D2 方案 c + R7，2026-09-09 feedback-round1）：槽位比例双保险 + 品牌名净化。"""

    def test_slot_size_mapping(self):
        from app.gateway.video_gen.pipelines.remotion_course_props import slot_size_for_layout

        assert slot_size_for_layout("ImageTextLeft") == "800x640"
        assert slot_size_for_layout("QuotePage") == "540x675"
        assert slot_size_for_layout("FullVisualPage") == "1920x1080"
        assert slot_size_for_layout("ConceptPage") is None, "文字卡无视觉槽 → 任务级"
        assert slot_size_for_layout(None) is None

    def test_assign_deterministic_layouts_only_fills_none(self, tmp_path):
        """draft 期指派只写 None 帧（用户/planner 值不覆写——D14 链不变）。"""
        from app.gateway.video_gen.pipelines.remotion_course_props import assign_deterministic_layouts

        sb = _sb(
            [
                _frame(0),
                _frame(1, layout="QuotePage"),  # planner hint / 用户手改
                _frame(2, narration="正文较长" * 8),
            ]
        )
        n = assign_deterministic_layouts(sb)
        assert n == 2, "只指派 None 帧"
        assert sb.frames[1].layout == "QuotePage"
        assert sb.frames[0].layout == "CoverPage"

    def test_draft_assignment_matches_compose_layouts(self, tmp_path):
        """draft 指派 = 成片版式（一致性不变量：生图按 draft 版式出槽位比例，
        compose 不改版式）。"""
        from app.gateway.video_gen.pipelines.remotion_course_props import assign_deterministic_layouts

        groups = [
            SceneGroup(index=0, title="一", frame_indices=[0, 1]),
            SceneGroup(index=1, title="二", frame_indices=[2, 3]),
        ]
        frames = [
            _frame(0),
            _frame(1, narration="较长正文" * 8),
            _frame(2, narration="较长正文" * 8),
            _frame(3, narration="感谢观看"),
        ]
        sb = _sb(frames, groups=groups)
        assign_deterministic_layouts(sb)
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert [s["layout"] for s in props["scenes"]] == [f.layout for f in sb.frames]

    def test_brand_name_injected_with_project(self, tmp_path):
        """R7：有项目→项目名注入 CoverPage/EndCard。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(user_id="u1", title="名师语文课")
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")], title="名师语文课")
        task_dir = tmp_path / "u1" / "tasks" / "vg_brand_inject"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
        )
        assert props["scenes"][0]["layoutProps"]["brandName"] == "名师语文课"
        assert props["scenes"][-1]["layoutProps"]["brandName"] == "名师语文课"

    def test_brand_name_empty_without_project(self, tmp_path):
        """隐-2：无项目 → 空串（模板不渲染品牌行；不注入任务标题）。"""
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")], title="某个任务标题")
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["layoutProps"]["brandName"] == ""
        assert props["scenes"][-1]["layoutProps"]["brandName"] == ""

    def test_upload_visual_gets_contain(self, tmp_path):
        """D2 兜底：上传图（local_visual_path）→ contain 不裁切。"""
        sb = _sb(
            [
                _frame(0),
                _frame(1, narration="较长正文" * 8, local_visual_path="visuals/upload.png"),
                _frame(2, narration="感谢观看"),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][1]["visual"]["fit"] == "contain"

    def test_ai_image_requests_slot_size(self):
        """接线守卫：ai_image 生图请求必须按帧版式槽位尺寸出图（F7 根治）。"""
        from pathlib import Path as P

        src = (P("__file__").parent.parent / "app" / "gateway" / "video_gen" / "visual_sources" / "ai_image.py").read_text(encoding="utf-8")
        assert 'slot_size_for_layout(getattr(frame, "layout", None))' in src, "ai_image 生图尺寸须走槽位映射（T-D2）——回归到任务级宽高本测试必须红"


class TestSubtitleStyleChain:
    """T-E9（v4.1 拍板）：subtitle_style 三级链（任务显式 > 项目 > 默认）。"""

    def test_task_level_bold_overrides_default(self, tmp_path):
        """无项目任务显式 bold → brand.subtitleStyle.variant=bold（修前只有 none 生效）。"""
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")])
        props = build_remotion_props(sb, task_dir=tmp_path, task_payload={"subtitle_style": "bold"})
        assert props["brand"]["subtitleStyle"]["variant"] == "bold"

    def test_task_level_overrides_project(self, tmp_path):
        """任务显式 > 项目（项目 bold，任务 minimal → minimal）。"""
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(user_id="u1", title="链", subtitle_style="bold")
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")])
        task_dir = tmp_path / "u1" / "tasks" / "vg_sub_chain"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
            task_payload={"subtitle_style": "minimal"},
        )
        assert props["brand"]["subtitleStyle"]["variant"] == "minimal"

    def test_project_level_applies_without_task_override(self, tmp_path):
        from app.gateway.video_gen.course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=tmp_path)
        proj = store.create(user_id="u1", title="链2", subtitle_style="outline")
        sb = _sb([_frame(0), _frame(1, narration="感谢观看")])
        task_dir = tmp_path / "u1" / "tasks" / "vg_sub_chain2"
        task_dir.mkdir(parents=True)
        props = build_remotion_props(
            sb,
            task_dir=task_dir,
            user_id="u1",
            course_project_id=proj.id,
            storage_dir=tmp_path,
            task_payload={},
        )
        assert props["brand"]["subtitleStyle"]["variant"] == "outline"

    def test_task_none_disables_captions(self, tmp_path):
        sb = _sb([_frame(0, srt="1\n00:00:00,100 --> 00:00:00,200\n你\n"), _frame(1)])
        props = build_remotion_props(sb, task_dir=tmp_path, task_payload={"subtitle_style": "none"})
        assert props["scenes"][0]["captions"] == []


class TestBrandPackRobustness:
    """T-G7（m3）：brand_pack 非数字字段安全降级（修前 int() 抛 ValueError 整任务 failed）。"""

    def test_non_numeric_frames_fall_to_default(self, tmp_path):
        from app.gateway.video_gen.brand_pack import resolve_brand
        from app.gateway.video_gen.course_models import CourseProject

        proj = CourseProject(
            id="cp_x",
            user_id="u1",
            title="健壮",
            intro_outro={"intro": {"headline": "h", "durationInFrames": "abc"}, "outro": {"headline": "h", "durationInFrames": -5}},
        )
        brand = resolve_brand(proj)
        assert brand["intro"]["durationInFrames"] == 90
        assert brand["outro"]["durationInFrames"] == 90


# ── remotion_course 独立管线 T5（2026-09-10 计划）：layout_content overlay ──


class TestLayoutContentOverlay:
    """确定性默认值 + layout_content 白名单 overlay（§4.4）。"""

    def test_overlay_priority_over_deterministic(self, tmp_path):
        sb = _sb(
            [
                _frame(0),
                StoryboardFrame(
                    index=1,
                    narration="这一段旁白本来很长，确定性推导会取首个短句作标题。",
                    duration=4.0,
                    layout="ConceptPage",
                    layout_content={"title": "自定义标题", "bullets": ["要点甲", "要点乙"]},
                ),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        lp = props["scenes"][1]["layoutProps"]
        assert lp["title"] == "自定义标题"
        assert lp["bullets"] == ["要点甲", "要点乙"]

    def test_type_coercion(self, tmp_path):
        sb = _sb(
            [
                StoryboardFrame(
                    index=0,
                    narration="流程与数据。",
                    duration=4.0,
                    layout="ProcessPage",
                    layout_content={
                        "steps": [{"name": "步骤一", "desc": "描述"}, "垃圾项", {"desc": "无名步骤"}],
                    },
                ),
                StoryboardFrame(
                    index=1,
                    narration="图表。",
                    duration=4.0,
                    layout="ChartPage",
                    layout_content={
                        "chart": [
                            {"label": "甲", "value": 150},  # 钳到 100
                            {"label": "乙", "value": "-5"},  # 钳到 0
                            {"label": "丙", "value": "62"},  # 字符串数字可转
                            {"label": "丁", "value": "abc"},  # 非法丢弃
                        ],
                    },
                ),
                StoryboardFrame(
                    index=2,
                    narration="要点。",
                    duration=4.0,
                    layout="ConceptPage",
                    layout_content={"bullets": ["正常", 42, "", None]},  # 混型→list[str]，空/None 丢弃
                ),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["layoutProps"]["steps"] == [
            {"name": "步骤一", "desc": "描述"},
            {"name": "", "desc": "无名步骤"},
        ]
        assert props["scenes"][1]["layoutProps"]["chart"] == [
            {"label": "甲", "value": 100},
            {"label": "乙", "value": 0},
            {"label": "丙", "value": 62},
        ]
        assert props["scenes"][2]["layoutProps"]["bullets"] == ["正常", "42"]

    def test_unknown_field_for_layout_dropped(self, tmp_path):
        # ConceptPage 无 quote 字段——白名单外丢弃；title 空串视为无效（保默认值）
        sb = _sb(
            [
                StoryboardFrame(
                    index=0,
                    narration="概念讲解的核心内容比较长，确定性标题会取首句。",
                    duration=4.0,
                    layout="ConceptPage",
                    layout_content={"quote": "不该进 ConceptPage", "title": "  "},
                ),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        lp = props["scenes"][0]["layoutProps"]
        assert "quote" not in lp
        assert lp["title"]  # 空串被丢 → 确定性默认值仍在

    def test_no_layout_content_byte_identical(self, tmp_path):
        """存量兼容关键：无 layout_content → 输出与现状逐字段一致。"""
        sb_plain = _sb([_frame(0), _frame(1, layout="ConceptPage")])
        sb_with_none = _sb(
            [
                _frame(0),
                StoryboardFrame(index=1, narration=_frame(1).narration, duration=4.0, layout="ConceptPage", layout_content=None),
            ]
        )
        p1 = build_remotion_props(sb_plain, task_dir=tmp_path)
        p2 = build_remotion_props(sb_with_none, task_dir=tmp_path)
        assert p1["scenes"] == p2["scenes"]

    def test_still_props_consume_and_cache_key_changes(self, tmp_path):
        """评审建议：build_still_props 的 layoutProps 实际消费 layout_content，
        且 cache key 随内容变化（改内容必换 key，否则编辑器改内容静帧不刷新）。"""
        frame_base = dict(
            index=0,
            narration="静帧预览的旁白内容。",
            duration=4.0,
            layout="ConceptPage",
        )
        f1 = StoryboardFrame(**frame_base, layout_content={"title": "版本甲"})
        f2 = StoryboardFrame(**frame_base, layout_content={"title": "版本乙"})
        f3 = StoryboardFrame(**frame_base)  # 无 content

        sb1 = _sb([f1])
        sb2 = _sb([f2])
        sb3 = _sb([f3])

        props1, key1 = build_still_props(sb1, 0, task_dir=tmp_path)
        props2, key2 = build_still_props(sb2, 0, task_dir=tmp_path)
        props3, key3 = build_still_props(sb3, 0, task_dir=tmp_path)

        assert props1["scenes"][0]["layoutProps"]["title"] == "版本甲"
        assert props2["scenes"][0]["layoutProps"]["title"] == "版本乙"
        assert props3["scenes"][0]["layoutProps"]["title"] != "版本甲"
        assert key1 != key2
        assert key1 != key3


class TestReview20260911:
    """2026-09-11 审查修复回归：chart NaN/inf 防御 + note 字段 overlay。"""

    def test_chart_nan_inf_dropped_not_crash(self, tmp_path):
        """M5：json.loads 接受裸 NaN/Infinity（LLM 可产出）——修前 round(nan) 抛
        ValueError / round(inf) 抛 OverflowError 且都在 try 外，渲染期直接炸任务。"""
        sb = _sb(
            [
                StoryboardFrame(
                    index=0,
                    narration="图表页。",
                    duration=4.0,
                    layout="ChartPage",
                    layout_content={
                        "chart": [
                            {"label": "正常", "value": 42},
                            {"label": "NaN", "value": float("nan")},
                            {"label": "Inf", "value": float("inf")},
                            {"label": "负无穷", "value": float("-inf")},
                        ]
                    },
                )
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        chart = props["scenes"][0]["layoutProps"]["chart"]
        assert chart == [{"label": "正常", "value": 42}]

    def test_note_field_overlaid(self, tmp_path):
        """M9：ChartPage/FullVisualPage 的 note（模板 SceneContent 字段）overlay 生效。"""
        sb = _sb(
            [
                StoryboardFrame(index=0, narration="图表页。", duration=4.0, layout="ChartPage", layout_content={"note": "内部评测集 · n = 120"}),
                StoryboardFrame(index=1, narration="全幅页。", duration=4.0, layout="FullVisualPage", layout_content={"note": "建议 1920 × 1080 · 全幅视觉素材"}),
            ]
        )
        props = build_remotion_props(sb, task_dir=tmp_path)
        assert props["scenes"][0]["layoutProps"]["note"] == "内部评测集 · n = 120"
        assert props["scenes"][1]["layoutProps"]["note"] == "建议 1920 × 1080 · 全幅视觉素材"
