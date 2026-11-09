"""Shared ffmpeg helper functions for Worker pipelines.

Consolidates _ffmpeg_err, _ffmpeg_concat, _ffmpeg_mix_audio that were
duplicated across the (now-removed) video_gen_stock_footage.py and
video_gen_ai_visual.py.
"""

from __future__ import annotations

import functools
import json
import logging
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Quality / format presets
# ---------------------------------------------------------------------------

QUALITY_PRESETS: dict[str, dict] = {
    "draft": {"crf": 28, "resolution": None, "preset": "ultrafast"},
    "standard": {"crf": 20, "resolution": None, "preset": "medium"},
    "high": {"crf": 16, "resolution": None, "preset": "slow"},
    "preview": {"crf": 35, "resolution": "640x360", "preset": "ultrafast"},
}


def _dynamic_concat_timeout(n_frames: int, has_transitions: bool, total_seconds: float | None = None) -> int:
    """Calculate dynamic concat timeout based on frame count.

    Long videos with transitions (xfade) produce very long filter_complex strings.

    2026-08-03 Task 18：新增 total_seconds。修前只按镜数算（300 + n×1），而真实耗时
    随**总时长**线性增长——54 镜 8 分钟片得到 354s 超时，实测重编码超时（真实事故
    vg_66978a4b06ba）。stream copy 生效后耗时骤降，但保留时长维度以防回落到重编码。
    不传 total_seconds 时保持旧公式，向后兼容。
    """
    base = 300
    per_frame = 3 if has_transitions else 1
    est = base + n_frames * per_frame
    if total_seconds and total_seconds > 0:
        # 重编码回落最坏情况按 ~1.5x 实时估（与 _polish_timeout 的 per_sec 同量级）
        est = max(est, base + int(total_seconds * 1.5))
    return min(est, 1800)


CONCAT_TARGET_FPS = 30  # G1: ai_video 各镜统一帧率，消除 concat 衔接黑场/卡顿

# Task 18：stream copy 产物时长与各段之和的可接受偏差。concat 拼接本身在容器时基上
# 有亚帧级取整（30fps 下单帧 0.033s），故留 0.2s；混帧率导致的真实压缩是秒级
# （实测 4.0s -> 3.2s），两者不会混淆。
# 评审 Minor #1（2026-08-14）：容差改非对称——短损 = 时长被静默压缩（不可信），
# 短侧下限收紧到 0.1s（约 3 帧）；长损下游 -t 截断无损，上限保持 0.2s。
_CONCAT_COPY_TOLERANCE_SEC = 0.2
_CONCAT_COPY_SHORT_TOLERANCE_SEC = 0.1


# ---------------------------------------------------------------------------
# Ken Burns (zoompan)
#
# 2026-08-03 Task 11：从 video_gen_ai_visual 搬来（**不是复制**）。三条管线都
# import 本模块，而 mixed/segmented 此前用的是只有 scale/crop/format 的静态版本
# ——docstring 却写着 "with Ken Burns effect"（文档说谎）。搬来后单一实现，
# 下面那些实测结论（4x 过采样、crop 路线已被 ffmpeg 8.1.2 证伪）也不再有分叉风险。
# ---------------------------------------------------------------------------

# Ken Burns 过采样倍数（方案甲 2026-07-20）：zoompan x/y 整数取整抖动 ±1px ÷ 4
# -> ≤0.25 输出像素（修前 2x -> 0.5px 仍肉眼可见）。crop 替换路线经 ffmpeg 8.1.2
# 实测证伪（crop 的 w/h 仅初始化求值一次，无法逐帧动画），故保留 zoompan 调参。
_ZOOM_OVERSAMPLE = 4


def _build_zoompan_vf(*, width: int, height: int, duration: float, frame_index: int = 0) -> str:
    """Task 3b/3i: Ken Burns zoompan vf，运镜速率按 duration 归一化 + 方向交替。

    旧版 zoom+0.0008 写死->12s 帧第125帧撞 1.1 上限后静止175帧（PPT 卡住）。
    rate=0.1/total_frames，每帧走完完整行程。
    Task 3i: 偶数帧 zoom in(1.0->1.1)，奇数帧 zoom out(1.1->1.0)，破除整片单调缓慢拉近。
    方案甲（2026-07-20）：过采样 2x->4x（_ZOOM_OVERSAMPLE，取整抖动 ≤0.25 输出像素）+
    fps 25->30（对齐 CONCAT_TARGET_FPS，消除 25->30 重采样跳帧）。
    """
    total_frames = max(int(duration * 30), 1)
    zoom_rate = round(0.1 / total_frames, 6)  # 归一化：总行程 0.1 均摊到每帧
    if frame_index % 2 == 0:
        z_expr = f"min(zoom+{zoom_rate},1.1)"  # 偶数帧 zoom in
    else:
        # 奇数帧 zoom out：首帧设 1.1 初值（zoompan 默认从 1.0 起），递减到 1.0
        z_expr = f"if(eq(on\\,0)\\,1.1\\,max(zoom-{zoom_rate}\\,1.0))"
    return (
        f"scale={width * _ZOOM_OVERSAMPLE}:{height * _ZOOM_OVERSAMPLE}:force_original_aspect_ratio=increase,"
        f"crop={width * _ZOOM_OVERSAMPLE}:{height * _ZOOM_OVERSAMPLE},"
        f"zoompan=z='{z_expr}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
        f"d={total_frames}:s={width}x{height}:fps=30"
    )


def _get_quality_args(quality: str = "standard") -> list[str]:
    """Return ffmpeg encoder args for a quality preset."""
    preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["standard"])
    args: list[str] = ["-c:v", "libx264", "-crf", str(preset["crf"])]
    if preset.get("preset"):
        args += ["-preset", preset["preset"]]
    if preset.get("resolution"):
        # NEW-2: 用 -s（输出尺寸）而非 -vf scale=。-vf 是「简单滤镜」，与调用方的
        # -filter_complex（xfade 转场）互斥 -> ffmpeg 报 "Simple and complex
        # filtering cannot be used together"，被 fallback 吞成「转场静默消失」；
        # 且会覆盖 _apply_intro_outro_fade 已发的 -vf fade（ffmpeg 只取最后一个
        # -vf）-> 淡入淡出静默丢弃（实测末帧亮度 127 vs 修后 8）。
        # -s 与 -vf / -filter_complex 均不冲突。
        args += ["-s", str(preset["resolution"])]
    args += ["-pix_fmt", "yuv420p"]
    args += ["-movflags", "+faststart"]  # Task 3f: moov 前置，Web 边下边播
    return args


def _escape_subtitle_filter_path(path: str) -> str:
    """Escape a file path for libavfilter's ``subtitles=`` option value.

    Single-quote wrapping protects ``[`` ``]`` ``,`` and space characters that
    the filtergraph parser treats as special. Embedded ''' is escaped as
    ``'\\\''`` (close, escaped, reopen).

    P0-6 降级 (2026-08-07): 空格实测无害，此前 docstring 声称「路径含空格
    会被截断」是夸大断言。``:`` 和 ``'`` 两版转义都不支持（实测 rc=234/254），
    但生产路径无这些字符。
    """
    return "'" + path.replace("'", "'\\''") + "'"


def _ffmpeg_err(stderr_bytes: bytes, prefix: str) -> str:
    """Extract useful error lines from ffmpeg stderr output."""
    raw = stderr_bytes.decode(errors="replace")
    err_lines = [
        line.strip()
        for line in raw.splitlines()
        if any(kw in line.lower() for kw in ("error", "invalid", "cannot", "no such", "not found", "failed"))
    ]
    if err_lines:
        return f"{prefix}: {'; '.join(err_lines)}"
    return f"{prefix}: ...{raw[-500:]}"


def _ffprobe_duration(path: Path) -> float:
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(probe.stdout.strip())


def _write_av_drift_sidecar(
    video_path, audio_path, task_dir, *, transition_count: int = 0, threshold: float = 0.5
) -> dict | None:
    """E4: 转场 xfade 使视频总时长 < 音频时长（每次转场重叠 td 秒），probe 实际时长写 av_drift.json。

    xfade 让相邻段重叠 ``td`` 秒 -> 视频总时长 = Σdur − Σtd；音频按帧 duration 无重叠拼接 =
    Σdur。启用转场后视频比音频短 Σtd（10 段 fade 0.5s ≈ 短 4.5s），``_ffmpeg_mix_audio`` 的
    ``-shortest`` 截掉音频尾 -> 后半段字幕/口型系统性提前。本函数只做**诚实化**：当 drift 超过
    threshold 时写 sidecar（不阻断、不修漂移），供 gateway 回传"本视频含 N 段转场，字幕可能有
    约 Xs 漂移"的提示。彻底消除需 gateway 侧把 Σtd 补偿进 SRT 时间轴（列后续）。

    transition_count=0（无转场）直接跳过——无转场时视频≈音频，drift≈0 不应误报。
    """
    if transition_count <= 0:
        return None
    if not audio_path or not Path(audio_path).exists():
        return None
    try:
        video_dur = _ffprobe_duration(Path(video_path))
        audio_dur = _ffprobe_duration(Path(audio_path))
    except (ValueError, subprocess.SubprocessError, OSError):
        return None
    drift = audio_dur - video_dur  # 转场重叠使视频短于音频
    if drift <= threshold:
        return None
    data = {
        "video_dur": round(video_dur, 3),
        "audio_dur": round(audio_dur, 3),
        "drift_sec": round(drift, 3),
        "transition_count": transition_count,
    }
    try:
        (Path(task_dir) / "av_drift.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return None
    return data


# ---------------------------------------------------------------------------
# Transition mapping
# ---------------------------------------------------------------------------

_TRANSITION_MAP = {
    "fade": "fade",
    "dissolve": "dissolve",
    "slide_left": "slideleft",
    "slide_right": "slideright",
    "wipe_left": "wipeleft",
    "wipe_right": "wiperight",
    "zoom_in": "zoomin",
}


def _ffmpeg_concat_with_transitions(
    segments: list[Path],
    output: Path,
    *,
    transition: str = "none",
    transition_duration: float = 0.5,
    transition_durations: list[float] | None = None,
    quality: str = "standard",
    timeout: int = 600,
) -> None:
    """Concatenate segments with xfade transitions between them.

    Falls back to simple concat if transition='none' or only 1 segment.
    When transition_durations is provided, uses per-transition duration
    (one entry per gap between segments, i.e. len(segments)-1 entries).
    """
    if len(segments) <= 1 or transition == "none":
        _ffmpeg_concat(segments, output, quality=quality, timeout=timeout)
        return

    xfade_type = _TRANSITION_MAP.get(transition, "fade")

    # Probe durations for all segments
    durations = [_ffprobe_duration(s) for s in segments]

    # Per-transition durations (or uniform fallback)
    n_transitions = len(segments) - 1
    if transition_durations and len(transition_durations) >= n_transitions:
        tds = transition_durations[:n_transitions]
    else:
        tds = [transition_duration] * n_transitions

    # Build ffmpeg filter_complex for chained xfade
    inputs_args: list[str] = []
    for s in segments:
        inputs_args += ["-i", str(s)]

    filter_parts: list[str] = []
    # First xfade: [0:v][1:v]xfade=...[v0]
    td0 = tds[0]
    offset = durations[0] - td0
    filter_parts.append(
        f"[0:v][1:v]xfade=transition={xfade_type}:duration={td0}:offset={offset}[v0]"
    )

    # Subsequent xfades: [v_{i-1}][{i+1}:v]xfade=...[v_{i}]
    cum_dur = durations[0] + durations[1]
    for i in range(2, len(segments)):
        td_i = tds[i - 1]
        offset = cum_dur - sum(tds[:i]) - td_i
        prev_label = f"v{i - 2}"
        out_label = f"v{i - 1}"
        filter_parts.append(
            f"[{prev_label}][{i}:v]xfade=transition={xfade_type}:duration={td_i}:offset={offset}[{out_label}]"
        )
        cum_dur += durations[i]

    final_label = f"v{len(segments) - 2}"
    filter_complex = ";".join(filter_parts)

    quality_args = _get_quality_args(quality)
    cmd = [
        "ffmpeg", "-y",
        *inputs_args,
        "-filter_complex", filter_complex,
        "-map", f"[{final_label}]",
        "-r", str(CONCAT_TARGET_FPS),  # G1: xfade 路径同样统一输出帧率（与 _ffmpeg_concat 对齐）
        *quality_args,
        "-an",  # no audio (will be added in mix step)
        str(output),
    ]

    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        logger.warning("xfade transition failed, falling back to simple concat: %s",
                       _ffmpeg_err(proc.stderr, "xfade"))
        _ffmpeg_concat(segments, output, quality=quality, timeout=timeout)


def _colorgrade_vf(color_palette: dict | None) -> str | None:
    """B: 构造 colorgrade 的 -vf 滤镜串。None=无操作（copy 即可）。

    删除破坏性 ``hue=h={primary色相角}``：饱和主色（#2C3E50->210°）会把全色整圈
    平移、肤色变蓝紫。灰度主色本就跳过 hue。现仅保留 ``eq=contrast/brightness/saturation``
    （安全、subtle）。未来低强度色调渲染可在此基础上加（见 spec §B 备选 B）。
    """
    if not color_palette:
        return None
    saturation = color_palette.get("saturation", 1.0)
    brightness = color_palette.get("brightness", 0.0)
    contrast = color_palette.get("contrast", 1.0)
    if contrast == 1.0 and brightness == 0.0 and saturation == 1.0:
        return None
    return f"eq=contrast={contrast}:brightness={brightness}:saturation={saturation}"


def _build_fade_vf(total_duration: float, fade_sec: float = 0.5) -> str:
    """片头淡入 + 片尾淡出 vf（Task 3g，从 ai_visual 提取为共享）。"""
    out_st = max(total_duration - fade_sec, 0)
    return f"fade=t=in:st=0:d={fade_sec},fade=t=out:st={out_st:.2f}:d={fade_sec}"


_SUBTITLE_STYLE_MAP: dict[str, str] = {
    "default": "FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2,Alignment=2,MarginV=30",
    "bold": "FontSize=32,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=3,BackColour=&H80000000,BorderStyle=4,Alignment=2,MarginV=40",
    "minimal": "FontSize=18,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=1,Alignment=2,MarginV=20",
    "outline": "FontSize=28,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=4,Alignment=2,MarginV=35",
}


def _subtitle_force_style(subtitle_style: str = "default") -> str:
    """libass force_style 串（从 ai_visual style_overrides 提取为共享）。

    ``"none"`` 返回空串，与 gateway 侧 ``subtitle_force_style`` 保持同一语义
    （2026-07-29）：这里烧不烧由 ``subtitle_path`` 决定，本函数只保证
    「无字幕」不被静默解释成 default 样式。两个 caller 都是 ``if force_style:``
    守卫，空串即不加 force_style，不会构造出畸形滤镜串。
    """
    if subtitle_style == "none":
        return ""
    return _SUBTITLE_STYLE_MAP.get(subtitle_style, _SUBTITLE_STYLE_MAP["default"])


def _write_subtitle_status(task_dir: Path, status: str, error: str | None = None) -> None:
    """P0-2: 写 subtitle_status.json 供 gateway 提取回传前端。

    status: ok | failed | skipped。gateway _extract_subtitle_status 读此文件。
    """
    import json

    data = {"status": status}
    if error:
        data["error"] = error
    try:
        (Path(task_dir) / "subtitle_status.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
    except OSError:
        logger.warning("failed to write subtitle_status.json", exc_info=True)


def _build_fade_af(total_duration: float, fade_sec: float = 0.5) -> str:
    """#1（评审补强）：片头/片尾音频淡入淡出 af，与 _build_fade_vf 对齐。

    旧 finalizer 只 -vf 画面 fade + -c:a copy -> 片尾画面黑了旁白/BGM 还在响。现加 afade
    让音轨同步淡出。"""
    out_st = max(total_duration - fade_sec, 0)
    return f"afade=t=in:st=0:d={fade_sec},afade=t=out:st={out_st:.2f}:d={fade_sec}"


def _polish_timeout(video: Path, base: int = 120, per_sec: float = 1.5, cap: int = 1800) -> int:
    """#2（评审补强）：polish 步动态超时——长视频（字节 seedance i2v ~280s/镜，多镜很长）
    旧固定 120s 会超时。按时长线性放大；探不到->base。"""
    try:
        dur = _probe_duration(Path(str(video)))
    except Exception:
        return base
    if dur <= 0:
        return base
    return min(cap, base + int(dur * per_sec))


_FONTSDIR_CANDIDATES = ("/usr/share/fonts", "/System/Library/Fonts", "/Library/Fonts", "/usr/local/share/fonts")

# 具体 CJK 字体文件候选——drawtext 的 fontfile= 要文件路径，
# 与 libass 的 fontsdir=（目录）不同，两者不可混用。
# Debian fonts-noto-cjk（Dockerfile:5）只提供 .ttc，勿写 .ttf
# （backend/tests/test_worker_fonts.py 锁死此事实）。
_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    # macOS 本地开发——需在目录扫描之前命中，否则会扫到
    # CJKSymbolsFallback.ttc（只含符号，正文汉字仍是方块）
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
)

# 候选全不中时扫这些目录找 CJK 字体——基础镜像是未锁 suite 的
# python:3.12-slim（Dockerfile:1），suite 浮动后 apt 布局可能变。
_CJK_FONT_DIRS = ("/usr/share/fonts", "/System/Library/Fonts")


def _resolve_cjk_fontfile() -> str | None:
    """返回第一个存在的 CJK 字体文件路径，供 drawtext 的 fontfile= 用。

    2026-07-29：黑场占位片（_make_black_subtitle_clip）此前既无 fontfile
    也无 font，ffmpeg 回落内建 Latin-only 默认字体 -> 中文全变方块
    （线上实测「画面中间各种方块乱码」）。字幕烧录走 libass 有 fontsdir 兜底
    （_subtitle_filter_vf），本函数补齐 drawtext 侧。

    返回 None 时调用方须优雅降级（不烧录字体名，英文仍可读），不得抛异常——
    黑场占位本身已是兜底路径，不能再成为新的失败源。
    """
    for f in _CJK_FONT_CANDIDATES:
        if Path(f).is_file():
            return f
    for root in _CJK_FONT_DIRS:
        base = Path(root)
        if not base.is_dir():
            continue
        try:
            for p in sorted(base.rglob("*")):
                if p.suffix.lower() in (".ttc", ".otf", ".ttf") and "CJK" in p.name:
                    return str(p)
        except OSError:
            continue
    return None


def _subtitle_filter_vf(srt: Path, force_style: str | None = None, fontsdir: str | None = None) -> str:
    """构造 subtitles= 滤镜串（含 force_style + 路径转义 + fontsdir 兜底）。

    T6（2026-08-13）：.ass 文件自带样式（PlayRes/WrapStyle/FontSize），不拼 force_style；
    .srt 保持旧路径（force_style + fontsdir 兜底）兼容存量任务。
    """
    parts = [f"subtitles={_escape_subtitle_filter_path(str(srt))}"]
    is_ass = str(srt).lower().endswith(".ass")
    fd = fontsdir
    if fd is None:
        fd = next((d for d in _FONTSDIR_CANDIDATES if Path(d).exists()), None)
    if fd:
        parts.append(f"fontsdir={_escape_subtitle_filter_path(fd)}")
    # ASS 自带样式，不覆盖 force_style；SRT 旧路径保留 force_style
    if force_style and not is_ass:
        escaped_style = force_style.replace("'", "'\\''")
        parts.append(f"force_style='{escaped_style}'")
    return ":".join(parts)


@functools.lru_cache(maxsize=1)
def _subtitles_filter_available() -> bool:
    """F1: 检测 ffmpeg 是否含 subtitles 滤镜（需 libass）。缓存避免重复 spawn。

    复用 run-local.sh 的检测逻辑。本机 dev 若缺 libass -> _burn_subtitles 给明确错误
    而非 ffmpeg 笼统的 'Error opening output files: Invalid argument'。
    Docker Worker 镜像构建期已校验存在（Dockerfile:13）。
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"],
            capture_output=True, text=True, timeout=10,
        )
        return "subtitles" in (r.stdout or "")
    except Exception:
        return False


def _burn_subtitles(video: Path, srt: Path, output: Path, *, force_style: str | None = None) -> tuple[bool, str]:
    """Burn subtitles into video. Returns (True, "") on success, (False, stderr_tail) on failure.

    T6（2026-08-13）：支持 .ass 文件（自带样式，不拼 force_style）。
    .srt 保持旧路径（force_style）。ASS 失败不自动回退 SRT——由调用方决定。
    """
    if not _subtitles_filter_available():
        msg = (
            "ffmpeg 缺 libass（subtitles 滤镜不可用），无法烧录字幕。"
            "修复：brew install libass && brew reinstall ffmpeg；或改用 Docker Worker（镜像已装 libass）"
        )
        logger.warning(msg)
        return False, msg
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-vf", _subtitle_filter_vf(srt, force_style),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=_polish_timeout(video, base=300))
    if proc.returncode != 0 or not output.exists():
        err_tail = (proc.stderr or b"").decode(errors="replace").strip().splitlines()[-1:] or ["unknown"]
        logger.warning("Subtitle burn failed, skipping subtitles: %s", err_tail[0][:200])
        output.unlink(missing_ok=True)
        return False, err_tail[0][:200]
    return True, ""


def _apply_colorgrade_and_subtitles(
    video: Path,
    *,
    task_dir: Path,
    tmp_dir: Path,
    color_palette: dict | None = None,
    subtitle_path: str | None = None,
    subtitle_style: str = "default",
    subtitle_ass: str | None = None,
) -> Path:
    """共享收口：colorgrade -> 烧字幕 -> 写 subtitle_status。

    T6（2026-08-13）：优先烧 ``subtitle_ass``（.ass 自带样式，不拼 force_style）；
    无 ASS 回退 ``subtitle_path``（.srt + force_style）+ WARN。
    """
    current = Path(str(video))
    cg_vf = _colorgrade_vf(color_palette)  # None=无操作

    # T6: 优先 ASS（自带样式），回退 SRT + force_style
    ass_path = subtitle_ass if subtitle_ass and Path(subtitle_ass).exists() else None
    if ass_path:
        sub_file = str(ass_path)
        sub_fs = None  # ASS 自带样式
    elif subtitle_path and Path(subtitle_path).exists():
        sub_file = str(subtitle_path)
        sub_fs = _subtitle_force_style(subtitle_style)
    else:
        sub_file = None
        sub_fs = None

    has_sub = bool(sub_file)
    # 合并路径：colorgrade + 字幕同在 -> 单次 -vf（减一次重编码）；失败兜底拆开
    if cg_vf and has_sub:
        try:
            out = tmp_dir / "graded_subs.mp4"
            merged_vf = f"{cg_vf},{_subtitle_filter_vf(Path(sub_file), sub_fs)}"
            cmd = ["ffmpeg", "-y", "-i", str(current), "-vf", merged_vf,
                   "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
                   "-c:a", "copy", str(out)]
            proc = subprocess.run(cmd, capture_output=True, timeout=_polish_timeout(current))
            if proc.returncode == 0 and out.exists():
                _write_subtitle_status(task_dir, "ok")
                return out
            logger.warning("merged colorgrade+subtitles failed, retrying split: %s", proc.stderr.decode(errors="replace")[-300:])
        except Exception:
            logger.warning("merged colorgrade+subtitles exception, retrying split", exc_info=True)
    # 单独路径（仅 colorgrade / 仅字幕 / 合并兜底）
    if cg_vf:
        try:
            graded = tmp_dir / "graded.mp4"
            _ffmpeg_colorgrade(current, graded, color_palette)
            if graded.exists():
                current = graded
        except Exception:
            logger.warning("colorgrade failed, continuing without")
    if has_sub:
        sub_out = tmp_dir / "with_subs.mp4"
        _sub_ok, _sub_err = _burn_subtitles(current, Path(sub_file), sub_out, force_style=sub_fs)
        if _sub_ok:
            current = sub_out
            _write_subtitle_status(task_dir, "ok")
        else:
            _write_subtitle_status(task_dir, "failed", f"字幕烧录失败：{_sub_err}")
    else:
        _write_subtitle_status(task_dir, "skipped")
    return current


def _apply_intro_outro_fade(video: Path, *, tmp_dir: Path, fade_sec: float = 0.5, quality: str = "standard") -> Path:
    """共享收口：片头淡入 + 片尾淡出（画面 + 音轨，Task 3g + #1 评审补强）。>1s 才 fade。

    #1：旧版仅 -vf 画面 fade + -c:a copy -> 片尾画面黑了旁白/BGM 还在响。现加 -af afade
    音轨同步淡出、-c:a aac 重编码音轨带 fade。无音轨时 -af 失败->except 返原视频（不阻断）。
    """
    try:
        total_dur = _probe_duration(Path(str(video)))
    except Exception:
        return Path(str(video))
    if total_dur <= 1.0:
        return Path(str(video))
    try:
        faded = tmp_dir / "faded.mp4"
        cmd = [
            "ffmpeg", "-y", "-i", str(video),
            "-vf", _build_fade_vf(total_dur, fade_sec),
            "-af", _build_fade_af(total_dur, fade_sec),
            *_get_quality_args(quality), "-c:a", "aac", str(faded),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=_polish_timeout(Path(str(video))))
        if proc.returncode == 0 and faded.exists():
            return faded
        logger.warning("fade polish failed (returncode=%s), continuing without: %s", proc.returncode, proc.stderr.decode(errors="replace")[-300:])
    except Exception:
        logger.warning("fade polish failed, continuing without", exc_info=True)
    return Path(str(video))


def _ffmpeg_colorgrade(segment: Path, output: Path, color_palette: dict | None) -> None:
    """Apply color grading to a segment based on color_palette.

    Uses ``eq`` filter only (Task B: removed destructive ``hue=h=<primary hue angle>``).
    No-op (copy) if color_palette is None or all-default.
    """
    vf = _colorgrade_vf(color_palette)
    if vf is None:
        shutil.copy2(segment, output)
        return
    cmd = [
        "ffmpeg", "-y", "-i", str(segment),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-c:a", "copy",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        logger.warning("colorgrade failed, copying original: %s", _ffmpeg_err(proc.stderr, "colorgrade"))
        shutil.copy2(segment, output)


def _ffmpeg_concat(segments: list[Path], output: Path, *, quality: str = "standard", timeout: int = 300) -> None:
    """Concatenate segments, stream-copy first, re-encode only as fallback.

    2026-08-03 Task 18：原先无条件重编码（``-preset medium``），54 镜任务实测 354s
    超时（vg_66978a4b06ba）。段进来时已同规格，故先试 ``-c copy``（本机实测高熵素材
    0.363s vs 重编码 12.214s，产物等价），仅在 copy 不可信时才走原重编码路径。

    **回落判据不能只看 returncode。** 实测：混帧率段（30fps 2s + 24fps 2s）concat
    ``-c copy`` 返回 **rc=0**，只在 stderr 刷 ``Non-monotonic DTS``，产物时长却是
    3.200s（应为 4.0s）——即时间轴被静默压缩。这是本函数最危险的失效模式（音画漂移，
    参见 vg_93e83a7ee259 一类事故），所以 copy 后必须 ffprobe 校验实测时长与各段
    时长之和，偏差超容差即判 copy 不可信并回落重编码（重编码会重新采样时间戳）。
    """
    if not segments:
        raise ValueError("No segments to concatenate")

    quality_args = _get_quality_args(quality)
    filelist = output.parent / "filelist.txt"
    try:
        with open(filelist, "w") as f:
            for p in segments:
                f.write(f"file '{p}'\n")

        # Task 18：优先 stream copy。段已由 normalize_clip_resolution / _image_to_video
        # 归一到 CONCAT_TARGET_FPS + libx264 + yuv420p（G1），此处再套 -preset medium
        # 全片重编码既浪费（54 镜实测超 354s 超时，vg_66978a4b06ba）又多叠一次画质损失。
        # copy 路径不带 -r：帧率由段自身保证（G1 已把每段归一到 30fps），而 -r 对
        # stream copy 无效（不重新采样，只会改容器时基）。回落路径仍带 -r 作双保险。
        # 回落不可省：上传素材归一化失败时会 copy2 原片，规格可能不一致。
        copy_cmd = [
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", str(filelist),
            "-c", "copy", "-movflags", "+faststart",
            str(output),
        ]
        proc = subprocess.run(copy_cmd, capture_output=True, timeout=timeout)
        copy_reason = ""
        if proc.returncode != 0 or not output.exists():
            copy_reason = _ffmpeg_err(proc.stderr, "concat copy")
        else:
            # 时长校验：混规格段的 copy 会 rc=0 但静默压缩时间轴（见 docstring）。
            #
            # BUG-A 修（2026-08-03）：必须**逐段**探测且要求全部成功才做校验。
            # _probe_duration 对探不到的段返回 0.0，只看 sum>0 的话，54 段里 1 段失败
            # 就会让 expected 少算该段时长，而好的 copy 产物 got 含全部段 ->
            # |got-expected| 变大 -> 把成功的 copy 误判为时长不符 -> 回落重编码，
            # Task 18 的提速静默失效，退回它本要修的慢路径。
            # 触发条件真实存在：normalize_clip_resolution 归一失败时 shutil.copy2 原片，
            # 会把不可 probe 的上传素材直接塞进 segments。
            # 原则不变：不拿「部分未知」去否决一个成功的 copy。
            seg_durations = [_probe_duration(p) for p in segments]
            expected = sum(seg_durations)
            got = _probe_duration(output)
            if (
                expected > 0
                and all(d > 0 for d in seg_durations)
                and (got < expected - _CONCAT_COPY_SHORT_TOLERANCE_SEC or got > expected + _CONCAT_COPY_TOLERANCE_SEC)
            ):
                copy_reason = f"duration mismatch: got {got:.3f}s, segments sum to {expected:.3f}s"
        if copy_reason:
            logger.warning("concat stream copy unusable, falling back to re-encode: %s", copy_reason)
            proc = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "concat", "-safe", "0", "-i", str(filelist),
                    "-r", str(CONCAT_TARGET_FPS),  # G1: 双保险统一输出帧率
                    *quality_args,
                    "-c:a", "aac",
                    str(output),
                ],
                capture_output=True,
                timeout=timeout,
            )
        if proc.returncode != 0:
            raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg concat failed"))
    finally:
        filelist.unlink(missing_ok=True)


def _ffmpeg_mix_audio(video: Path, audio: Path, output: Path) -> None:
    """Mix video with an external audio track.

    2026-07-22 根治（vg_f74c8fa2331a 混音 300s 超时）：删除 tpad 整片重编码分支。
    旧行为：视频短于音频 >0.05s 时用 ``tpad=stop_mode=clone`` 烘焙定格末帧 ->
    全片 libx264 重编码（680s 2048x1152 实测超 300s 硬超时，两轮渲染连败）。
    新行为：视频短于音频时**不加 -shortest** —— 容器时长 = max(v,a)，播放器对末帧
    自然定格（与 tpad 视觉效果一致），视频流 stream copy 秒级完成，耗时与片长脱钩。
    视频长于音频时保留 -shortest（切视频尾，旧行为不变）。
    timeout=300 对 copy-only 操作与片长无关，保持硬编码即可。
    """
    if not video.exists():
        raise RuntimeError(f"audio mix aborted: input video missing: {video}")
    if not audio.exists():
        raise RuntimeError(f"audio mix aborted: input audio missing: {audio}")
    probe_audio = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", str(video)],
        capture_output=True, text=True, timeout=10,
    )
    has_audio = bool(probe_audio.stdout.strip())

    # 音频明显更长（转场重叠/分段取整残差）-> 不得加 -shortest（会切旁白尾巴，B1 红线）
    try:
        video_dur = _ffprobe_duration(video)
        audio_dur = _ffprobe_duration(audio)
        delta = audio_dur - video_dur
    except (ValueError, subprocess.SubprocessError):
        delta = 0.0
    audio_longer = delta > 0.05

    if has_audio:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video), "-i", str(audio),
            "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=longest:normalize=0[aout]",
            "-map", "0:v", "-map", "[aout]",
            "-c:v", "copy", "-c:a", "aac",
        ]
    else:
        cmd = [
            "ffmpeg", "-y",
            "-i", str(video), "-i", str(audio),
            "-map", "0:v", "-map", "1:a",
            "-c:v", "copy", "-c:a", "aac",
        ]
    if not audio_longer:
        cmd.append("-shortest")
    cmd.append(str(output))

    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg audio mix failed"))


def _ffprobe_stream_duration(path: Path, selector: str) -> float:
    """probe 单条流时长（selector 'v:0' / 'a:0'）；探测失败或无该流 -> 0.0。"""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", selector,
         "-show_entries", "stream=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    try:
        return float(probe.stdout.strip().splitlines()[0])
    except (ValueError, IndexError, TypeError, AttributeError):
        return 0.0


def _shortest_args_for(video: Path) -> list[str]:
    """视频流明显短于音频流（>0.05s）时返 []（禁 -shortest，保旁白尾巴，容器末帧定格）；

    否则返 ["-shortest"]（切视频尾，旧行为）。供 _ffmpeg_mix_bgm* 复用——
    mix_audio 去 tpad 后，音频更长的文件到达 BGM 阶段时 视频流<音频流，
    无条件 -shortest 会把旁白尾巴再次切掉（B1 回潮）。
    """
    v_dur = _ffprobe_stream_duration(video, "v:0")
    a_dur = _ffprobe_stream_duration(video, "a:0")
    if v_dur > 0 and a_dur > 0 and (a_dur - v_dur) > 0.05:
        return []
    return ["-shortest"]


def _ffmpeg_mix_bgm(video: Path, bgm: Path, output: Path, *, bgm_volume: float = 0.15) -> None:
    """Mix BGM into video. BGM is looped/truncated to video duration and mixed at reduced volume."""
    if not video.exists():
        raise RuntimeError(f"bgm mix aborted: input video missing: {video}")
    if not bgm.exists():
        raise RuntimeError(f"bgm mix aborted: input bgm missing: {bgm}")
    # BGM volume as fraction: 0.15 = -16dB roughly
    vol_str = f"{bgm_volume:.2f}"
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video), "-i", str(bgm),
        "-filter_complex",
        f"[1:a]aloop=loop=-1:size=2e9,volume={vol_str},atrim=0:{_ffprobe_duration(video)}[bgm];"
        f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", *_shortest_args_for(video),
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg BGM mix failed"))


def _ffmpeg_mix_bgm_with_ducking(
    video: Path,
    bgm: Path,
    output: Path,
    *,
    bgm_volume: float = 0.15,
    duck_threshold: float = -20.0,
    duck_ratio: float = 4.0,
    attack: float = 0.1,
    release: float = 0.5,
) -> None:
    """Mix BGM with automatic ducking when voice is active.

    Uses ffmpeg sidechaincompress: voice signal controls BGM volume.
    When voice exceeds duck_threshold, BGM volume is reduced by duck_ratio.
    Falls back to simple mix on failure.
    """
    if not video.exists():
        raise RuntimeError(f"bgm mix aborted: input video missing: {video}")
    if not bgm.exists():
        raise RuntimeError(f"bgm mix aborted: input bgm missing: {bgm}")
    vol_str = f"{bgm_volume:.2f}"
    video_dur = _ffprobe_duration(video)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video),   # [0] video + voice
        "-i", str(bgm),     # [1] BGM
        "-filter_complex",
        # BGM: aloop 循环补长（修前 apad=静音填充 -> 10s 音乐在 10s 后消失）-> 截到视频长
        f"[1:a]volume={vol_str},aloop=loop=-1:size=2e9[bgm_looped];"
        f"[bgm_looped]atrim=0:{video_dur}[bgm_base];"
        # Sidechain compress: voice controls BGM ducking
        f"[bgm_base][0:a]sidechaincompress=threshold={duck_threshold}dB"
        f":ratio={duck_ratio}:attack={attack}:release={release}"
        f":makeup=6dB[bgm_ducked];"
        # Mix ducked BGM with voice
        f"[0:a][bgm_ducked]amix=inputs=2:duration=first:dropout_transition=0:normalize=0[aout]",
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", *_shortest_args_for(video),
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if proc.returncode != 0:
        logger.warning("BGM ducking failed, falling back to simple mix: %s",
                       _ffmpeg_err(proc.stderr, "ducking"))
        _ffmpeg_mix_bgm(video, bgm, output, bgm_volume=bgm_volume)


def _ffmpeg_convert_format(
    input_path: Path,
    output_path: Path,
    *,
    output_format: str = "mp4",
    quality: str = "standard",
    timeout: int = 300,
) -> Path:
    """Convert video to target format with quality preset.

    Returns the output path. For mp4, returns input_path unchanged (no re-encode needed unless quality differs).
    """
    if output_format == "mp4" and input_path.suffix == ".mp4":
        # Task 3f (Bug 1 fix): 不重编码，但做 moov 重定位（stream copy + faststart）。
        # 上游 concat 之后的 burn字幕/colorgrade/audio mix/BGM/fade 等重编码步未带
        # faststart，moov 落在尾部 -> 必须在此最终出口收口，否则 Web 仍无法边下边播。
        # stream copy 不损画质、不重编码，仅把 moov atom 前置。
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c", "copy", "-movflags", "+faststart",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            # 兜底：moov 重定位失败（罕见）-> 直接拷贝（moov 在尾部，仍可播放只是不能边下边播）
            logger.warning("faststart moov relocate failed, copying as-is: %s", _ffmpeg_err(proc.stderr, "faststart"))
            import shutil

            shutil.copy2(input_path, output_path)
        return output_path

    if output_format == "gif":
        # GIF: extract with palette for better quality
        palette = output_path.parent / "palette.png"
        dur = _ffprobe_duration(input_path)
        fps = min(15, max(8, int(30 / max(dur / 10, 1))))  # adaptive fps
        # Generate palette
        subprocess.run(
            ["ffmpeg", "-y", "-i", str(input_path),
             "-vf", f"fps={fps},palettegen=max_colors=128",
             str(palette)],
            capture_output=True, timeout=timeout,
        )
        # Use palette
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path), "-i", str(palette),
            "-filter_complex", f"fps={fps},paletteuse=dither=bayer:bayer_scale=3",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        palette.unlink(missing_ok=True)
        if proc.returncode != 0:
            raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg gif conversion failed"))
        return output_path

    if output_format == "webm":
        preset = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["standard"])
        crf_val = preset["crf"]
        # VP9 CRF range is 0-63 (higher = lower quality)
        vp9_crf = min(63, int(crf_val * 63 / 51))
        cmd = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-c:v", "libvpx-vp9", "-crf", str(vp9_crf), "-b:v", "0",
            "-c:a", "libopus", "-pix_fmt", "yuv420p",
            str(output_path),
        ]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0:
            raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg webm conversion failed"))
        return output_path

    # Fallback: just copy
    return input_path


def _normalize_loudness(video: Path, output: Path, *, target_i: float = -16.0, target_tp: float = -1.5, target_lra: float = 11.0) -> Path:
    """C3（Task 8）：把成品音轨响度归一到广播/流媒体常用目标。

    修前全仓**零** loudnorm —— 不同任务、不同 TTS 供应商产出音量忽大忽小，用户每
    看一个视频都要手动调音量。

    **位置很关键**：必须在 ``amix`` **之后**对成品整轨做一次。Task 5 给四处 amix
    加了 ``normalize=0`` 以免旁白被按 1/N 衰减；若在 amix 之前逐路 loudnorm，两者
    会相互抵消。此函数在混音（旁白 + BGM）全部完成后调用，两个修复正交。

    单次（非双 pass）loudnorm 已足够把不同来源收敛到 ±1.5 LUFS 内；双 pass 需要
    先分析再编码两趟，对成片时长收益不划算。

    失败（无音轨/损坏/ffmpeg 报错）时**返回原文件**——响度归一是锦上添花，不该
    阻断出片。
    """
    cmd = [
        "ffmpeg", "-y", "-i", str(video),
        "-af", f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        str(output),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=_polish_timeout(video))
    except Exception:
        logger.warning("loudness normalization raised, keeping original", exc_info=True)
        return video
    if proc.returncode == 0 and output.exists() and output.stat().st_size > 0:
        return output
    logger.warning(
        "loudness normalization failed (rc=%s), keeping original: %s",
        proc.returncode,
        proc.stderr.decode(errors="replace")[-300:],
    )
    output.unlink(missing_ok=True)
    return video


def _finalize_output(result: Path, final_raw: Path) -> Path:
    """把最终视频落到 final_raw，无条件原子覆盖（修 recompose mp4 短路 bug）。

    - result == final_raw：convert 已直接写出 output（真转码场景），原样返回。
    - result != final_raw：convert 短路返回了 input 路径（mp4->mp4），需把 result
      原子移到 final_raw。用"先 move 到同目录 .tmp 再 os.replace"保证原子性——
      读者要么看到旧 output 要么看到新 output，无 partial/404 窗口。

    修两个 bug：
    1. ai_visual 旧代码 `if result != final_raw and not final_raw.exists()` ——
       recompose 时 output 已存在致 guard 为 False、move 被跳过、新视频被 finally 删。
    2. mixed 旧代码无 move 兜底 —— 短路时 output.mp4 永不创建。
    """
    import os

    if result == final_raw:
        return final_raw
    tmp_final = final_raw.with_suffix(final_raw.suffix + ".tmp")
    shutil.move(str(result), str(tmp_final))
    os.replace(str(tmp_final), str(final_raw))
    return final_raw


def _ffmpeg_extract_thumbnail(video_path: Path, output_path: Path, timeout: int = 30) -> bool:
    """Extract a JPEG thumbnail from the video.

    Seeks to max(1s, duration*10%) to avoid black/transition frames.
    Returns True on success.
    """
    try:
        duration = _ffprobe_duration(video_path)
        ss = max(1.0, duration * 0.1)
        cmd = ["ffmpeg", "-y", "-ss", str(ss), "-i", str(video_path),
               "-frames:v", "1", "-q:v", "2", str(output_path)]
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return proc.returncode == 0 and output_path.exists()
    except Exception:
        logger.warning("Thumbnail extraction failed for %s", video_path)
        return False


def _probe_duration(path) -> float:
    """ffprobe 探视频时长（秒），失败返 0（不抛，安全用）。

    包装 _ffprobe_duration 的异常（ai_video 占位/对齐路径下 ffprobe 可能失败）。
    """
    try:
        return _ffprobe_duration(path)
    except Exception:
        return 0.0


def _kenburns_tail_clip(
    tail_png: Path,
    gap: float,
    output: Path,
    *,
    width: int = 1280,
    height: int = 720,
    frame_index: int = 0,
) -> Path:
    """从末帧 PNG 生成 Ken Burns zoompan 缓推片段（Phase 1 Task 2）。

    复刻 ``video_gen_ai_visual.py:_image_to_video`` 的命令形态（-loop 1 + zoompan vf），
    但**不 import video_gen_ai_visual**——它反向 import _ffmpeg_helpers，直接 import 会
    循环依赖。故用本模块已有的 ``_build_zoompan_vf`` 自组装。

    gap 为补长时长（秒），frame_index 透传以保持 zoom 方向交替语义。
    """
    vf = _build_zoompan_vf(width=width, height=height, duration=gap, frame_index=frame_index)
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(tail_png),
        "-vf", vf,
        "-c:v", "libx264", "-crf", "20", "-preset", "fast",
        "-pix_fmt", "yuv420p",
        "-t", f"{gap:.3f}",
        "-an", str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"kenburns tail clip failed (rc={proc.returncode})")
    return output


# Phase 1 分层降级链默认阈值（可被 config dict 覆盖，不读 env）
_SLOW_MAX_RATIO = 0.10  # Tier 1 微慢放最大占比
_INTERP_ENABLED = False  # Task 3: minterpolate 默认关闭


def _concat_two_with_duration_check(
    seg_a: Path, seg_b: Path, output: Path, *, timeout: int = 180
) -> Path:
    """concat 两段，stream copy 先试，ffprobe 校验时长 ∈ [a+b−0.3, a+b+0.5]，不合格重编码。

    既有教训：concat -c copy 混规格段 rc=0 但静默压缩时长。必须 ffprobe 校验。
    两段分辨率不一致直接走重编码路径。
    """
    dur_a = _probe_duration(seg_a)
    dur_b = _probe_duration(seg_b)
    expected = dur_a + dur_b
    dim_a = _probe_dimensions(seg_a)
    dim_b = _probe_dimensions(seg_b)
    same_dims = dim_a == dim_b and dim_a[0] > 0

    filelist = output.parent / "_concat_two_list.txt"
    try:
        with open(filelist, "w") as f:
            f.write(f"file '{seg_a}'\nfile '{seg_b}'\n")

        if same_dims:
            # 先试 stream copy（-an 剥音轨：段音轨随后由 normalize_clip_resolution 处理）
            copy_cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(filelist), "-c:v", "copy", "-an", "-movflags", "+faststart", str(output),
            ]
            proc = subprocess.run(copy_cmd, capture_output=True, timeout=timeout)
            if proc.returncode == 0 and output.exists():
                got = _probe_duration(output)
                # 非对称容差 [−0.3, +0.5]：短超 0.3s 说明时长被静默压缩（混规格段教训），
                # 必须重编码；长超 0.5s 才不信（下游 -t 截断无损）。
                if expected > 0 and (got >= expected - 0.3) and (got <= expected + 0.5):
                    return output
                logger.warning(
                    "concat copy duration mismatch: got %.3fs, expected %.3fs — re-encoding",
                    got, expected,
                )
            output.unlink(missing_ok=True)

        # 重编码路径（filter_complex concat）
        reenc_cmd = [
            "ffmpeg", "-y", "-i", str(seg_a), "-i", str(seg_b),
            "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0[v]",
            "-map", "[v]",
            "-r", str(CONCAT_TARGET_FPS),
            "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
            "-an", str(output),
        ]
        proc = subprocess.run(reenc_cmd, capture_output=True, timeout=timeout)
        if proc.returncode != 0 or not output.exists():
            raise RuntimeError(_ffmpeg_err(proc.stderr, "concat two segments failed"))
        # 校验重编码产物时长（与 copy 判定同口径：短超 0.3s 或长超 0.5s 才告警）
        got = _probe_duration(output)
        if expected > 0 and (got < expected - 0.3 or got > expected + 0.5):
            logger.warning("concat re-encode duration off: got %.3fs, expected %.3fs", got, expected)
        return output
    finally:
        filelist.unlink(missing_ok=True)


def _interp_timeout(lease_remaining: float | None) -> int:
    """minterpolate 阻塞 subprocess 的 timeout 核算（C5 租约不变量）。

    timeout 必须 ≤ 剩余租约 − 300（运行期间无法续租，留缓冲防 recover_stale_tasks
    静默重排），夹在 [60, 300]；无租约信息（None）回旧行为 300。
    已知取舍：租约 < 360s 时下限 60 会突破「≤租约−300」（届时拿不到正值超时），
    取友好超时 60 而非秒退——调用方正常续租下不会进入该区。
    """
    if lease_remaining is None:
        return 300
    return max(min(lease_remaining - 300, 300), 60)


def _ffmpeg_extend_clip_to_duration(
    clip: Path,
    target: float,
    output: Path,
    *,
    config: dict | None = None,
    frame_index: int = 0,
    lease_remaining: float | None = None,
) -> Path:
    """把短于 target 的 clip 补到 target 秒——分层降级链（Phase 1 v2）。

    ``gap = target - actual_dur``，``gap_ratio = gap / target``：

    | Tier | 条件 | 策略 |
    |------|------|------|
    | 1 | gap_ratio ≤ slow_max_ratio (0.10) | 整段 setpts 微慢放（≤10% 不可感知） |
    | 2 | gap_ratio > slow_max_ratio | 末段 Ken Burns zoompan 缓推（默认主力） |
    | 3 | Tier 1/2 均失败 | tpad 硬定格（兜底，永不失败） |

    实验项：config["interp_enabled"]=True 且 gap_ratio ≤ 0.50 时，在 Tier 2 之前
    尝试 minterpolate（mci → blend 降级，再失败落 Tier 2）。

    config dict 从 payload["config"] 透传（与 transition/width/height 同路径），不读 env。
    frame_index 透传以保持 Ken Burns zoom 方向交替语义。

    H1（保留）：不得引入任何 fade。失败抛 RuntimeError。
    """
    actual = _probe_duration(clip)
    if actual > 0 and actual >= target:
        cmd = ["ffmpeg", "-y", "-i", str(clip), "-t", str(target), "-c", "copy", str(output)]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0 or not output.exists():
            raise RuntimeError(f"trim clip to {target}s failed (rc={proc.returncode})")
        return output

    # N9: 探不到时长（actual <= 0）按「未知」保守补长
    gap = max(target - actual, 0.0) if actual > 0 else target
    # M7 修复：Tier 1 判据用 gap/actual（真实拉伸率），而非 gap/target。
    # gap/target ≤ 0.10 时 actual≈0.9×target，真实慢放 = 1+gap/actual ≈ 1.111（11.1% 超 10% 声称）。
    # gap_ratio (gap/target) 保留用于日志/interpolate 判据（用户感知口径）。
    gap_ratio = gap / target if target > 0 else 1.0
    slowmo_ratio = gap / actual if actual > 0 else 1.0

    # 从 config dict 读阈值（零 env）
    slow_max_ratio = (config or {}).get("slow_max_ratio", _SLOW_MAX_RATIO)
    interp_enabled = (config or {}).get("interp_enabled", _INTERP_ENABLED)

    # ── Tier 1: setpts 微慢放（≤10% actual 拉伸率） ──────────────
    if 0 < slowmo_ratio <= slow_max_ratio and actual > 0:
        try:
            ratio = 1.0 + gap / actual  # e.g. gap=0.7, actual=9.3 → ratio=1.0753
            slowmo_cmd = [
                "ffmpeg", "-y", "-i", str(clip),
                "-vf", f"setpts={ratio:.6f}*PTS",
                "-r", str(CONCAT_TARGET_FPS),
                "-t", f"{target:.3f}",
                "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
                "-an", str(output),
            ]
            proc = subprocess.run(slowmo_cmd, capture_output=True, timeout=180)
            if proc.returncode == 0 and output.exists():
                got = _probe_duration(output)
                if abs(got - target) <= 0.5:
                    logger.info("fill_strategy=slowmo gap=%.2fs gap_ratio=%.1f%%", gap, gap_ratio * 100)
                    return output
            logger.warning("Tier 1 slowmo failed (rc=%s), falling through", proc.returncode)
        except Exception:
            logger.warning("Tier 1 slowmo exception, falling through", exc_info=True)

    # ── 实验项: minterpolate（默认关闭） ──────────────────────────
    if interp_enabled and gap_ratio <= 0.50 and actual > 0:
        r = actual / target  # 时间拉伸比
        for mi_mode in ("mci", "blend"):
            try:
                interp_cmd = [
                    "ffmpeg", "-y", "-i", str(clip),
                    "-vf", f"setpts=PTS/{r:.6f},minterpolate=fps={CONCAT_TARGET_FPS}:mi_mode={mi_mode}"
                    + (":mc_mode=aobmc:me_mode=bidir:vsbmc=1" if mi_mode == "mci" else ""),
                    "-t", f"{target:.3f}",
                    "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
                    "-an", str(output),
                ]
                # 租约不变量：minterpolate 在 720p 约 1-3× 实时，timeout 严格 ≤ 租约−300
                # （lease_remaining 由 compose 调用链传入，本模块不读 env）
                proc = subprocess.run(interp_cmd, capture_output=True, timeout=_interp_timeout(lease_remaining))
                if proc.returncode == 0 and output.exists():
                    got = _probe_duration(output)
                    if abs(got - target) <= 0.5:
                        logger.info("fill_strategy=interp mode=%s gap=%.2fs", mi_mode, gap)
                        return output
                logger.warning("minterpolate mi_mode=%s failed (rc=%s), trying next", mi_mode, proc.returncode)
                output.unlink(missing_ok=True)
            except Exception:
                logger.warning("minterpolate mi_mode=%s exception", mi_mode, exc_info=True)
                output.unlink(missing_ok=True)
        # minterpolate 全部失败 → 落 Tier 2

    # ── Tier 2: 末段 Ken Burns 缓推（默认主力） ────────────────────
    if actual > 0:
        try:
            # 1) 抽末帧（-sseof 避开文件尾坏帧）
            tail_png = output.parent / f"_tail_{output.stem}.png"
            extract_cmd = [
                "ffmpeg", "-y", "-sseof", "-0.3", "-i", str(clip),
                "-frames:v", "1", "-q:v", "2", str(tail_png),
            ]
            proc = subprocess.run(extract_cmd, capture_output=True, timeout=30)
            if proc.returncode != 0 or not tail_png.exists():
                raise RuntimeError("tail frame extraction failed")
            # 2) Ken Burns 缓推片段
            w, h = _probe_dimensions(clip)
            if w <= 0 or h <= 0:
                w, h = 1280, 720
            tail_clip = output.parent / f"_kb_{output.stem}.mp4"
            _kenburns_tail_clip(tail_png, gap, tail_clip, width=w, height=h, frame_index=frame_index)
            tail_png.unlink(missing_ok=True)
            # 3) concat 原片 + 缓推 → -t target 收尾
            _concat_two_with_duration_check(clip, tail_clip, output, timeout=180)
            tail_clip.unlink(missing_ok=True)
            got = _probe_duration(output)
            if abs(got - target) <= 0.5:
                logger.info("fill_strategy=kenburns gap=%.2fs gap_ratio=%.1f%%", gap, gap_ratio * 100)
                return output
            logger.warning("Tier 2 kenburns duration off: got %.3fs, target %.3fs", got, target)
        except Exception:
            logger.warning("Tier 2 kenburns failed, falling to tpad", exc_info=True)

    # ── Tier 3: tpad 硬定格（最终兜底） ────────────────────────────
    pad = gap
    cmd = ["ffmpeg", "-y", "-i", str(clip),
           "-vf", f"tpad=stop_mode=clone:stop_duration={pad:.3f}",
           "-t", str(target),
           "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
           "-an", str(output)]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"extend clip to {target}s failed (rc={proc.returncode})")
    logger.info("fill_strategy=tpad gap=%.2fs gap_ratio=%.1f%%", gap, gap_ratio * 100)
    return output


def _probe_dimensions(clip: Path) -> tuple[int, int]:
    """Task 3h: probe video clip width/height via ffprobe。"""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", str(clip)],
            capture_output=True, text=True, timeout=15,
        )
        parts = proc.stdout.strip().split(",")
        return int(parts[0]), int(parts[1])
    except Exception:
        return 0, 0


def normalize_clip_resolution(clip: Path, output: Path, target_w: int, target_h: int, *, max_duration: float | None = None) -> Path:
    """Task 3h + G1: 归一 video clip 到目标分辨率 + 统一帧率/编码（cover 裁剪，不拉伸）。

    G1: 始终 re-encode 到 CONCAT_TARGET_FPS + libx264 + yuv420p（修前尺寸匹配时 copy
    保留原帧率/编码 -> concat 衔接 pts 不连续 -> 黑场/卡顿）。失败兜底 copy 原片（不阻塞）。
    H5: ①``-an`` 剥段音轨——上传 mp4 原声会随 concat 进 _ffmpeg_mix_audio 被 amix 等增益
    混入旁白（响度减半）；段统一无音轨后流布局一致（Ken Burns/黑场段本就无音轨），
    concat demuxer 行为确定。②``max_duration`` 精确截断长 clip（重编码路径，帧级精度）——
    段时长>帧时长会让后续帧画面相对旁白整体错位（短 clip 不补长是 D2 设计，维持）。
    """
    w, h = _probe_dimensions(clip)
    need_scale = (w != target_w) or (h != target_h)
    cmd: list = ["ffmpeg", "-y", "-i", str(clip)]
    if need_scale:
        cmd += ["-vf", f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,crop={target_w}:{target_h}"]
    if max_duration is not None and max_duration > 0:
        cmd += ["-t", f"{max_duration:.3f}"]
    cmd += [
        "-r", str(CONCAT_TARGET_FPS),
        "-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p",
        "-an", str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=180)
    if proc.returncode != 0 or not output.exists():
        shutil.copy2(clip, output)  # 失败则保留原片（不阻塞）
    return output


def _probe_black_scenes(video: Path, min_duration: float = 0.3) -> list[dict]:
    """G2: 用 ffmpeg blackdetect 扫描视频黑场段，返回 [{start, duration}]。

    供 compose 在 concat 后调用，写 sidecar 观察"分镜之间黑屏"是否仍存在（统一帧率后
    应消失；若仍在，多为 provider 首尾黑帧）。纯诊断，不 trim，不阻断。失败返 []。
    """
    import re

    try:
        proc = subprocess.run(
            [
                "ffmpeg", "-hide_banner", "-i", str(video),
                "-vf", f"blackdetect=d={min_duration}:pix_th=0.10",
                "-an", "-f", "null", "-",
            ],
            capture_output=True, text=True, timeout=120,
        )
    except Exception:
        return []
    scenes: list[dict] = []
    for line in (proc.stderr or "").splitlines():
        m = re.search(r"black_start:([\d.]+).*black_duration:([\d.]+)", line)
        if m:
            scenes.append({"start": float(m.group(1)), "duration": float(m.group(2))})
    return scenes
