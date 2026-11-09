"""Provider registry — assembles providers from config with fallback support."""

from __future__ import annotations

import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import httpx

from .base import AIVisualImageProvider, AIVisualVideoProvider, ImageRef, VideoRef
from .capabilities import video_capability
from .throttle import ProviderUnavailableError, RateLimitError

if TYPE_CHECKING:
    from app.gateway.video_gen.config import VideoGenConfig

logger = logging.getLogger(__name__)


# C: 标记哪些 image provider 真消费 reference_image_paths（i2i）。
# seedream (Task6 实现后)、gpt-image-2 消费参考图；
# E4（2026-08-21）：seedream-360 经 /v1/images/edits（extra_body.image）补全
# 参考图能力（T3 实测单图编辑/多图融合通过），纳入白名单。
# 2026-09-23：gpt-image-2.5-flare/sunburst **不纳入**——spike（temp/360_spike_0923）
# 实测 2.5 系 edits 参考图全变体 400（data URL/公网 URL/单数字段/multipart 均被拒），
# 进白名单会让 remotion_course 参考图链路选中即失败。
# dashscope 纯文生图会静默丢参考图（ai_image 角色一致性/三视图注入/跨帧锚依赖 i2i）。
I2I_CAPABLE_IMAGE_PROVIDERS: set[str] = {"seedream", "gpt-image-2", "seedream-360"}

# Default QPS and retry settings per provider type
_DEFAULT_THROTTLE = {
    "ai_image": {"qps": 1.0, "max_retries": 3},
    "ai_video": {"qps": 0.5, "max_retries": 5},
}


# ── Fallback wrapper ─────────────────────────────────────────────────────

# Provider infrastructure errors that warrant trying an alternate provider.
# Do NOT fallback on ValueError/TypeError (those are caller bugs).
# P1-4: httpx 异常族**不继承** builtins 的 TimeoutError/ConnectionError
# （实测 issubclass 全为 False，经 HTTPError 直接继承 Exception），
# 故必须显式列出。TransportError 是全部传输类异常的共同祖先。
_FALLBACK_EXC = (
    ProviderUnavailableError,
    RateLimitError,
    TimeoutError,
    ConnectionError,
    httpx.TimeoutException,
    httpx.TransportError,
)


class FallbackAIImageProvider:
    """Wraps a primary AI image provider with an optional fallback."""

    def __init__(self, primary: AIVisualImageProvider, fallback: AIVisualImageProvider | None = None) -> None:
        self._primary = primary
        self._fallback = fallback

    async def generate_image(self, prompt: str, **kwargs) -> ImageRef:
        try:
            return await self._primary.generate_image(prompt, **kwargs)
        except _FALLBACK_EXC as exc:
            if self._fallback is None:
                raise
            logger.warning("AI image primary failed (%s), trying fallback", exc)
            return await self._fallback.generate_image(prompt, **kwargs)


class FallbackAIVideoProvider:
    """Wraps a primary AI video provider with an optional fallback."""

    def __init__(self, primary: AIVisualVideoProvider, fallback: AIVisualVideoProvider | None = None) -> None:
        self._primary = primary
        self._fallback = fallback
        # QP1-3: fallback 不支持多参考图而降级的标记——ai_video.render() 读后写 sidecar，
        # 让"角色一致性降级"对用户可见（修前静默归零，连 warning 都不打）。
        self.degraded_without_refs = False

    @property
    def supports_image_urls(self) -> bool:
        """R3: 取 primary 的能力（primary 失败才走 fallback；能力以 primary 为准）。"""
        return getattr(self._primary, "supports_image_urls", False)

    @property
    def max_video_duration(self) -> float:
        """P0-2: 能力以 primary 为准（与 supports_image_urls 同原则）。"""
        return getattr(self._primary, "max_video_duration", 8.0)

    @property
    def min_video_duration(self) -> float:
        """Task 3: 能力以 primary 为准（与 supports_image_urls/max_video_duration 同原则）。

        2026-08-03 修 BUG-B：漏透传导致生产链上栅格对齐失效，详见
        ``ThrottledAIVideoProvider.min_video_duration``。
        """
        return float(getattr(self._primary, "min_video_duration", 0.0) or 0.0)

    @property
    def integer_seconds_only(self) -> bool:
        """Task 3: 能力以 primary 为准（同上 BUG-B）。"""
        return bool(getattr(self._primary, "integer_seconds_only", False))

    @property
    def supports_tail_frame(self) -> bool:
        """Phase 2: 能力以 primary 为准（同上 BUG-B）。"""
        return bool(getattr(self._primary, "supports_tail_frame", False))

    @property
    def multi_image_truncated(self) -> bool:
        """审查偏离项 3（2026-08-19）：primary 或 fallback 任一截断即 True。"""
        return bool(getattr(self._primary, "multi_image_truncated", False) or getattr(self._fallback, "multi_image_truncated", False))

    def reset_multi_image_truncated(self) -> None:
        """转发清零到两侧子 provider（ai_video render 开始调用）。"""
        for child in (self._primary, self._fallback):
            if child is None:
                continue
            reset = getattr(child, "reset_multi_image_truncated", None)
            if callable(reset):
                reset()

    async def generate_video(self, prompt: str, *, duration: float = 30.0, **kwargs) -> VideoRef:
        try:
            return await self._primary.generate_video(prompt, duration=duration, **kwargs)
        except _FALLBACK_EXC as exc:
            if self._fallback is None:
                raise
            logger.warning("AI video primary failed (%s), trying fallback", exc)
            fb_kwargs = dict(kwargs)
            image_urls = fb_kwargs.pop("image_urls", None)
            if image_urls:
                if getattr(self._fallback, "supports_image_urls", False):
                    fb_kwargs["image_urls"] = image_urls
                else:
                    # QP1-3: 降级端不支持多图——至少传首图（front 视图）保基本一致性，
                    # 并打标让用户可见（修前 **kwargs 静默吞）。
                    # 注（T3，2026-08-18）：修前注释「seedance 只读单数 image_url」已过时
                    # ——seedance 实际消费复数 image_urls（r2v 多参考），T3 起四个在册
                    # video provider 均已声明 supports_image_urls=True，本分支是给未来
                    # 未声明该能力的 provider 留的防御降级。
                    # 终审 Important-1: image_urls 是角色三视图本地绝对路径
                    # （character_sheet_generator 产物），远端 360 API 抓不到本地路径——
                    # 先归一成 data-URI（与 seedream._to_data_uri / seedance_ark._normalize_image_url 同规；
                    # URL/data-URI/不存在路径原样透传）。
                    from .seedream import _to_data_uri

                    fb_kwargs.setdefault("image_url", _to_data_uri(image_urls[0]))
                    self.degraded_without_refs = True
                    logger.warning("fallback video provider lacks multi-image support — passing only first reference image; character consistency degraded")
            # 降级时按 fallback 自身能力钳制时长——primary 的 15s 上限对 seedance(~8s) 不适用，
            # 未钳制会被 360 拒收白烧一次降级（评审 Important-1）。
            duration = min(duration, getattr(self._fallback, "max_video_duration", 8.0))
            return await self._fallback.generate_video(prompt, duration=duration, **fb_kwargs)


# 下拉里显示的模型名（Task 6）。
# E5（2026-08-21 360 主力切换）起，能力/风险标注经 ``_PROVIDER_ANNOTATIONS``
# 元数据下发（决策 7/D5：实测结论 / 「未检测」，不推断）——取代 2026-08-01
# 「只给模型名不展示限制文案」的旧决策（旧决策针对的是当时的推断性文案）。
_IMAGE_PROVIDER_LABELS: dict[str, str] = {
    "seedream": "Seedream 5.0 Pro（字节直连）",
    "seedream-360": "Seedream 5.0 Pro（360 通道）",
    "gpt-image-2": "GPT Image 2",
    "dashscope": "通义万相",
    # 2026-09-23 批次（计划 2026-09-23 §3）：定位档位写进 label，能力/风险走标注。
    "gpt-image-2.5-flare": "GPT Image 2.5 Flare（快速）",
    "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst（精度）",
}

_VIDEO_PROVIDER_LABELS: dict[str, str] = {
    "seedance-ark": "Seedance 2.0（字节直连）",
    "seedance": "Seedance 2.0（360 通道）",
    # E2（2026-08-21）：fast 用户可选项。能力/风险标注走 E5 元数据机制
    # （「仅 720p/低成本」），不进 label——label 只给模型名。
    "seedance-fast": "Seedance 2.0 Fast（360 通道）",
    "minimax-h3": "MiniMax-H3",
    # 2026-08-03 Task 7：删除 "kling": "可灵" —— 不再宣告，模型下拉里不该出现纯桩。
    # 2026-08-28 拍板：seedance-mini / happyhorse 同步摘除（实现保留，见 KNOWN 注释）。
}

# E5（2026-08-21）：选择器能力/风险标注元数据（静态表，随 available_* 下发）。
# 口径（决策 D5）：**实测过的标实测结论，未实测的一律标「未检测」，不做推断性
# 标注**。实测依据 = 姊妹篇 T1~T8 探针（temp/v1_probe）+ 历史探针存档；
# notes 只写官方文档口径的事实（如分辨率上限），不写主观优劣。
_PROVIDER_ANNOTATIONS: dict[str, dict] = {
    # ── 生视频 ──
    "seedance": {
        "reference_images": True,
        "tested": "已实测（2026-08-21：10 路并发全成零限流；比例/水印/关音轨生效）",
        "notes": "主力通道",
    },
    "seedance-fast": {
        "reference_images": True,
        "tested": "未检测",
        "notes": "仅 480p/720p，低成本",
    },
    "seedance-ark": {
        "reference_images": True,
        "tested": "已实测（Ark 直连探针；账号级 3 并发）",
        "notes": "360 故障时的降级兜底",
    },
    "minimax-h3": {
        "reference_images": True,
        "tested": "已实测（时长 4~15s 整数秒栅格）",
        "notes": "",
    },
    # ── 生图 ──
    "seedream": {
        "reference_images": True,
        "tested": "已实测（参考图编辑，Ark 直连）",
        "notes": "",
    },
    "seedream-360": {
        "reference_images": True,
        "tested": "已实测（单图编辑/多图融合通过）",
        "notes": "",
    },
    "gpt-image-2": {
        "reference_images": True,
        "tested": "已实测（文生图/参考图编辑，360 通道）",
        "notes": "",
    },
    "dashscope": {
        "reference_images": False,
        "tested": "未检测",
        "notes": "纯文生图，不支持参考图",
    },
    # 2026-09-23 批次（spike：仓库根 temp/360_spike_0923*.py 四轮探针）：
    # 文生图可用但 quality 必填（flare=low / sunburst=high 由构造分支配置）；
    # /v1/images/edits 参考图**全变体 400**（data URL/公网 URL/单数字段/multipart）
    # → reference_images=False，不进 I2I 白名单（计划 §3.2.3 前提被证伪，附录留档）。
    "gpt-image-2.5-flare": {
        "reference_images": False,
        "tested": "已实测（2026-09-23：文生图通过；参考图 edits 360 侧 400 不可用）",
        "notes": "尺寸 1:1/16:9/3:2 档实测通过，5:4 槽位尺寸被拒需就近映射；FullVisualPage 1920x1080 贴档至 1280x720 为降采样（360 侧白名单上限），全幅版式画质敏感场景知悉",
    },
    "gpt-image-2.5-sunburst": {
        "reference_images": False,
        "tested": "已实测（2026-09-23：文生图通过；参考图 edits 360 侧 400 不可用）",
        "notes": "精度档较慢（1024 图实测 46~73s，高峰可超 300s 读超时），批量任务注意；尺寸同 flare；FullVisualPage 1920x1080 贴档至 1280x720 为降采样（360 侧白名单上限），全幅版式画质敏感场景知悉",
    },
}

# 未登记名字的保守默认（D5：不推断——「未检测」而非乐观/悲观臆断）。
_DEFAULT_ANNOTATION: dict = {"reference_images": False, "tested": "未检测", "notes": ""}


# ── Registry ─────────────────────────────────────────────────────────────


class ProviderRegistry:
    """Holds configured provider instances for AI image, AI video."""

    def __init__(self, config: VideoGenConfig) -> None:
        self._closables: list[Any] = []
        # Task 6: 留存 config 供「按名取用」（make_ai_image/make_ai_video）使用。
        self._config = config
        self.ai_image: AIVisualImageProvider = self._build_ai_image(config)
        self.ai_video: AIVisualVideoProvider = self._build_ai_video(config)

        # Warn about unconfigured providers at startup
        if isinstance(self.ai_image, _StubAIImageProvider):
            logger.warning("AI image provider not configured — ai_image video type will fail. Set qihu_api_key or ark_api_key in config.yaml video_gen section.")
        if isinstance(self.ai_video, _StubAIVideoProvider):
            logger.warning("AI video provider not configured — ai_video video type will fail. Set qihu_api_key or ark_api_key in config.yaml video_gen section.")

    async def close(self) -> None:
        """Close any underlying HTTP clients / resources."""
        for provider in self._closables:
            close_fn = getattr(provider, "close", None)
            if close_fn and callable(close_fn):
                try:
                    result = close_fn()
                    if result is not None and hasattr(result, "__await__"):
                        await result
                except Exception:
                    logger.warning("Error closing provider %s", type(provider).__name__, exc_info=True)

    def _build_ai_image(self, config: VideoGenConfig) -> AIVisualImageProvider:
        from .throttle import ThrottledAIImageProvider

        primary = self._try_make_ai_image_provider(config, config.ai_image_provider)
        if primary is None:
            # Usability: ByteDance seedream selected but ark_api_key empty ->
            # degrade to gpt-image-2 (360 降级备用), NOT stub/NotImplementedError.
            # Only for seedream (gpt-image-2 IS the fallback; 360-gateway providers
            # have no graceful path).
            #
            if config.ai_image_provider == "seedream" and config.qihu_api_key:
                logger.warning("ai_image_provider='seedream' selected but ark_api_key empty -> falling back to gpt-image-2 (360). Set ark_api_key to enable ByteDance.")
                primary = self._try_make_ai_image_provider(config, "gpt-image-2")
            if primary is None:
                logger.warning("AI image provider '%s' not configured or no API key; using stub", config.ai_image_provider)
                return _StubAIImageProvider()

        throttled_primary = ThrottledAIImageProvider(primary, sink_label=config.ai_image_provider, **_DEFAULT_THROTTLE["ai_image"])
        # Stage 4.1: assemble FallbackAIImageProvider ONLY when the ByteDance Ark
        # seedream primary is actually active (ark_api_key set). A 360 gpt-image-2
        # fallback is meaningful on infra errors (5xx/429/timeout/conn) because
        # seedream is a DIFFERENT gateway. 4xx content rejections stay RuntimeError
        # (not in _FALLBACK_EXC) -> no fallback. When ark_api_key is empty, primary
        # already degraded to gpt-image-2 above (no double-fallback).
        fallback = None
        if config.ai_image_provider == "seedream" and config.ark_api_key:
            fallback = self._try_make_ai_image_provider(config, "gpt-image-2")
        if fallback is not None:
            throttled_fallback = ThrottledAIImageProvider(fallback, sink_label="gpt-image-2", **_DEFAULT_THROTTLE["ai_image"])
            return FallbackAIImageProvider(throttled_primary, throttled_fallback)
        return throttled_primary

    def _build_ai_video(self, config: VideoGenConfig) -> AIVisualVideoProvider:
        from .throttle import ThrottledAIVideoProvider

        primary = self._try_make_ai_video_provider(config, config.ai_video_provider)
        if primary is None:
            # Usability: seedance-ark selected but ark_api_key empty -> degrade
            # to seedance (360), NOT stub. Consistent with TTS + image-side graceful degrade.
            #
            if config.ai_video_provider == "seedance-ark" and config.qihu_api_key:
                logger.warning("ai_video_provider='seedance-ark' selected but ark_api_key empty -> falling back to seedance (360). Set ark_api_key to enable ByteDance.")
                primary = self._try_make_ai_video_provider(config, "seedance")
            if primary is None:
                logger.warning("AI video provider '%s' not configured or no API key; using stub", config.ai_video_provider)
                return _StubAIVideoProvider()

        throttled_primary = ThrottledAIVideoProvider(primary, sink_label=config.ai_video_provider, **_DEFAULT_THROTTLE["ai_video"])

        # E3（2026-08-21 360 主力切换）——主备方向对调：360（seedance，公示
        # 10,000 RPM）为主力，Ark 保留为降级兜底（账号级 3 并发为最终方案，
        # 不升级企业认证）。primary=seedance 系且 ark_api_key 存在时 fallback 挂
        # seedance-ark；触发沿用 _FALLBACK_EXC（429/5xx/超时/传输错误），
        # 4xx 内容拒绝（RuntimeError 族）不降级（requirement #4: surfaces to user）。
        # 修前方向相反（ark 主、360 备）——Ark 3 并发撑不起 20 人团队。
        # primary=seedance-ark 时不再挂 360 fallback（方向已反转：360 故障
        # 演练中 Ark 是被降落方，不应再往 360 回灌）。
        fallback = None
        provider_l = config.ai_video_provider.lower()
        if provider_l in ("seedance", "seedance-fast") and config.ark_api_key:
            fallback = self._try_make_ai_video_provider(config, "seedance-ark")
        if fallback is not None:
            throttled_fallback = ThrottledAIVideoProvider(fallback, sink_label="seedance-ark", **_DEFAULT_THROTTLE["ai_video"])
            return FallbackAIVideoProvider(throttled_primary, throttled_fallback)

        return throttled_primary

    # ── 按名取用（Task 6，2026-08-01：任务级生成模型选择） ─────────────────

    # 前端下拉可选的 provider 名。与 frontend/src/core/video-gen/provider-options.ts 对应。
    # 「可用」还要看 key 是否配置（各 _try_make_* 分支均 `and config.xxx_api_key` 短路），
    # 由 GET /api/video/providers 的 available 字段体现。
    KNOWN_IMAGE_PROVIDERS: tuple[str, ...] = (
        "seedream",
        "seedream-360",
        "gpt-image-2",
        "dashscope",
        # 2026-09-23 批次（计划 2026-09-23 §3）：复用 GptImage2Provider 实现，
        # 换模型 id + quality 必填档位；参考图编辑不可用（见 _PROVIDER_ANNOTATIONS）。
        "gpt-image-2.5-flare",
        "gpt-image-2.5-sunburst",
    )
    # 2026-08-03 Task 7：摘除 kling（providers/kling.py 是纯桩，raise NotImplementedError）。
    # 修前它配了 key 就报 available=True，用户一用即崩，且其 fallback 永不触发
    # （NotImplementedError 不在 _FALLBACK_EXC）。真正接入时：先写实现 + 单测，再加回本名单。
    # 注：minimax-h3 **保留**——它有真实现（复用 QihuVideoProvider + 实测模型 id
    # minimax/MiniMax-H3，2026-08-01 接入），与 kling 不同类。
    KNOWN_VIDEO_PROVIDERS: tuple[str, ...] = (
        "seedance-ark",
        "seedance",
        # E2（2026-08-21 360 主力切换）：fast 用户可选项（低成本）。
        # 2026-08-28 拍板：seedance-mini 与 happyhorse 从任务级下拉摘除
        # （用户口径「Mini 和 Happyhorse 不出现」）——provider 实现保留：
        # happyhorse 是 vimax 桥的 fallback backend（CLAUDE.md Stage 5.2 /
        # packages/vimax 依赖），具名调用 _assert_known 会拒但 vimax 内部直连
        # 不受影响；seedance-mini 为 E2 纯用户选项无其他依赖。
        "seedance-fast",
        "minimax-h3",
    )

    def make_ai_image(self, name: str | None) -> AIVisualImageProvider:
        """Build an image provider by name; ``None`` → the config-built default.

        用户决策 4：**显式指定则不降级** —— 具名产物只包 Throttle，不挂 Fallback。
        选了 A 却静默出 B 的图比失败更糟；失败要把真实原因回传前端。
        `None` 时返回启动时构建的实例（含既有降级链），行为与改动前完全一致。
        """
        if name is None:
            return self.ai_image
        from .throttle import ThrottledAIImageProvider

        self._assert_known(name, self.KNOWN_IMAGE_PROVIDERS, "生图")
        inner = self._try_make_ai_image_provider(self._config, name)
        if inner is None:
            raise ValueError(f"生图模型 '{name}' 未配置 API key，无法使用（请改用其他模型或在 config.yaml 补齐密钥）")
        return ThrottledAIImageProvider(inner, sink_label=name, **_DEFAULT_THROTTLE["ai_image"])

    def make_ai_video(self, name: str | None) -> AIVisualVideoProvider:
        """Build a video provider by name; ``None`` → the config-built default.

        同 :meth:`make_ai_image`：显式指定不降级。
        """
        if name is None:
            return self.ai_video
        from .throttle import ThrottledAIVideoProvider

        self._assert_known(name, self.KNOWN_VIDEO_PROVIDERS, "生视频")
        inner = self._try_make_ai_video_provider(self._config, name)
        if inner is None:
            raise ValueError(f"生视频模型 '{name}' 未配置 API key，无法使用（请改用其他模型或在 config.yaml 补齐密钥）")
        return ThrottledAIVideoProvider(inner, sink_label=name, **_DEFAULT_THROTTLE["ai_video"])

    @staticmethod
    def _assert_known(name: str, known: tuple[str, ...], kind: str) -> None:
        if name.lower() not in known:
            raise ValueError(f"未知的{kind}模型 '{name}'；可选：{', '.join(known)}")

    # provider → 其所需的 config key 属性名（Task 6）。
    #
    # ⚠️ **必须与 `_try_make_ai_*_provider` 的各分支条件保持一致** ——
    # 那些分支形如 `if name == "x" and config.<key>`，这里登记的就是那个 `<key>`。
    # 新增 provider 时两处都要改；`test_availability_key_map_matches_actual_factory_branches`
    # 会对 7 种 key 组合交叉验证两者结论一致，漏改即转红。
    _IMAGE_PROVIDER_KEY: dict[str, str] = {
        "seedream": "ark_api_key",
        "seedream-360": "qihu_api_key",
        "gpt-image-2": "qihu_api_key",
        "dashscope": "dashscope_api_key",
        # 2026-09-23 批次：与 gpt-image-2 同网关同凭据。
        "gpt-image-2.5-flare": "qihu_api_key",
        "gpt-image-2.5-sunburst": "qihu_api_key",
    }
    _VIDEO_PROVIDER_KEY: dict[str, str] = {
        "seedance-ark": "ark_api_key",
        "seedance": "qihu_api_key",
        "seedance-fast": "qihu_api_key",
        "minimax-h3": "qihu_api_key",
        # 2026-08-28：seedance-mini/happyhorse 摘除（实现保留）——vimax 桥
        # 内部直连 happyhorse 不走本表，摘除不影响其 fallback。
    }

    def available_image_providers(self) -> list[dict]:
        """列出生图模型及其可用性（供 GET /api/video/providers）。"""
        return self._availability(
            self.KNOWN_IMAGE_PROVIDERS,
            self._IMAGE_PROVIDER_KEY,
            self._config.ai_image_provider,
            _IMAGE_PROVIDER_LABELS,
        )

    def available_video_providers(self) -> list[dict]:
        """列出生视频模型及其可用性（供 GET /api/video/providers）。

        Task 13（2026-08-03）：每项附 ``capability`` 能力元数据，前端可按能力渲染/校验，
        无需为每个新模型改前端代码。查**静态表**，不构造 provider（防句柄泄漏回潮，
        见 ``_availability`` docstring）。只加在 video 侧——image 侧能力模型不同
        （无时长概念），留待需要时另做（YAGNI）。
        """
        out = self._availability(
            self.KNOWN_VIDEO_PROVIDERS,
            self._VIDEO_PROVIDER_KEY,
            self._config.ai_video_provider,
            _VIDEO_PROVIDER_LABELS,
        )
        for item in out:
            item["capability"] = asdict(video_capability(item["name"]))
        return out

    def _availability(self, known: tuple[str, ...], key_map: dict[str, str], default_name: str, labels: dict[str, str]) -> list[dict]:
        """可用性 = 所需 key 是否已配置。**只读 config，不构建 provider 实例。**

        初版用「能否构建」判定（`factory(...) is not None`），每次调用都会 new 出
        一批带 httpx client 的实例并登记进 `_closables` 且永不释放 —— 实测调 3 次
        `_closables` 从 2 涨到 29。本端点每次打开创建对话框都会被调用，会一直累积
        到进程重启。查表判定无副作用，也更快（零对象分配、零 import）。
        """
        out: list[dict] = []
        for name in known:
            key_attr = key_map.get(name)
            available = bool(key_attr and getattr(self._config, key_attr, ""))
            out.append(
                {
                    "name": name,
                    "label": labels.get(name, name),
                    "available": available,
                    "is_default": name == (default_name or "").lower(),
                    # E5（2026-08-21）：能力/风险标注元数据（D5 口径：实测结论/未检测，
                    # 不推断）。缺登记名回落保守默认，不允许 KeyError/静默丢项。
                    "annotations": dict(_PROVIDER_ANNOTATIONS.get(name, _DEFAULT_ANNOTATION)),
                }
            )
        return out

    # ── Provider factories ──────────────────────────────────────────────

    def _try_make_ai_image_provider(self, config: VideoGenConfig, name: str) -> AIVisualImageProvider | None:
        name = name.lower()
        if name == "seedream" and config.ark_api_key:
            # ByteDance Ark seedream (doubao-seedream-5-0-pro-260628) — 字节默认首选.
            # Ark 直连 /images/generations, Bearer $ARK_API_KEY. See providers/seedream.py.
            from .seedream import SeedreamImageProvider

            inner = SeedreamImageProvider(
                api_key=config.ark_api_key,
                base_url=config.ark_base_url,
                model=config.seedream_image_model,
            )
            self._closables.append(inner)
            return inner
        if name == "dashscope" and config.dashscope_api_key:
            from .dashscope import DashScopeImageProvider

            inner = DashScopeImageProvider(api_key=config.dashscope_api_key)
            self._closables.append(inner)
            return inner
        if name == "gpt-image-2" and config.qihu_api_key:
            # gpt-image-2 lives on the same 360 gateway as qihu → reuse credentials.
            # Supports reference-image editing (QihuImageProvider is text-only).
            # NOTE (0.0 spike): gpt-image-2 reference editing total identity failure
            # (different genders across frames). Kept for text-to-image only.
            # nano-banana branch removed 2026-08-11; ViMax bridge imports
            # NanoBananaImageProvider directly (not via registry).
            from .gpt_image2 import GptImage2Provider

            inner = GptImage2Provider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
            )
            self._closables.append(inner)
            return inner
        if name == "gpt-image-2.5-flare" and config.qihu_api_key:
            # 2026-09-23 批次：复用 GptImage2Provider，仅换模型 id + quality
            # （spike：2.5 系 quality 必填，缺省 400）。D3 修复批定稿：flare 与
            # sunburst 均 quality="high"——两档差异由模型变体本身承担（实测
            # flare high 22.1s vs sunburst ~30s，「快速/精度」定位仍成立），
            # low 档实测反而更慢且为最低质量，不采用。
            # 参考图编辑不可用 → 不进 I2I 白名单（见 _PROVIDER_ANNOTATIONS）。
            from .gpt_image2 import GptImage2Provider

            inner = GptImage2Provider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
                model="openai/gpt-image-2.5-flare",
                quality="high",
            )
            self._closables.append(inner)
            return inner
        if name == "gpt-image-2.5-sunburst" and config.qihu_api_key:
            # 同 flare（D3：quality=high）；sunburst=精度档（官方口径密集排版/信息图保真）。
            from .gpt_image2 import GptImage2Provider

            inner = GptImage2Provider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
                model="openai/gpt-image-2.5-sunburst",
                quality="high",
            )
            self._closables.append(inner)
            return inner
        if name == "seedream-360" and config.qihu_api_key:
            # Task 6 (2026-08-01): doubao-seedream-5.0-pro via the 360 gateway.
            # 复用 Qihu 协议（同网关同凭据），**不是** Ark 直连的 SeedreamImageProvider ——
            # 那个走 ark_base_url + ARK_API_KEY + image:[list] 参考图字段，协议不同。
            # 模型 id 已实测（GET /v1/models，214 models）：bytedance/doubao-seedream-5.0-pro
            from .qihu_image import QihuImageProvider

            inner = QihuImageProvider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
                model=config.seedream_360_image_model,
            )
            self._closables.append(inner)
            return inner
        return None

    def _try_make_ai_video_provider(self, config: VideoGenConfig, name: str) -> AIVisualVideoProvider | None:
        name = name.lower()

        def _make_seedance(model: str) -> AIVisualVideoProvider:
            from .seedance import SeedanceVideoProvider

            inner = SeedanceVideoProvider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
                # E2（2026-08-21）：模型名/分辨率接配置——修前用类默认，
                # config.seedance_video_model 是死旋钮（改了不生效）。
                model=model,
                resolution=config.seedance_video_resolution,
            )
            self._closables.append(inner)
            return inner

        if name == "seedance" and config.qihu_api_key:
            # seedance lives on the 360 gateway → reuse qihu creds.
            # r2v (multi-reference) video; selected via ai_video_provider or the
            # vimax bridge backend (C3). See providers/seedance.py.
            return _make_seedance(config.seedance_video_model)
        if name == "seedance-fast" and config.qihu_api_key:
            # E2（2026-08-21）：fast 型号选项（低成本；官方口径仅 480p/720p，
            # 标注走 E5 元数据）。模型 id 探针实测在册。
            return _make_seedance(config.seedance_fast_video_model)
        if name == "seedance-mini" and config.qihu_api_key:
            # E2（2026-08-21）：mini 型号选项（同上）。
            return _make_seedance(config.seedance_mini_video_model)
        if name == "seedance-ark" and config.ark_api_key:
            # ByteDance Ark seedance 2.0 direct (doubao-seedance-2-0-260128).
            # Ark /contents/generations/tasks (submit+poll), Bearer $ARK_API_KEY, generate_audio=false.
            # Distinct from the 360-gateway seedance above. See providers/seedance_ark.py (Stage 5.1).
            # E3（2026-08-21）：降级兜底位——提交路径过在途闸（ark_video_max_inflight，
            # Ark 个人档 3 并发）。
            from .seedance_ark import SeedanceArkVideoProvider

            inner = SeedanceArkVideoProvider(
                api_key=config.ark_api_key,
                base_url=config.ark_base_url,
                model=config.seedance_ark_video_model,
                max_inflight=config.ark_video_max_inflight,
            )
            self._closables.append(inner)
            return inner
        if name == "minimax-h3" and config.qihu_api_key:
            # Task 6 (2026-08-01): MiniMax-H3 via the 360 gateway. qihu(Hailuo 2.3)
            # 已于 2026-08-11 下架——H3 复用同一 QihuVideoProvider 协议，仅模型 id 不同。
            # 模型 id 已实测（GET /v1/models）：minimax/MiniMax-H3（大小写敏感）。
            from .qihu_video import QihuVideoProvider

            inner = QihuVideoProvider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
                model=config.minimax_h3_video_model,
            )
            self._closables.append(inner)
            return inner
        if name == "happyhorse" and config.qihu_api_key:
            # alibaba happyhorse — realistic-person i2v (不拦真人，区别于 seedance).
            # Reuses the 360 gateway + qihu creds. Selected via ai_video_provider or
            # the vimax bridge auto-routing (写实→happyhorse). See providers/happyhorse.py.
            from .happyhorse import HappyhorseVideoProvider

            inner = HappyhorseVideoProvider(
                api_key=config.qihu_api_key,
                base_url=config.qihu_base_url,
            )
            self._closables.append(inner)
            return inner
        # 2026-08-03 Task 7：删除 kling 构造分支（纯桩，见 KNOWN_VIDEO_PROVIDERS 注释）。
        # providers/kling.py 保留作未来接入骨架，但不再接线。
        return None


class _StubAIImageProvider:
    async def generate_image(self, prompt: str, **kwargs):
        raise NotImplementedError("AI image provider not configured. Set ai_image_provider and API key in config.yaml")


class _StubAIVideoProvider:
    async def generate_video(self, prompt: str, *, duration: float = 30.0, **kwargs):
        raise NotImplementedError("AI video provider not configured. Set ai_video_provider and API key in config.yaml")


def build_provider_registry(config: VideoGenConfig) -> ProviderRegistry:
    return ProviderRegistry(config)
