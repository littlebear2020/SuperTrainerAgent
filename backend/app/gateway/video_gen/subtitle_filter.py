"""B6c: ViMax 字幕滤镜构造（force_style + fontsdir 兜底）—— gateway 侧。

镜像 worker ``docker/production-worker/pipelines/_ffmpeg_helpers._subtitle_filter_vf`` /
``_subtitle_force_style`` / ``_escape_subtitle_filter_path``（跨镜像无法 import——worker 与
gateway 是不同包/镜像，gateway 侧重写一份）。ViMax ``overlay_audio_track`` 用它构造烧录滤镜。
保持两处 ``_SUBTITLE_STYLE_MAP`` + fontsdir 逻辑同步。
"""

from pathlib import Path

_SUBTITLE_STYLE_MAP: dict[str, str] = {
    "default": "FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=30",
    "bold": "FontSize=32,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,BackColour=&H80000000,BorderStyle=4,Alignment=2,MarginV=40",
    "minimal": "FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1,Alignment=2,MarginV=20",
    "outline": "FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=4,Alignment=2,MarginV=35",
}

_FONTSDIR_CANDIDATES = (
    "/usr/share/fonts",
    "/System/Library/Fonts",
    "/Library/Fonts",
    "/usr/local/share/fonts",
)


def should_burn_subtitles(*, global_burn: bool, subtitle_style: str | None) -> bool:
    """ViMax 烧录决策：per-task「无字幕」意图覆盖全局开关。

    2026-07-29：修前只看全局 ``vimax_burn_subtitles``（默认 True），per-task
    ``subtitle_style`` 仅用于选样式串，而 ``_SUBTITLE_STYLE_MAP`` 无 ``"none"`` 键
    → 回落 default → 用户选「无字幕」照样得到字幕。

    ``"none"`` 只增加一条否决权：全局关掉时任何样式都不烧，语义不变。
    """
    if subtitle_style == "none":
        return False
    return bool(global_burn)


def subtitle_force_style(subtitle_style: str = "default") -> str:
    """libass force_style 串（镜像 worker ``_subtitle_force_style``）。

    ``"none"`` 返回空串：它表示「不要字幕」，静默回落 default 会让
    「选了无字幕却仍有默认样式字幕」这种反向失效无从察觉（2026-07-29）。
    未知值仍回落 default——只有显式的 "none" 才代表关闭。
    """
    if subtitle_style == "none":
        return ""
    return _SUBTITLE_STYLE_MAP.get(subtitle_style, _SUBTITLE_STYLE_MAP["default"])


def _escape_filter_path(path: str) -> str:
    """转义 subtitles= 滤镜里的路径：单引号包裹 + 转义内嵌单引号（镜像 worker）。"""
    return "'" + path.replace("'", "'\\''") + "'"


def subtitle_filter_vf(
    srt: Path,
    subtitle_style: str = "default",
    fontsdir: str | None = None,
) -> str:
    """构造 ``subtitles=`` 滤镜串（force_style + fontsdir 兜底）。

    T6/T7（2026-08-13）：.ass 文件自带样式（PlayRes/WrapStyle/FontSize），不拼 force_style；
    .srt 保持旧路径（force_style）兼容。
    """
    parts = [f"subtitles={_escape_filter_path(str(srt))}"]
    is_ass = str(srt).lower().endswith(".ass")
    fd = fontsdir
    if fd is None:
        fd = next((d for d in _FONTSDIR_CANDIDATES if Path(d).exists()), None)
    if fd:
        parts.append(f"fontsdir={_escape_filter_path(fd)}")
    # ASS 自带样式，不覆盖 force_style；SRT 旧路径保留 force_style
    if not is_ass:
        fs = subtitle_force_style(subtitle_style)
        if fs:
            escaped_fs = fs.replace("'", "'\\''")
            parts.append(f"force_style='{escaped_fs}'")
    return ":".join(parts)
