"""品牌包单一事实源（T4/M3，2026-09-08 remotion-course 计划）。

CourseProject → Remotion brand props：
- 有 course_project_id → 读 CourseProject 组装 brand（缺字段落默认，不炸）；
- 无 → 系统默认品牌包（D12：深空蓝 dark-blue + 默认字幕样式 + 默认音色）。

A 类字段（代码层注入模板）在此消费：remotion_theme/font_family/logo_path/
intro_outro/color_palette.primary/subtitle_style；
B 类字段（生成层：voice_id/style_prefix/三视图/锚图）不在此——素材生成阶段已消费。
"""

from __future__ import annotations

import logging
from pathlib import Path

from .course_models import CourseProject

logger = logging.getLogger(__name__)

DEFAULT_THEME = "dark-blue"  # D12 平台默认主题
VALID_THEMES = ("dark-blue", "light", "dark-green", "warm")
# T-A3（2026-09-09 feedback-round1）：片头/片尾默认帧数的单一事实源——
# remotion_course_props 与本模块共用（消除双处 90 硬编码漂移）。3s @ 30fps。
INTRO_FRAMES_DEFAULT = 90
OUTRO_FRAMES_DEFAULT = 90


def _safe_frames(value, default: int) -> int:
    """T-G7（m3）：片头/片尾帧数安全解析——int(x or 90) 遇非数字字符串抛
    ValueError 会让整任务 failed（项目字段是自由文本入口）。非数字/负数落默认。"""
    try:
        n = int(value)
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


def _default_brand(title: str | None = None) -> dict:
    """系统默认品牌包（不挂课程项目的任务）。"""
    return {
        "themeName": DEFAULT_THEME,
        "brandName": title or "",
        "subtitleStyle": {"karaokeEnabled": True},  # D6 默认开
    }


def resolve_brand(
    project: CourseProject | None,
    *,
    title: str | None = None,
) -> dict:
    """组装 brand props。project=None 或字段缺失一律安全降级到默认值。"""
    if project is None:
        return _default_brand(title)

    # 品牌名优先项目标题（挂了项目 → 项目即品牌主体）
    brand = _default_brand(project.title or title)

    # remotion_theme（T8 字段；存量项目无此字段 → 默认）
    theme = getattr(project, "remotion_theme", None)
    if theme in VALID_THEMES:
        brand["themeName"] = theme
    elif theme:
        logger.warning("course project %s has invalid remotion_theme %r; fallback %s", project.id, theme, DEFAULT_THEME)

    # color_palette.primary（hex）→ 覆写主题主色；saturation/contrast/brightness
    # 是 ffmpeg 调色参数，remotion 不消费（保持既有口径，不双写）
    palette = getattr(project, "color_palette", None) or {}
    primary = palette.get("primary")
    if isinstance(primary, str) and primary.startswith("#"):
        brand["colors"] = {"primary": primary}

    # 字体（T8 字段）
    font_family = getattr(project, "font_family", None)
    if isinstance(font_family, str) and font_family.strip():
        brand["fontFamily"] = font_family.strip()

    # LOGO（T8 字段：项目资产目录下的相对路径或绝对路径，worker 侧本地化）
    logo_path = getattr(project, "logo_path", None)
    if isinstance(logo_path, str) and logo_path.strip():
        brand["logoPath"] = logo_path.strip()

    # 字幕样式（T-E9，2026-09-09 feedback-round1）：三级链「任务显式 > 项目 > 默认」
    # （与 voice_id 同构）。variant 是模板 SubtitleStyleSpec.variant
    # （default/bold/minimal/outline/none）；修前任务级只有 none 生效——bold/
    # minimal/outline 未映射模板（断链）。
    style = str(getattr(project, "subtitle_style", "default") or "default").strip()
    spec: dict = {"karaokeEnabled": style != "none", "variant": style if style in ("bold", "minimal", "outline", "none") else "default"}
    if style == "bold":
        spec["fontSize"] = 40
    brand["subtitleStyle"] = spec

    # 片头/片尾（T8 字段 intro_outro dict）
    intro_outro = getattr(project, "intro_outro", None)
    if isinstance(intro_outro, dict):
        intro = intro_outro.get("intro")
        if isinstance(intro, dict) and (intro.get("headline") or intro.get("subtitle")):
            brand["intro"] = {
                "headline": str(intro.get("headline") or title or ""),
                "subtitle": str(intro.get("subtitle") or ""),
                "durationInFrames": _safe_frames(intro.get("durationInFrames"), INTRO_FRAMES_DEFAULT),
            }
        outro = intro_outro.get("outro")
        if isinstance(outro, dict) and (outro.get("headline") or outro.get("follow")):
            brand["outro"] = {
                "headline": str(outro.get("headline") or "感谢观看"),
                "follow": str(outro.get("follow") or ""),
                "durationInFrames": _safe_frames(outro.get("durationInFrames"), OUTRO_FRAMES_DEFAULT),
            }

    return brand


def load_project_for_task(
    storage_dir: str | Path | None,
    course_project_id: str | None,
    user_id: str,
) -> CourseProject | None:
    """按任务 payload 的 course_project_id 加载项目；缺省/查无返回 None。"""
    if not course_project_id:
        return None
    try:
        from .course_project_store import CourseProjectStore

        store = CourseProjectStore(base_dir=storage_dir or Path("backend/.deer-flow/video-gen"))
        return store.get(course_project_id, user_id)
    except Exception:
        logger.warning("failed to load course project %s for brand pack; using default", course_project_id, exc_info=True)
        return None
