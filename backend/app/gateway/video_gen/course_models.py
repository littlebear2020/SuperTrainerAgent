"""Course-level data models for the video gen course studio.

CourseOutline: LLM-generated outline with chapters and key points.
CourseProject: User-created project container for visual consistency across videos.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from .style_presets import style_prefix_for


@dataclass
class OutlineChapter:
    """One chapter in a course outline."""

    title: str
    key_points: list[str]
    estimated_minutes: int = 3


@dataclass
class CourseOutline:
    """Full course outline produced by LLM."""

    title: str
    chapters: list[OutlineChapter]
    total_estimated_minutes: int = 0

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "chapters": [asdict(ch) for ch in self.chapters],
            "total_estimated_minutes": self.total_estimated_minutes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CourseOutline:
        valid_keys = set(OutlineChapter.__dataclass_fields__)
        chapters = [OutlineChapter(**{k: v for k, v in ch.items() if k in valid_keys}) for ch in data.get("chapters", [])]
        return cls(
            title=data["title"],
            chapters=chapters,
            total_estimated_minutes=data.get("total_estimated_minutes", 0),
        )


@dataclass
class CharacterSheet:
    """角色三视图固化资产（front/side/back + 特征描述）。

    Task 10/11 生成或上传，Task 12/13 注入各引擎作 reference，实现跨视频角色一致。
    M1（v7 拍板）：``id`` 供管理端点寻址（替代数组下标——删除/替换/排序不再随数组
    漂移）；帧级引用键仍是 ``name``（LLM 面向旁白的自然键）。
    """

    id: str = ""
    name: str = ""
    front_url: str = ""
    side_url: str = ""
    back_url: str = ""
    # 合成图方案（2026-08-27 计划 X1）：每角色 1 张三视图合成图（正/侧/背横向拼接，
    # 生成/上传时拼图落盘，注入通道恒带本图——分离三视图保留展示不再进注入）。
    # ⚠️ to_dict/from_dict 是手写显式字段：本字段必须两处各加一行，不会自动携带。
    composite_url: str = ""
    features: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "front_url": self.front_url,
            "side_url": self.side_url,
            "back_url": self.back_url,
            "composite_url": self.composite_url,
            "features": self.features,
        }

    @classmethod
    def from_dict(cls, d: dict) -> CharacterSheet:
        import uuid

        return cls(
            # M1：存量无 id → 生成 uuid4().hex[:8] 回填（惰性：下次 update 落盘）
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", ""),
            front_url=d.get("front_url", ""),
            side_url=d.get("side_url", ""),
            back_url=d.get("back_url", ""),
            # X1 白名单兼容：存量 JSON 无 composite_url → 落 ""（注入侧惰性兜底 X5）
            composite_url=d.get("composite_url", ""),
            features=d.get("features", ""),
        )


@dataclass
class PropAsset:
    """M6 道具资产（决策 12/15/16/18）：固定产品跨视频稳定出现。

    形态（决策 16，行业调研背书）：主图 1 张（实拍、干净/白底、产品完整清晰）+
    补充角度 0~2 张（仅实拍——AI 生成补角度有幻觉固化风险，禁止）。
    ``id`` 供管理端点寻址（同 M1 纪律）；``name`` 是帧级引用键（旁白称呼，唯一）。
    保真边界（决策 18）：logo/包装文字特写的硬保真不在本计划——UI 文案管理预期。
    """

    id: str = ""
    name: str = ""
    main_image: str = ""
    extra_images: list = None  # type: ignore[assignment]  # list[str] ≤2，默认 [] 见 __post_init__
    description: str = ""

    def __post_init__(self) -> None:
        if self.extra_images is None:
            self.extra_images = []

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "main_image": self.main_image,
            "extra_images": list(self.extra_images),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PropAsset:
        import uuid

        extras = d.get("extra_images") or []
        return cls(
            id=d.get("id") or uuid.uuid4().hex[:8],
            name=d.get("name", ""),
            main_image=d.get("main_image", ""),
            extra_images=[str(x) for x in extras],
            description=d.get("description", ""),
        )


@dataclass
class CourseProject:
    """User-created project container for course video consistency."""

    id: str
    user_id: str
    title: str
    visual_style_preset: str = "default"
    character_reference: str | None = None
    color_palette: dict | None = None
    voice_id: str = ""
    # T-E11（D6，2026-09-09 feedback-round1）：项目级默认语速。None = 未设置
    # （走任务缺省/config tts_speed）；存量项目缺字段 → dataclass 默认值零迁移。
    tts_speed: float | None = None
    subtitle_style: str = "default"
    # Task 9: 三视图角色固化 + 场景风格参考图（跨引擎共享增强层）
    character_sheets: list = None  # type: ignore[assignment]  # list[CharacterSheet]，默认 [] 见 __post_init__
    # deprecated（C2，决策 7）：不再参与 style_prefix 构建；字段保留仅为存量 JSON 兼容，
    # 不读取、不迁移。风格参考职责由 style_anchor_image 承担。
    scene_style_refs: list = None  # type: ignore[assignment]  # list[str] 风格参考图 URL
    # C1（2026-08-24 计划）：风格锚图本地绝对路径（同三视图纪律）；None = 未设置。
    # 命名避让既有 VIDEO_GEN_IMAGE_STYLE_ANCHOR（跨帧弱锚）与组锚概念。
    style_anchor_image: str | None = None
    # T5（2026-08-31 候选持久化）：AI 生成成功但未固化的候选**文件名**（位于
    # style_anchor/{pid}/candidates/ 下）。修前候选是前端易失 state——退出/刷新/
    # 生成期间切走页面即丢（「生成后无法保存」事故）。固化/删除锚图/换一张/生成
    # 失败时清空；前端挂载时非空自动恢复候选条。None = 无未固化候选。
    style_anchor_candidate: str | None = None
    # M6（决策 12）：道具资产列表；None → __post_init__ 兜底 []（存量项目零迁移）
    prop_assets: list = None  # type: ignore[assignment]  # list[PropAsset]
    # ── 品牌包 A 类字段（2026-09-08 remotion-course 计划 T8）─────────────
    # 消费方 = brand_pack.resolve_brand（remotion_course 任务）。存量项目缺字段
    # → dataclass 默认值零迁移（store 读 JSON 走 dataclass 字段白名单）。
    # remotion_theme：四主题预设（dark-blue/light/dark-green/warm），缺省走平台默认。
    remotion_theme: str | None = None
    # 字体栈（CSS font-family 值；None = 主题默认 PingFang/Noto Sans 栈）
    font_family: str | None = None
    # LOGO 资产（项目资产目录相对路径或绝对路径；brand_pack 透传，worker 本地化）
    logo_path: str | None = None
    # 片头/片尾文案：{"intro": {headline, subtitle, durationInFrames},
    #               "outro": {headline, follow, durationInFrames}}；None = 无片头片尾
    intro_outro: dict | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        # dataclass 默认值不能用 []（可变），用 None + post_init 兜底
        if self.character_sheets is None:
            self.character_sheets = []
        if self.scene_style_refs is None:
            self.scene_style_refs = []
        if self.prop_assets is None:
            self.prop_assets = []

    def to_dict(self) -> dict:
        d = asdict(self)
        # asdict 把 CharacterSheet 递归转 dict（序列化正确）；显式标注类型清晰
        return d

    @classmethod
    def from_dict(cls, data: dict) -> CourseProject:
        valid_keys = set(cls.__dataclass_fields__)
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        # Task 9: 反序列化 character_sheets dict → CharacterSheet 对象
        sheets_data = filtered.get("character_sheets") or []
        filtered["character_sheets"] = [cs if isinstance(cs, CharacterSheet) else CharacterSheet.from_dict(cs) for cs in sheets_data]
        props_data = filtered.get("prop_assets") or []
        filtered["prop_assets"] = [pa if isinstance(pa, PropAsset) else PropAsset.from_dict(pa) for pa in props_data]
        # B1 修复（复核阻断项）：随机回填 id 仅「下次 update 落盘」在读路径不成立——
        # 每次加载生成新 id，id 寻址（serve/delete/replace）会间歇 404。标记回填事实，
        # store.get/list 读到即落盘（首次读后 id 稳定；标记属性不进 to_dict/asdict）。
        obj = cls(**filtered)
        obj._ids_backfilled = any((isinstance(d, dict) and not d.get("id")) for d in (data.get("character_sheets") or [])) or any((isinstance(d, dict) and not d.get("id")) for d in (data.get("prop_assets") or []))
        return obj
        if "scene_style_refs" not in filtered or filtered["scene_style_refs"] is None:
            filtered["scene_style_refs"] = []
        return cls(**filtered)

    @property
    def style_prefix(self) -> str:
        """Build a style prefix string for injection into image prompts.

        2026-08-01（Task 2 Step 4b）三处修正 —— 本属性此前是反馈 5「画面提示词
        变英文」在**项目级**链路上的根因（真实任务 vg_d0779ddd81f8 佐证）：

        1. `visual_style_preset` 原样拼接 → 改为查 `style_presets` 表翻译成中文。
           存量项目存的是英文字面量（`'3D render'`），原样进 prompt 会把整条
           画面词带成英文；未知值也不再泄漏（宁可无前缀，不可注入英文）。
        2. **删掉 `color_palette` 的文本拼接。** 它是给 ffmpeg `eq` 滤镜的数值参数
           （`_ffmpeg_helpers._colorgrade_vf` 已消费并真实生效），拼进 prompt 属
           重复消费；且 `saturation: 1` 是恒等值，对生图模型是纯噪声。
           **字段本身保留** —— worker 侧调色仍在用。
        3. 删掉末尾写死的 `"professional lighting, high quality, detailed"`。
           质量词由 `_enhance_prompt_quality` 统一在尾部追加，此处是重复且为英文。

        角色描述与场景参考图（本来就是中文）保持不动。
        """
        parts: list[str] = []
        # 查表翻译：新 key 与存量英文字面量都能命中；未知值返回空串（不泄漏原文）
        prefix = style_prefix_for(self.visual_style_preset)
        if prefix:
            parts.append(prefix)
        if self.character_reference:
            parts.append(self.character_reference)
        # C2（决策 7，2026-08-24 计划）：退役 scene_style_refs 的文本拼接——该字段把
        # URL 字符串当文字拼进 prompt，图片从未进入生图参考图通道（名存实亡）。
        # 字段保留不删（存量 JSON 兼容）、不读取、不迁移；风格参考图职责由
        # style_anchor_image（C1/C4 专用参数通道）承担。
        return ", ".join(parts)
