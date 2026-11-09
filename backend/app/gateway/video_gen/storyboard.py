"""Storyboard data structures shared by all visual sources.

Modeled on Pixelle-Video's pixelle_video/models/storyboard.py, trimmed to this project's needs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class StoryboardFrame:
    index: int
    narration: str
    # Visual source inputs (filled by storyboard_planner; one or both per video_type)
    image_prompt: str | None = None  # AI image/video
    search_term: str | None = None  # legacy stock field (retained for backward-compat with historical storyboards; no longer populated)
    visual_source: str | None = None  # per-frame override: "ai_image"|"ai_video"|"graphic"（存量 "remotion" 继续由 worker 文字卡兜底）
    # graphic-native-text（2026-08-17）：graphic 帧（图内文字镜）的结构化数据
    # （graphic_spec.py 的 dict 形态；数字逐字符校验来自原文，纯文字允许 LLM 概括
    # ——validate_spec_against_narration 实际口径）。消费方 =
    # graphic_text.inject_graphic_text（注入 image_prompt 由生图画进画面）；
    # None = 无 spec（按普通画面渲染，不注入）。
    # ⚠️ 拆帧路径（service._split_long_narration_frames）显式 kwargs 重建帧**故意不带**
    # 本字段——旁白已改写，旧 spec 事实失效；子帧 visual_source 继承、不注入。
    graphic_spec: dict | None = None
    # Production artifacts (filled in Gateway prepare stage)
    audio_path: str | None = None
    duration: float = 0.0
    visual_url: str | None = None
    visual_type: str | None = None  # "image" | "video"
    srt: str = ""  # Word-level SRT from TTS (edge-tts WordBoundary events)
    speech_duration: float | None = None  # 真实 TTS 语音时长（字幕配比基准，不含 spf 补的静音）；None = 与 duration 相同
    local_visual_path: str | None = None  # user-uploaded local visual (relative to task_dir)
    # 2026-08-03 (Task 1): 生成后 ffprobe 实测的视觉时长。None = 未探测（旧 storyboard
    # 或非 ai_video）。与 duration（音频权威时间轴）分离——duration 决定成片节拍，
    # 本字段只用于「模型实际给了几秒」的诚实告知与补偿计算，不参与 SRT 编排。
    actual_visual_duration: float | None = None
    # 2026-08-03 (Task 8): 镜头语言。视频模型对「中景」「缓慢推近」这类术语的语义权重
    # 高于模糊描述，故与 image_prompt 分离为结构化字段（LLM 更容易稳定产出枚举值，
    # 且下游可做连贯性决策——见 Task 9）。None = 未指定，提示词不追加。
    shot_size: str | None = None  # wide | medium | close
    camera_move: str | None = None  # static | push_in | pull_out | pan | tilt
    # 2026-08-17（Phase 1 T1 方言层）：结构化画面 IR（翻译器输入）。planner LLM 产出
    # {subject, action, environment, style, audio}；prompt_translators 按 provider 方言
    # 序列化（H3 三字段 / Seedance 五槽）。None = 旧 storyboard / 手改帧（编辑端点清
    # IR）/ 拆帧子帧（不继承，同 graphic_spec 先例）-> 一律 legacy 透传（P0-5 不变量）。
    # shot_size/camera_move 仍是镜头语言唯一事实源（Task 8 字段），IR 不重复存。
    visual_ir: dict | None = None
    # M2（2026-08-24 计划二期）：逐镜出场声明（对象数组）。语义铁律——
    # None（存量/缺字段/LLM 不合作）= 回退旧行为（角色=首主角三视图全传、道具=无）；
    # [] = 本镜明确无人物/无道具。元素：角色 {"name": str, "view"?: "front"|"side"|"back"}、
    # 道具 {"name": str, "view"?: int（0=main_image，i≥1=extra_images[i-1]）}。
    # name 是帧级引用键（LLM 自然键，须在项目名册内——归一化见 planner）。
    visible_characters: list | None = None
    visible_props: list | None = None
    # remotion_course（2026-09-08 计划 T6a）：版式指派。取值 = 19 版式 PascalCase id
    # （LayoutRouter 键）。D14 优先级链：用户手改 > planner hint > 确定性规则表——
    # 字段即"生效值"：编辑器写回直接覆盖；planner 只在 None 时写（不覆写既有值，
    # 用户选择天然胜出）；None = 走 remotion_course_props 的确定性映射。
    # 仅 remotion_course 消费；ai_image/ai_video 不读（无版式概念，D13）。
    layout: str | None = None
    # remotion_course（2026-09-10 独立管线计划 T1）：版式结构化内容（模板 SceneContent
    # 共享扁平接口的子集——版式↔字段映射见 3.3 表/cours 版式契约）。与 layout 同纪律：
    # 用户编辑器写回 = 生效值（parser 产出 > 确定性推导），仅 remotion_course 消费
    # （remotion_course_props._layout_props 白名单 overlay）；ai_image/ai_video 不读。
    # 永不承载图片数据——图槽素材走 local_visual_path/visual_url（visual 通道）。
    # 旧 storyboard 无此键 → from_dict 白名单默认 None（零迁移兼容）。
    layout_content: dict | None = None
    # remotion_course（§3.6 一单元多图顺延，2026-09-11 审查补交付）：该帧配图来自
    # 邻近单元的多图顺延（"来自邻近单元的文档配图"）——编辑器素材区标注供用户纠正。
    # 仅展示用途，渲染链不读；旧 storyboard 无此键 → None。
    image_note: str | None = None


@dataclass
class SceneGroup:
    """A group of consecutive frames representing a content segment (e.g. a chapter section)."""

    index: int
    title: str
    frame_indices: list[int]
    group_duration: float = 0.0
    visual_source_override: str | None = None
    visual_theme: str | None = None
    # E（2026-08-31 转场清理）：默认 fade → none（vimax 不消费转场字段——
    # 2026-08-31 核查；W1 对 ai_image/ai_video 组级同强转 none，见 service.py）
    transition_in: str = "none"
    transition_out: str = "none"
    transition_duration: float = 0.8
    # T6（2026-08-18 两反馈批 P-05）：分组来源标记。"planner" = storyboard_planner
    # 生产分组器产出（普通任务）；None = course_pipeline 段落分组等既有来源。
    # 消费方 = worker_probe.should_use_segmented_pipeline 的 v3 栅栏——planner
    # groups 第一步只供 T-S1/T-S2 消费，不转活 worker v3_segmented 管线。
    origin: str | None = None


@dataclass
class StoryboardConfig:
    video_type: str  # ai_image|ai_video|remotion|vimax|comic
    width: int
    height: int
    fps: int = 30
    voice_id: str = "preset:146"  # 360 fallback (doubao is the default via resolve_default_voice, not the fallback)
    tts_speed: float = 1.2
    subtitle_enabled: bool = True
    subtitle_style: str = "default"
    # E（2026-08-31 转场清理）：默认 fade → none（对齐 schemas.CreateVideoRequest；
    # 前端已删转场控件并不再下发 transition 键——缺省即硬切）
    transition: str = "none"
    transition_duration: float = 0.5
    bgm_path: str | None = None
    bgm_volume: float = 0.15
    bgm_ducking: bool = True
    style: str | None = None
    color_palette: dict | None = None  # Task 3l: 项目调色板，worker _ffmpeg_colorgrade 消费
    # T3（2026-09-01 图文镜比例）：任务级 graphic 占比上限（%）。None = 默认 30%。
    # asdict/from_dict 白名单自动完成双向持久化（重建/续点/详情页可读），零额外代码。
    # 上限≠保障：只抬护栏天花板，不强迫 LLM 多推荐。
    graphic_ratio: int | None = None


# remotion_course 版式全集（2026-09-08 T6a；模板 LayoutRouter 的 19 键）。
# 单一事实源：remotion_course_props/planner/编辑器守卫共用。
REMOTION_LAYOUTS = (
    "CoverPage",
    "ObjectivesPage",
    "OutlinePage",
    "ChapterDivider",
    "ConceptPage",
    "ImageTextLeft",
    "ImageTextRight",
    "FullVisualPage",
    "VideoClipPage",
    "ComparisonPage",
    "ChartPage",
    "ProcessPage",
    "CodePage",
    "QuotePage",
    "CasePage",
    "SummaryPage",
    "QuizPage",
    "EndingPage",
    "EndCard",
)


@dataclass
class ShotDirection:
    """S1（2026-08-26 镜头调度表计划）：全片调度表的一行（帧序号绑定）。

    阶段 1 一次轻量 LLM 调用联合设计全片镜头调度（治 E8 逐帧化下连续镜头
    人物位置/机位独立抽签的鬼畜问题）；阶段 2 逐帧扩写注入本帧行 + ±1 邻行，
    ``shot_size``/``camera_move`` 从本行确定读取（不再采信逐帧返回值）。
    枚举域与 StoryboardFrame.shot_size/camera_move 一致（wide/medium/close ×
    static/push_in/pull_out/pan/tilt——compose_video_prompt 术语表依赖）。
    """

    frame_index: int
    shot_size: str
    camera_move: str
    # remotion_course（2026-09-08 T6）：调度表可选版式意图列（D14 planner hint）。
    # None = 该行未产出/非法枚举被剔除 → 帧走确定性规则表。
    layout: str | None = None
    subject_position: str = ""  # 人物/主体在画面中的位置与朝向（跨镜连续承接的关键字段）
    composition: str = ""  # 构图
    continuity: str = ""  # 与前后镜的承接要点
    # 图文镜路由表列（2026-08-31 路由并入计划）：ai_image/ai_video/graphic。
    # None = 该行未产出（LLM 漏列/非法值归一，或存量行无此键）——帧不动，回退逐帧推荐。
    visual_source: str | None = None


@dataclass
class Storyboard:
    title: str
    config: StoryboardConfig
    frames: list[StoryboardFrame] = field(default_factory=list)
    scene_groups: list[SceneGroup] = field(default_factory=list)
    total_duration: float = 0.0
    subtitle_locked: bool = False  # 用户手改字幕后置锁：recompose 用用户 SRT；旁白变更清锁重新生成
    # T5（2026-08-18 两反馈批）：全片锚定卡——plan_course 分块前一次锚定调用产出
    # {anchor_card, style_string}，注入每块 system + IR style 槽统一覆写消费。
    # 2026-08-19 起**持久化**（to_dict/from_dict）：「落盘无消费方」前提已被打破——
    # regenerate_frame_prompt 端点从 storyboard.json 重读后重生单帧描述，需注入同一份
    # 锚定卡（否则重生帧与 bulk 帧风格保护不对等）；单帧兜底链风格守卫同源消费。
    # 旧 storyboard 无此键 → from_dict 默认 None（调用方全部 getattr/缺省容错）。
    global_anchor: dict | None = None
    # T5b（2026-08-18 拍板⑦）：任务级信息图版式卡（配色基调+字体风格+版式骨架，
    # 锚定调用现场设计一次）——**持久化**（to_dict/from_dict）：渲染与预览在别的
    # 时点/进程从 storyboard.json 重读，不落盘任务级版式就断了。None/空 = 锚定
    # 失败或无字段，graphic_text.inject_graphic_text 回落确定性默认版式卡常量。
    graphic_layout_card: str | None = None
    # S1（2026-08-26 调度表计划）：全片镜头调度表（list[ShotDirection]，帧序号绑定；
    # 插删帧行号重排见 frame_ops.remap_shot_table_*）。None = 未生成/二次失败降级/
    # 存量任务（该任务逐帧扩写走现状零注入——AC3/AC6 降级态）。**无表时 to_dict
    # 不含该键**（shot_table_enabled=false 产物形状与现状逐字节一致，AC3）。
    shot_table: list[ShotDirection] | None = None

    def recompute_duration(self) -> None:
        self.total_duration = sum(f.duration for f in self.frames)

    def to_dict(self) -> dict:
        """Serialize storyboard to a plain dict (for JSON persistence)."""
        d = {
            "title": self.title,
            "config": asdict(self.config),
            "frames": [asdict(f) for f in self.frames],
            "scene_groups": [asdict(g) for g in self.scene_groups],
            "total_duration": self.total_duration,
            "subtitle_locked": self.subtitle_locked,
            # T5b：版式卡持久化（渲染/预览重读 storyboard.json 消费）。
            "graphic_layout_card": self.graphic_layout_card,
            # 2026-08-19：锚定卡持久化（重生端点/兜底链风格守卫重读消费——见字段注释）。
            "global_anchor": self.global_anchor,
        }
        # S1：无表不含键（AC3 关闭/降级态产物形状与现状一致；存量 storyboard 重存零 diff）。
        if self.shot_table:
            d["shot_table"] = [asdict(r) for r in self.shot_table]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> Storyboard:
        """Deserialize storyboard from a dict (inverse of to_dict).

        Igores unknown fields for forward compatibility.
        """
        cfg_data = data["config"]
        valid_cfg_keys = set(StoryboardConfig.__dataclass_fields__)
        config = StoryboardConfig(**{k: v for k, v in cfg_data.items() if k in valid_cfg_keys})
        valid_frame_keys = set(StoryboardFrame.__dataclass_fields__)
        frames = [StoryboardFrame(**{k: v for k, v in f.items() if k in valid_frame_keys}) for f in data["frames"]]
        valid_group_keys = set(SceneGroup.__dataclass_fields__)
        raw_groups = data.get("scene_groups", [])
        scene_groups = [SceneGroup(**{k: v for k, v in g.items() if k in valid_group_keys}) for g in raw_groups]
        valid_sd_keys = set(ShotDirection.__dataclass_fields__)
        raw_table = data.get("shot_table") or []
        # S1：旧 storyboard 无键/空表 → None（降级态语义，见字段注释）。
        shot_table = [ShotDirection(**{k: v for k, v in r.items() if k in valid_sd_keys}) for r in raw_table if isinstance(r, dict)] or None
        return cls(
            title=data["title"],
            config=config,
            frames=frames,
            scene_groups=scene_groups,
            total_duration=data.get("total_duration", 0.0),
            subtitle_locked=data.get("subtitle_locked", False),
            # T5b：旧 storyboard 无该键 → None（渲染回落确定性默认版式卡）。
            graphic_layout_card=data.get("graphic_layout_card"),
            # 2026-08-19：旧 storyboard 无该键 → None（风格守卫/重生端点跳过锚注入）。
            global_anchor=data.get("global_anchor"),
            shot_table=shot_table,
        )


def remap_scene_groups_after_insert(groups: list[dict], insert_index: int) -> list[dict]:
    """帧插入后的 scene_groups 同步：>=insert_index 的旧序号 +1；新帧并入前一镜
    （insert_index-1，即「在 N 镜后插入」手势锚定的 N）所在组；插到最前
    （index 0，无前一镜）并入原第一镜（insert_index+1）所在组；都找不到
    （前一镜无组等历史/异常数据）并入最后一组。无分组时原样返回。"""
    if not groups:
        return groups
    for g in groups:
        g["frame_indices"] = [i + 1 if i >= insert_index else i for i in g.get("frame_indices", [])]
    host = None
    for g in groups:
        if insert_index - 1 in g.get("frame_indices", []):
            host = g
            break
    if host is None and insert_index == 0:
        for g in groups:
            if insert_index + 1 in g.get("frame_indices", []):
                host = g
                break
    if host is None:
        host = groups[-1]
    host["frame_indices"] = sorted(set(host["frame_indices"]) | {insert_index})
    return groups


def remap_scene_groups_after_delete(groups: list[dict], deleted_index: int) -> list[dict]:
    """帧删除后的 scene_groups 同步：移除被删序号，>deleted_index 的序号 -1；
    清空组剔除并对剩余组重排 index。"""
    for g in groups:
        kept = [i for i in g.get("frame_indices", []) if i != deleted_index]
        g["frame_indices"] = [i - 1 if i > deleted_index else i for i in kept]
    remaining = [g for g in groups if g.get("frame_indices")]
    for n, g in enumerate(remaining):
        g["index"] = n
    return remaining
