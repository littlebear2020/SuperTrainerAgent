"""Worker pipeline: compose mixed-visual-source video.

Supports frames with different visual types (ai_image, ai_video, remotion)
within a single video. Falls back to text-only segments for remotion frames.

Pipeline function signature (matches framework):
    fn(task_dir: Path, payload: dict, options: dict, progress_cb: Callable[[int], None]) -> Path
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import urllib.request
from pathlib import Path

from pipelines import net_guard
from pipelines._placeholder_sidecar import write_placeholder_sidecar

opener = net_guard.build_guarded_opener()  # 30x 每跳守卫（评审修复轮 Important #2 补接）
from pipelines._ffmpeg_helpers import (
    _apply_colorgrade_and_subtitles,
    _apply_intro_outro_fade,
    _build_zoompan_vf,
    _dynamic_concat_timeout,
    _escape_subtitle_filter_path,
    _ffmpeg_concat,
    _ffmpeg_concat_with_transitions,
    _ffmpeg_convert_format,
    _ffmpeg_extend_clip_to_duration,
    _ffmpeg_extract_thumbnail,
    _ffmpeg_mix_audio,
    _ffmpeg_mix_bgm,
    _ffmpeg_mix_bgm_with_ducking,
    _ffprobe_duration,
    _finalize_output,
    _normalize_loudness,
    _write_av_drift_sidecar,
)

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 120


def _compute_frame_hash(frame: dict) -> str:
    """Compute content hash for a frame to determine if it needs regeneration.

    Parity with video_gen_segmented._compute_frame_hash: ``visual_type`` is included
    so an image↔video swap at the same path slot invalidates the cached segment.
    See that module for the audio_path omission rationale (CAS naming + narration).
    """
    parts = [
        str(frame.get("narration", "")),
        str(frame.get("visual_url", "")),
        str(frame.get("visual_source", "")),
        str(frame.get("visual_type", "")),
        str(frame.get("local_visual_path", "")),
        str(frame.get("duration", 3.0)),
    ]
    return hashlib.md5("|".join(parts).encode()).hexdigest()


def _load_manifest(task_dir: Path) -> dict:
    """Load existing manifest from task_dir, or empty dict if missing."""
    manifest_path = task_dir / "manifest.json"
    if manifest_path.exists():
        try:
            return json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_manifest(task_dir: Path, manifest: dict) -> None:
    """Save manifest to task_dir atomically."""
    manifest_path = task_dir / "manifest.json"
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(manifest_path)

# Unified segment encode params for consistent concat
SEGMENT_ENCODE_ARGS = ["-c:v", "libx264", "-crf", "20", "-preset", "fast", "-pix_fmt", "yuv420p", "-r", "30"]
def _build_transition_durations(
    n_frames: int,
    scene_groups: list[dict],
    default_td: float = 0.5,
) -> list[float]:
    """Build per-transition duration list based on scene_groups.

    Returns n_frames-1 durations. Transitions at group boundaries use
    the group's transition_duration; intra-group transitions use default_td.
    """
    if n_frames <= 1:
        return []
    tds = [default_td] * (n_frames - 1)
    for group in scene_groups:
        group_td = group.get("transition_duration", default_td)
        # The last frame index in this group — the transition FROM this frame
        # to the next group's first frame uses the group's transition_duration
        frame_indices = group.get("frame_indices", [])
        if frame_indices:
            last_idx = max(frame_indices)
            if last_idx < len(tds):
                tds[last_idx] = group_td
    return tds


def compose_mixed(
    task_dir: Path,
    payload: dict,
    options: dict,
    progress_cb,
    *,
    lease_remaining: float | None = None,
) -> Path:
    # lease_remaining: 渲染期剩余租约秒数（worker 传入）——阻塞 subprocess 的
    # timeout 核算用。pipelines 禁读 env，只经调用链透传。
    """Compose video from frames with mixed visual source types."""
    frames = payload.get("frames", [])
    config = payload.get("config", {})
    quality = payload.get("quality", "standard")
    output_format = payload.get("output_format", "mp4")

    if not frames:
        raise ValueError("No frames in payload")

    width = config.get("width", 1080)
    height = config.get("height", 1920)
    transition = config.get("transition", "fade")
    transition_duration = config.get("transition_duration", 0.5)
    default_type = payload.get("video_type", "ai_image")

    segments_dir = task_dir / "segments"
    segments_dir.mkdir(exist_ok=True)

    # --- Manifest-based dirty detection ---
    old_manifest = _load_manifest(task_dir)
    old_frame_hashes = {f["index"]: f.get("segment_hash") for f in old_manifest.get("frames", [])}
    old_global_hash = old_manifest.get("global_hash")

    global_parts = [
        str(config.get("transition", "fade")),
        str(config.get("transition_duration", 0.5)),
        str(width), str(height),
        str(config.get("color_palette", "")),
        str(payload.get("quality", "standard")),
    ]
    new_global_hash = hashlib.md5("|".join(global_parts).encode()).hexdigest()
    global_changed = new_global_hash != old_global_hash

    dirty_indices: set[int] = set()
    new_manifest_frames: list[dict] = []
    for i, frame in enumerate(frames):
        new_hash = _compute_frame_hash(frame)
        old_hash = old_frame_hashes.get(i)
        if new_hash != old_hash or global_changed:
            dirty_indices.add(i)
            seg_path = segments_dir / f"seg_{i:03d}.mp4"
            seg_path.unlink(missing_ok=True)
        new_manifest_frames.append({"index": i, "segment_hash": new_hash})

    # Invalidate flat concat output
    if dirty_indices:
        concat_video = task_dir / "concat_video.mp4"
        concat_video.unlink(missing_ok=True)

    total = len(frames)
    # Task 10: 收集降级为占位的帧。v2 早有逐帧兜底（6 处 _create_fallback_segment），
    # 但**从不上报** —— 占位上报此前只存在于 v1，用户看不到「这片子有多少帧是黑场」。
    placeholder_indices: list[int] = []

    def _fallback(frame_dict: dict, path: Path, dur: float, frame_i: int) -> None:
        _create_fallback_segment(frame_dict, path, width, height, dur)
        placeholder_indices.append(frame_dict.get("index", frame_i))

    for i, frame in enumerate(frames):
        seg_path = segments_dir / f"seg_{i:03d}.mp4"
        if seg_path.exists():
            continue  # reuse cached segment

        vs = frame.get("visual_source") or default_type
        duration = frame.get("duration", 3.0)
        visual_url = frame.get("visual_url")
        visual_type = frame.get("visual_type")
        local_path = frame.get("local_visual_path")

        if local_path:
            src = task_dir / local_path if not Path(local_path).is_absolute() else Path(local_path)
            # M2: 本地图片必须走 _image_to_video（F3 只修了 v1）——_prepare_segment_from_file
            # 是视频专用（无 -loop 1），图片输入产 0.04s 坏段。image_reuse 同按图片处理。
            if visual_type in ("image", "image_reuse") or src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                m_img_ok = True
                try:
                    _image_to_video(src, seg_path, duration, width, height, frame_index=i)
                except Exception:
                    m_img_ok = False
                    logger.warning("local image_to_video failed for frame %d; using fallback", i, exc_info=True)
                if not m_img_ok or not seg_path.exists():
                    _fallback(frame, seg_path, duration, i)
            else:
                try:
                    _prepare_segment_from_file(src, seg_path, duration, width, height, config=config, frame_index=i, lease_remaining=lease_remaining)
                except RuntimeError:
                    # 缺段会让后续帧音画错位——兜底段保时长，不静默跳过
                    logger.warning("local segment-from-file failed for frame %d; using fallback", i, exc_info=True)
                    _fallback(frame, seg_path, duration, i)
        elif visual_url:
            asset = _download_asset(visual_url, task_dir / f"asset_{i:03d}")
            # M2 补齐：image_reuse 同按图片路由（此前只修了 local 分支）——本地化失败
            # 的复用帧只剩远程 URL，误入视频路径（无 -loop 1）会产 0.04s 坏段。
            if visual_type in ("image", "image_reuse"):
                _image_to_video(asset, seg_path, duration, width, height, frame_index=i)
                # 与 local 分支对齐：E17 双重失败后缺段会让后续帧音画错位，兜底保时长
                if not seg_path.exists():
                    _fallback(frame, seg_path, duration, i)
            else:
                _prepare_segment_from_file(asset, seg_path, duration, width, height, config=config, frame_index=i, lease_remaining=lease_remaining)
        elif vs == "remotion":
            _create_text_scene(frame, seg_path, width, height, duration)
        else:
            _fallback(frame, seg_path, duration, i)

        progress_cb(int(10 + 60 * (i + 1) / total))

    # Task 10: 上报占位帧（v2 此前完全不报）
    write_placeholder_sidecar(
        task_dir,
        placeholder_indices,
        total=total,
        touched=[f.get("index", i) for i, f in enumerate(frames)],
    )
    if placeholder_indices:
        logger.warning("mixed: %d/%d frames fell back to placeholder: %s", len(placeholder_indices), total, placeholder_indices)

    # Concat all segments
    segments = sorted(segments_dir.glob("seg_*.mp4"))
    n_frames = len(segments)
    has_transitions = transition != "none" and n_frames > 1
    # Task 18：超时按总时长而非镜数（重编码回落耗时随片长线性增长）。
    total_secs = sum(float(f.get("duration", 0) or 0) for f in frames) if isinstance(frames, list) else 0.0
    concat_timeout = _dynamic_concat_timeout(n_frames, has_transitions, total_seconds=total_secs)

    # Build per-transition durations from scene_groups
    scene_groups = payload.get("scene_groups", [])
    transition_durations = _build_transition_durations(n_frames, scene_groups, default_td=transition_duration) if scene_groups else None

    video_no_audio = task_dir / "concat_video.mp4"
    if has_transitions:
        _ffmpeg_concat_with_transitions(
            segments, video_no_audio,
            transition=transition, transition_duration=transition_duration,
            transition_durations=transition_durations,
            quality=quality, timeout=concat_timeout,
        )
    else:
        _ffmpeg_concat(segments, video_no_audio, quality=quality, timeout=concat_timeout)
    progress_cb(75)

    # Burn subtitles + colorgrade — unified finalizer (was: inline _burn_subtitles, no colorgrade, no status)
    subtitle_path = payload.get("subtitle_path")
    color_palette = config.get("color_palette")
    video_no_audio = _apply_colorgrade_and_subtitles(
        video_no_audio,
        task_dir=task_dir,
        tmp_dir=task_dir,
        color_palette=color_palette,
        subtitle_path=subtitle_path,
        subtitle_style=config.get("subtitle_style", "default"),
        subtitle_ass=payload.get("subtitle_ass"),
    )
    progress_cb(85)

    # Mix audio
    audio_path = payload.get("audio_concat_path")
    # E4: 转场 xfade 致视频 < 音频（Σtd 重叠），probe 写 av_drift.json 供 gateway 回传诚实提示
    if has_transitions and audio_path and Path(audio_path).exists():
        _write_av_drift_sidecar(video_no_audio, Path(audio_path), task_dir, transition_count=n_frames - 1)
    if audio_path and Path(audio_path).exists():
        with_audio = task_dir / "with_audio.mp4"
        _ffmpeg_mix_audio(video_no_audio, Path(audio_path), with_audio)
        video_no_audio = with_audio
    progress_cb(90)

    # BGM with ducking
    bgm_path = config.get("bgm_path")
    bgm_volume = config.get("bgm_volume", 0.15)
    bgm_ducking = config.get("bgm_ducking", True)
    if bgm_path and Path(bgm_path).exists():
        with_bgm = task_dir / "with_bgm.mp4"
        if bgm_ducking:
            _ffmpeg_mix_bgm_with_ducking(video_no_audio, Path(bgm_path), with_bgm, bgm_volume=bgm_volume)
        else:
            _ffmpeg_mix_bgm(video_no_audio, Path(bgm_path), with_bgm, bgm_volume=bgm_volume)
        video_no_audio = with_bgm
    progress_cb(92)

    # C3 (Task 8): 响度归一 —— 必须在全部 amix 之后（否则与 normalize=0 抵消）
    video_no_audio = _normalize_loudness(video_no_audio, task_dir / "loudnorm.mp4")

    # Task 3g: intro/outro fade — shared finalizer (was: missing on v2_mixed)
    video_no_audio = _apply_intro_outro_fade(video_no_audio, tmp_dir=task_dir, quality=quality)
    progress_cb(95)

    # Format conversion
    output_path = task_dir / f"output.{output_format}"
    final = _ffmpeg_convert_format(video_no_audio, output_path, output_format=output_format, quality=quality)
    _finalize_output(final, output_path)

    # Thumbnail
    thumb = task_dir / "thumbnail.jpg"
    _ffmpeg_extract_thumbnail(output_path, thumb)

    # Save updated manifest
    _save_manifest(task_dir, {
        "frames": new_manifest_frames,
        "global_hash": new_global_hash,
    })

    progress_cb(100)
    return output_path


def _download_asset(url: str, dest_stem: Path) -> Path:
    """Download a visual asset from URL."""
    if "?" in url:
        ext = ".mp4" if "video" in url.lower() else ".jpg"
    else:
        ext = Path(url).suffix or ".jpg"
    dest = dest_stem.with_suffix(ext)
    dest.parent.mkdir(parents=True, exist_ok=True)
    net_guard.assert_public_http_url(url)  # 评审修复轮：第三下载器补接守卫
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    limit = net_guard.max_asset_bytes()
    with opener.open(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
        buf = bytearray()
        while True:
            chunk = resp.read(1 << 20)
            if not chunk:
                break
            buf += chunk
            if len(buf) > limit:
                raise ValueError(f"download exceeds REMOTION_ASSET_MAX_BYTES ({limit}B)")
        dest.write_bytes(bytes(buf))
    return dest


def _image_to_video(image_path: Path, output: Path, duration: float, width: int, height: int, frame_index: int = 0) -> None:
    """Convert image to video with Ken Burns effect.

    2026-08-03 Task 11：本函数原先 docstring 承诺 Ken Burns 但滤镜链只有
    scale/crop/format（文档说谎，静态图在 mixed/segmented 两条主力管线里毫无运动）。
    现复用 ``_ffmpeg_helpers._build_zoompan_vf`` —— 与 v1 (video_gen_ai_visual) 同一
    实现，4x 过采样 + 归一化速率 + 方向交替。
    注意 zoompan 不可用 crop 替代：ffmpeg 8.1.2 上 crop 的 w/h 仅初始化求值一次
    （helpers 里 _ZOOM_OVERSAMPLE 注释记录的实测结论）。

    ``frame_index`` 驱动方向交替（偶数推近/奇数拉远），不传则全片同向。
    失败回落到原静态滤镜链——运动是增强项，不该因 zoompan 失败而丢掉整段画面。
    """
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
        "-t", str(duration),
        "-vf", _build_zoompan_vf(width=width, height=height, duration=duration, frame_index=frame_index),
        *SEGMENT_ENCODE_ARGS,
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        # Fallback: static image
        cmd_fallback = [
            "ffmpeg", "-y", "-loop", "1", "-i", str(image_path),
            "-t", str(duration),
            "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
            *SEGMENT_ENCODE_ARGS,
            str(output),
        ]
        fb = subprocess.run(cmd_fallback, capture_output=True, timeout=120)
        # E17: fallback 也失败须告警（不静默产空/缺段，否则 concat 音画不等长无日志）
        if fb.returncode != 0 or not output.exists():
            logger.warning(
                "image_to_video fallback failed for %s -> %s (rc=%d, stderr=%s)",
                image_path,
                output,
                fb.returncode,
                (fb.stderr or b"")[:200],
            )


def _prepare_segment_from_file(src: Path, output: Path, duration: float, width: int, height: int, config: dict | None = None, frame_index: int = 0, lease_remaining: float | None = None) -> None:
    """Normalize a video file to unified segment params.

    B1: 源短于目标时长（ai_video 单镜常 < 旁白时长）→ 先循环补长到 duration，
    再 -t 截断 + scale/crop，避免段短于旁白导致"讲一半结束"。v1 用 _ffmpeg_extend_clip_to_duration，
    v2/v3 之前只 -t 截不补。
    """
    actual_src = src
    cleanup: Path | None = None
    try:
        src_dur = _ffprobe_duration(src)
    except (ValueError, subprocess.SubprocessError):
        # N9: 探不到时长时按「未知」保守补长，不得假设 src_dur == duration。
        # 原来赋 duration 会让下面的判据恒 False —— 而 probe 失败往往正说明这
        # 是个坏片/截断片，是**最该**补长的一类，结果恰好被跳过。
        logger.warning("probe duration failed for %s; padding conservatively to %.2fs", src, duration)
        src_dur = 0.0
    if src_dur + 0.05 < duration:
        extended = output.parent / f"_ext_{output.name}"
        try:
            _ffmpeg_extend_clip_to_duration(src, duration, extended, config=config, frame_index=frame_index, lease_remaining=lease_remaining)
            actual_src = extended
            cleanup = extended
        except RuntimeError:
            logger.warning("extend clip to %.1fs failed for %s; using as-is", duration, src)

    cmd = [
        "ffmpeg", "-y", "-i", str(actual_src),
        "-t", str(duration),
        "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
        *SEGMENT_ENCODE_ARGS,
        "-an",
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if cleanup is not None:
        cleanup.unlink(missing_ok=True)
    # M2: rc 必查——修复前静默产坏段/缺段，后续帧音画错位无日志。
    if proc.returncode != 0 or not output.exists():
        raise RuntimeError(f"segment-from-file failed for {src} (rc={proc.returncode})")


def _create_text_scene(frame: dict, output: Path, width: int, height: int, duration: float) -> None:
    """Create a text-only scene (remotion fallback) using ffmpeg drawtext."""
    narration = frame.get("narration", "")
    # Escape special chars for drawtext
    text = narration.replace(":", r"\:").replace("'", r"\'").replace("%", r"\%")
    font_size = min(48, max(24, width // max(len(narration), 1) * 2))

    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#0f172a:s={width}x{height}:d={duration}:r=30",
        "-vf",
        f"drawtext=text='{text}':"
        f"font='Noto Sans CJK SC':"
        f"fontsize={font_size}:fontcolor=white:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"alpha='if(lt(t,0.5),t/0.5,if(gt(t,{max(0, duration - 0.5):.1f}),({duration:.1f}-t)/0.5,1))'",
        *SEGMENT_ENCODE_ARGS,
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=60)
    if proc.returncode != 0:
        # Fallback: solid color without text
        logger.warning("drawtext failed, creating solid color fallback")
        cmd_fallback = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#0f172a:s={width}x{height}:d={duration}:r=30",
            *SEGMENT_ENCODE_ARGS,
            str(output),
        ]
        subprocess.run(cmd_fallback, capture_output=True, timeout=30)


def _create_fallback_segment(frame: dict, output: Path, width: int, height: int, duration: float) -> None:
    """Fallback: solid color background for frames without visual assets."""
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", f"color=c=#1a1a2e:s={width}x{height}:d={duration}:r=30",
        *SEGMENT_ENCODE_ARGS,
        str(output),
    ]
    subprocess.run(cmd, capture_output=True, timeout=30)

