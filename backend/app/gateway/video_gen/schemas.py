"""Schemas for video generation API endpoints."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


def _model_roster() -> set[str] | None:
    """models[] 花名册（注册名集合）；无部署配置（roster 空，CI/测试）→ None=跳过校验。

    条目 7（2026-09-12 缺陷批）：撤册模型名判定口径 = AppConfig.models[].name
    （factory.create_chat_model / get_model_config 的查找键）。独立函数便于
    测试 monkeypatch 与 create 端点复用。
    """
    try:
        from deerflow.config import get_app_config

        roster = {m.name for m in (get_app_config().models or [])}
    except Exception:  # noqa: BLE001
        return None
    return roster or None


class CreateAIVisualRequest(BaseModel):
    prompt: str = Field(..., max_length=2000)
    duration: float = Field(default=30.0, ge=5.0, le=120.0)
    style: str = "default"


ENGINE_MAP: dict[str, set[str]] = {
    "ai_image": {"auto", "dashscope"},
    # 2026-08-03 Task 7：摘除 kling（纯桩 provider，见 providers/registry.py
    # KNOWN_VIDEO_PROVIDERS 处注释）。
    "ai_video": {"auto"},
}


class CreateVideoRequest(BaseModel):
    video_type: Literal["ai_image", "ai_video", "vimax", "remotion_course"]
    mode: Literal["generate", "fixed"] = "generate"
    # 2026-10-20 Task 5：创建时自定义任务标题（仅存 payload，不加数据库列）。
    # 空串/纯空白视为未提供；超 80 字 schema 422。
    title: str | None = Field(default=None, max_length=80)
    topic: str | None = Field(default=None, max_length=500)  # for generate mode
    script: str | None = Field(default=None, max_length=20000)  # fixed 模式；放宽以支持文档解析后较长脚本
    lang: Literal["zh", "en", "ja", "ko", "fr", "de", "es"] = "zh"
    voice_id: str | None = None
    aspect: Literal["9:16", "16:9", "1:1"] | None = Field(default=None, description="画幅；不传由 config default_aspect 决定（修前写死 9:16 遮蔽 config）")
    style: str = "default"
    n_scenes: int | None = Field(default=None, ge=1, le=200, description="显式分镜数；不传则由 target_duration/seconds_per_frame 推导（修前默认 5 会漏进 payload 覆盖 target_duration）")
    target_duration: float | None = Field(default=None, ge=5.0, le=600.0, description="目标总时长(秒)，约束 generate 模式旁白字数")
    seconds_per_frame: int | None = Field(default=None, ge=3, le=8, description="单镜时长(秒)，与 target_duration 一起决定分镜数 ceil(target/spf)；修前缺失此字段，前端值被 schema 丢弃")
    # T3（2026-09-01 图文镜比例）：任务级 graphic 占比**上限**（%）。None = 默认 30%
    # （守 8-17「防半部片翻 PPT」回调史）。上限≠保障——实际占比由调度表按内容规划，
    # 内容没要点时选 70% 也不会硬凑（护栏无下限，用户已拍板知悉并在 UI 文案澄清）。
    graphic_ratio: Literal[30, 40, 50, 70] | None = Field(default=None, description="图文镜占比上限(%)；None=默认 30%。上限≠保障：实际占比由内容决定，不会超过所选值")
    ai_normalize_script: bool = Field(default=True, description="C1: 上传文档默认 LLM 改写成干净旁白（剥标题/页码/项目符号）；短输入(<80字)直接切")
    subtitle_style: Literal["default", "bold", "minimal", "outline", "none"] = "default"
    # E（2026-08-31 转场清理）：默认值 fade → none——转场 UI 已删（假选项比没有
    # 更误导），W1 对 ai_image/ai_video 本就强制 none，vimax 不消费转场字段
    # （2026-08-31 核查），remotion 是死代码路径。存量显式 fade 由 W1/默认值压成
    # none，无害。Literal 保留存量草稿只读兼容。
    transition: Literal["none", "fade", "dissolve", "slide_left", "slide_right", "wipe_left", "wipe_right", "zoom_in"] = "none"
    transition_duration: float = Field(default=0.5, ge=0.2, le=2.0)
    bgm: str | None = None
    template_id: str | None = None
    template_params: dict | None = None
    output_format: Literal["mp4", "webm", "gif"] = "mp4"
    quality: Literal["draft", "standard", "high", "preview"] = "standard"
    # video_count 已移除（决策 1 / 2026-07-31）：N 个任务除 version_index 外完全
    # 相同，5 倍成本换来近乎一样的成片，且实际无人使用。pydantic 默认忽略未知
    # 字段，存量草稿 payload 里残留的 video_count 不会 422。
    tts_speed: float | None = Field(default=None, ge=0.5, le=2.0, description="TTS 语速；不传由 config tts_speed 决定（修前写死 1.0 遮蔽 config 1.2）")
    bgm_volume: float = Field(default=0.15, ge=0.0, le=1.0)
    bgm_ducking: bool = Field(default=True, description="Enable BGM ducking when voice is active")
    engine: str = "auto"
    # Task 6（2026-08-01 反馈 8）：任务级生成模型选择。
    #
    # **不复用 `engine`** —— 它有自己的 ENGINE_MAP 语义且需兼容历史值；而且实测
    # `engine` 是**死旋钮**（_make_visual_source 根本不读它）。故新增两个独立字段：
    # `None` = 跟随 config.yaml 默认（含既有降级链，行为与改动前一致）；
    # 显式指定 = **不降级**（用户决策 4：选了 A 却静默出 B 比失败更糟）。
    #
    # 白名单取自 registry 的静态已知集合，避免 schema 依赖运行时 config。
    # 「有 key 才可用」由 GET /api/video/providers 的 available 字段体现。
    image_provider: str | None = Field(default=None, description="任务级生图模型；None=跟随 config 默认")
    video_provider: str | None = Field(default=None, description="任务级生视频模型；None=跟随 config 默认")
    # E6（2026-08-21）：任务级脚本/分镜 LLM 模型（models[] 注册名）；None=跟随
    # config.script_model。语义与 image_provider 一致：显式指定优先。
    # 未在 models[] 注册的名字由 create_chat_model 报错（无静态白名单可比——
    # models[] 是部署时配置）。
    script_model: str | None = Field(default=None, description="任务级脚本/分镜 LLM（models[] 注册名）；None=跟随 config.script_model")
    auto_render: bool = True
    course_project_id: str | None = None
    # remotion_course（2026-09-10 独立管线计划 T7）：解析双档——faithful 保真（默认，
    # 只优化表达适合口播，不改大意与数据）/ rewrite AI 改写。仅 remotion_course 消费，
    # 其他类型忽略。UI radio 仅这两档（决策 13）；"纯切分"仅在 API 层经
    # ai_normalize_script=False 保留。
    course_parse_mode: Literal["faithful", "rewrite"] = "faithful"
    # script/extract?with_images=1 返回的暂存 id——任务启动时认领嵌入图（决策 7）；
    # 仅 remotion_course 消费，其他类型忽略。max_length=40 与认领侧 ULID 白名单
    # （course_staging._EXTRACT_ID_RE 8-40）对齐——超长 id 与其在认领时被静默判非法，
    # 不如入口 422（2026-09-11 审查 Minor）。
    script_extract_id: str | None = Field(default=None, max_length=40, description="docx 图片暂存 extract_id（with_images 提取返回）；仅 remotion_course 消费")

    @field_validator("title", mode="before")
    @classmethod
    def _normalize_title(cls, value: object) -> object:
        """空串/纯空白 = 未提供；标题长度校验作用于裁剪后的值。"""
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="before")
    @classmethod
    def _strip_explicit_nulls(cls, data: object) -> object:
        """显式 null = 未指定（2026-08-31，R4 类推广）。

        pydantic v2 对非 Optional 标量默认字段（ai_normalize_script/transition_
        duration/bgm_volume/bgm_ducking/auto_render）的显式 null 一律 422。前端既有
        约定是「不指定不下发该字段」（create-payload.ts），但任何 spread 状态对象的
        调用方（脚本/未来组件）都可能带出 null——mode='before' 统一剥掉。本模型所有
        可空字段的 null 均等价于其默认值，剥除无语义损失；未来若新增「null 有独立
        语义」的字段，须改为白名单式排除。
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    @model_validator(mode="after")
    def _check_providers(self):
        """校验模型名，并确保两个命名空间不混用（选错要 422，不能到 worker 才炸）。"""
        from .providers.registry import ProviderRegistry

        if self.image_provider is not None and self.image_provider.lower() not in ProviderRegistry.KNOWN_IMAGE_PROVIDERS:
            raise ValueError(f"未知的生图模型 '{self.image_provider}'；可选：{', '.join(ProviderRegistry.KNOWN_IMAGE_PROVIDERS)}")
        if self.video_provider is not None and self.video_provider.lower() not in ProviderRegistry.KNOWN_VIDEO_PROVIDERS:
            raise ValueError(f"未知的生视频模型 '{self.video_provider}'；可选：{', '.join(ProviderRegistry.KNOWN_VIDEO_PROVIDERS)}")
        # E6：空白串是「想指定但没填」的配置错误信号——422 拒绝，不静默当 None。
        if self.script_model is not None and not self.script_model.strip():
            raise ValueError("script_model 不能为空白串；不指定请省略该字段（跟随 config.script_model）")
        # 条目 7（2026-09-12 缺陷批）：花名册校验——撤册模型名建单即 422（可操作
        # 文案），不再受理后硬 ValueError（script_model 走 factory.py:104「Model
        # not found」任务立刻 failed）/静默降级（subtitle 回规则链零留痕）不对称爆。
        # config 级三字段（subtitle/shot_table/vimax_llm_model）在 create 端点校验
        # （schema 层拿不到部署 config）。无部署配置（roster 空，CI/测试环境）跳过
        # ——只拒「可判定」的错。
        roster = _model_roster()
        if roster and self.script_model and self.script_model not in roster:
            raise ValueError(f"模型 {self.script_model} 未在花名册注册，请联系管理员检查 config.yaml models 配置")
        return self

    @model_validator(mode="after")
    def _check_inputs(self):
        if self.mode == "generate" and not self.topic:
            raise ValueError("topic is required when mode='generate'")
        if self.mode == "fixed" and not self.script:
            raise ValueError("script is required when mode='fixed'")
        return self

    @model_validator(mode="after")
    def _check_engine(self):
        allowed = ENGINE_MAP.get(self.video_type, {"auto"})
        if self.engine not in allowed:
            raise ValueError(f"engine '{self.engine}' not valid for {self.video_type}, allowed: {sorted(allowed)}")
        return self


class VideoCostEstimate(BaseModel):
    """ViMax 只读费用/成片时长预估（2026-10-20 Task 6；不参与扣费/预算闸）。"""

    currency: str = "CNY"
    low_cny: float | None = None
    high_cny: float | None = None
    point_cny: float | None = None
    basis: Literal["history", "price_table", "partial"]
    n_shots: int
    video_seconds: float
    n_characters: int
    breakdown: dict[str, float | None] = Field(default_factory=dict)
    unpriced: list[str] = Field(default_factory=list)
    spent_cny: float | None = None


class ReorderRequest(BaseModel):
    from_index: int = Field(..., ge=0)
    to_index: int = Field(..., ge=0)
