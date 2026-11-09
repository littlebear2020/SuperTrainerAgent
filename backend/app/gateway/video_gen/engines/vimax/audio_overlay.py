"""ViMax finished-mode audio overlay (阶段 2.5, plan §7.5).

ViMax produces a SILENT video; :func:`overlay_audio_track` mixes the TTS audio
(+ optional ducked BGM) onto it via ffmpeg, and (split bf7b75a2 + B6c) burns
``subtitle.srt`` when ``burn_subtitles=True``——时序已由 narration_generator
``_align_chunk_to_shot`` 对齐，desync 顾虑已过时（旧 docstring 称不烧字幕）。
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

from app.gateway._shared.executors import run_ffmpeg

logger = logging.getLogger(__name__)


def srt_has_cues(srt: Path) -> bool:
    """字幕文件是否含至少一个有效 cue。

    SRT 检测 ``-->`` 时间轴行；ASS 检测 ``Dialogue:`` 事件行（N1 修复）。
    libass 对空/无 cue 的字幕报 "Unable to open" → subtitles 滤镜 init 失败
    （AVERROR_INVALIDDATA），整个任务在 99% 处崩（服务器 vg_698c123a8ddd 实证）。
    """
    try:
        content = srt.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if str(srt).lower().endswith(".ass"):
        return "Dialogue:" in content
    return "-->" in content


def ship_soft_subtitle(task_dir: Path, video_path: Path | None = None) -> Path | None:
    """烧录失败/时序未对齐兜底：把 subtitle.srt 复制为与视频同名的 .srt（播放器自动挂载）。

    绕开烧录——不烧进视频，外挂 .srt 由播放器按自身时序渲染。
    ``video_path`` 决定目标 basename（默认 final_video.mp4 → final_video.srt，
    保持 Phase 3m 旧行为）；vimax 成品是 vimax_final.mp4 → vimax_final.srt。
    """
    import shutil

    srt = task_dir / "subtitle.srt"
    if not srt.exists():
        return None
    video_name = video_path.name if video_path is not None else "final_video.mp4"
    dest = task_dir / (Path(video_name).stem + ".srt")
    try:
        shutil.copy2(srt, dest)
        return dest
    except OSError:
        return None


def build_overlay_cmd(
    video: Path,
    audio: Path,
    bgm: Path | None,
    output: Path,
    *,
    subtitle_path: Path | None = None,
    burn_subtitles: bool = False,
    subtitle_style: str = "default",
    bgm_volume: float = 0.15,
) -> list[str]:
    """ffmpeg command: lay TTS audio (+ ducked BGM) over ViMax's silent video.

    Audio shorter than video is padded with silence (``apad``) so the output
    preserves the FULL video duration — previously ``-shortest`` truncated the
    video to the (much shorter) TrainerAI audio length (22s out of 222s).
    ``-shortest`` is kept: apad makes audio infinite → -shortest picks video.

    BGM：``-stream_loop -1`` 循环（短 BGM 曾放一遍即停，后半程无 BGM）；
    音量用 ``bgm_volume``（曾硬编码 0.15，不吃 storyboard 配置）。

    字幕（HR-T3）：Task1（c9c6eb77）已把对白镜 TTS chunk 逐镜对齐到 5.16s，
    消除累积漂移→时序对齐→burn_subtitles=True 且 subtitle_path 存在时用
    subtitles= filter 烧进视频。默认关（config.vimax_burn_subtitles 灰度，待 A/B）。
    """
    cmd: list[str] = ["ffmpeg", "-y", "-i", str(video), "-i", str(audio)]
    if bgm is not None:
        cmd += ["-stream_loop", "-1", "-i", str(bgm)]  # 短 BGM 循环（曾放一遍即停，后半程无 BGM）
    vf_parts: list[str] = []

    # T7/P1-3: 优先烧 .ass（自带 PlayRes/WrapStyle/FontSize），回退 .srt + force_style
    ass_path = None
    if subtitle_path is not None:
        candidate_ass = subtitle_path.with_suffix(".ass")
        if candidate_ass.exists():
            ass_path = candidate_ass

    effective_sub = ass_path if ass_path is not None else subtitle_path
    if burn_subtitles and effective_sub is not None and effective_sub.exists() and not srt_has_cues(effective_sub):
        logger.warning("ViMax overlay: %s 无有效字幕 cue，跳过烧录（任务继续，成品无字幕）", effective_sub)
    if burn_subtitles and effective_sub is not None and effective_sub.exists() and srt_has_cues(effective_sub):
        # B6c: force_style + fontsdir 兜底（镜像 worker _subtitle_filter_vf）
        # T7: subtitle_filter_vf 已对 .ass 跳过 force_style（样式 baked）
        from app.gateway.video_gen.subtitle_filter import subtitle_filter_vf

        vf_parts.append(subtitle_filter_vf(effective_sub, subtitle_style))
    if bgm is not None:
        # apad on voice → infinite; amix duration=first (voice=infinite);
        # -shortest caps output to video length. BGM -stream_loop -1 → infinite too.
        fc = f"[1:a]volume=1.0,apad[voice];[2:a]volume={bgm_volume}[bgm];[voice][bgm]amix=inputs=2:duration=first:normalize=0[a]"
        cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]"]
    else:
        # apad on audio → infinite; -shortest caps output to video length.
        cmd += ["-filter_complex", "[1:a]apad[a]", "-map", "0:v", "-map", "[a]"]
    if vf_parts:
        cmd += ["-vf", ",".join(vf_parts)]
        # 烧录必须重编码；限线程+preset 防 gateway 满核跑分钟级 x264（渲染窗口主机过载嫌疑）。
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-threads", "4"]
    else:
        # 无视频滤镜 → 视频流直接 copy（秒级），只对音频重编码。
        cmd += ["-c:v", "copy"]
    cmd += ["-c:a", "aac", "-shortest", str(output)]
    return cmd


def build_loudnorm_cmd(
    video: Path,
    output: Path,
    *,
    target_i: float = -16.0,
    target_tp: float = -1.5,
    target_lra: float = 11.0,
) -> list[str]:
    """成品整轨响度归一（P2-13），与 worker 三管线 ``_normalize_loudness`` 同参数同位置。

    修前只有 worker 三管线接了 loudnorm，ViMax 直渲产物音量随 TTS 供应商/BGM 有无
    忽大忽小。必须在混音（amix）**之后**对整轨单 pass 一次——在 amix 前逐路做会与
    amix 的 normalize=0 相互抵消。``-c:v copy`` 只重编码音频，秒级完成。
    """
    return [
        "ffmpeg",
        "-y",
        "-i",
        str(video),
        "-af",
        f"loudnorm=I={target_i}:TP={target_tp}:LRA={target_lra}",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(output),
    ]


def _run_cmd(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, timeout=600)
    if proc.returncode != 0:
        # 2000：libass 初始化段（"Unable to open ..." 等决定性行）在 ffmpeg 报错行之前，
        # 500 字节截断恰好把它切掉（vg_698c123a8ddd 现场实证）。
        raise RuntimeError(f"vimax audio overlay failed: {proc.stderr[-2000:]!r}")


async def overlay_audio_track(
    video_path: Path,
    task_dir: Path,
    *,
    audio_override: Path | None = None,
    burn_subtitles: bool = False,
    subtitle_style: str = "default",
) -> Path:
    """Overlay audio (+ optional ducked BGM) onto ViMax's silent video.

    ``audio_override`` (Phase 3): when provided, use this per-shot narration audio
    instead of ``task_dir/audio.mp3``. The narration matches the video length.

    HR-T3: burn_subtitles=True 且 task_dir/subtitle.srt 存在时烧字幕（时序已对齐）。

    Returns the overlaid mp4 path, or the input video unchanged when no audio was
    prepared (draft-only / no-TTS path) — so callers can use it unconditionally.
    """
    audio = audio_override or (task_dir / "audio.mp3")
    if not audio.exists():
        # HR-T5: 缺失可见化——静默返回原视频会让"无配音"难以诊断（narration/TTS 静默失败）。
        logger.warning(
            "ViMax overlay: %s 不存在，返回静默视频（检查 narration/TTS 生成是否失败）",
            audio,
        )
        return video_path
    bgm = task_dir / "bgm.mp3"
    bgm_volume = 0.15
    sb_file = task_dir / "storyboard.json"
    if bgm.exists() and sb_file.exists():
        try:
            bgm_volume = float(json.loads(sb_file.read_text(encoding="utf-8")).get("config", {}).get("bgm_volume", 0.15))
        except Exception:
            logger.warning("failed to read bgm_volume from storyboard.json; using default 0.15")
    output = task_dir / "vimax_final.mp4"
    srt = task_dir / "subtitle.srt"
    srt_size = srt.stat().st_size if srt.exists() else -1
    logger.info(
        "ViMax overlay: video=%s audio=%s srt=%s(size=%d) burn=%s style=%s",
        video_path.name,
        audio.name,
        srt.name,
        srt_size,
        burn_subtitles,
        subtitle_style,
    )
    cmd = build_overlay_cmd(
        video_path,
        audio,
        bgm if bgm.exists() else None,
        output,
        subtitle_path=srt if srt.exists() else None,
        burn_subtitles=burn_subtitles,
        subtitle_style=subtitle_style,
        bgm_volume=bgm_volume,
    )
    soft_shipped = False
    try:
        await run_ffmpeg(_run_cmd, cmd)
    except RuntimeError:
        if not burn_subtitles:
            raise
        # 优雅降级：字幕烧录失败（libass 环境问题/异常 SRT）不应让渲染了几十分钟的
        # 任务在 99% 处整个失败 —— 重试无烧录版本 + 软字幕兜底（vg_698c123a8ddd）。
        logger.warning(
            "ViMax overlay 字幕烧录失败，降级为无烧录重试 + 软字幕（task_dir=%s）",
            task_dir,
            exc_info=True,
        )
        fallback_cmd = build_overlay_cmd(
            video_path,
            audio,
            bgm if bgm.exists() else None,
            output,
            subtitle_path=None,
            burn_subtitles=False,
            bgm_volume=bgm_volume,
        )
        await run_ffmpeg(_run_cmd, fallback_cmd)  # 再失败说明非字幕问题 → 抛出
        soft_shipped = ship_soft_subtitle(task_dir, output) is not None

    # P2-13：混音完成后对成品整轨做响度归一（与 worker 三管线同标准）。
    # 失败/产物缺失 → 回落未归一化产物，响度是锦上添花，绝不阻断出片。
    normalized = task_dir / "vimax_final_loudnorm.mp4"
    try:
        await run_ffmpeg(_run_cmd, build_loudnorm_cmd(output, normalized))
    except RuntimeError:
        logger.warning("ViMax loudnorm 失败，交付未归一化产物（task_dir=%s）", task_dir, exc_info=True)
        return output
    if normalized.exists() and normalized.stat().st_size > 0:
        deliverable = normalized
    else:
        logger.warning("ViMax loudnorm 产物缺失/空，交付未归一化产物（task_dir=%s）", task_dir)
        deliverable = output
    # T3（Lane 2）：本次降级发过软字幕且交付物改名（loudnorm 产物）→ 外挂字幕
    # basename 跟随最终交付物，旧的中间产物名 sidecar 同步清理——修前挂在
    # vimax_final.mp4 上，播放器对 vimax_final_loudnorm.mp4 不再自动挂载。
    if soft_shipped and deliverable != output:
        old_sidecar = task_dir / (output.stem + ".srt")
        if ship_soft_subtitle(task_dir, deliverable):
            old_sidecar.unlink(missing_ok=True)
            logger.info("ViMax 软字幕 basename 已跟随交付物 %s", deliverable.name)
    return deliverable
