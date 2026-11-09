"""Worker pipeline: compose segmented video using scene_groups.

Each scene_group is rendered as a separate segment, then segments are
concatenated with longer inter-group transitions. Supports cache reuse
for fault recovery (skips existing segment/group files).

Pipeline function signature (matches framework):
    fn(task_dir: Path, payload: dict, options: dict, progress_cb: Callable[[int], None]) -> Path
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

from pipelines._placeholder_sidecar import write_placeholder_sidecar
from pipelines._ffmpeg_helpers import (
    _apply_colorgrade_and_subtitles,
    _apply_intro_outro_fade,
    _normalize_loudness,
    _dynamic_concat_timeout,
    _ffmpeg_concat,
    _ffmpeg_concat_with_transitions,
    _ffmpeg_convert_format,
    _ffmpeg_extract_thumbnail,
    _ffmpeg_mix_audio,
    _ffmpeg_mix_bgm,
    _ffmpeg_mix_bgm_with_ducking,
    _finalize_output,
    _get_quality_args,
    _write_av_drift_sidecar,
)
from pipelines.video_gen_mixed import (
    _build_transition_durations,
    _compute_frame_hash,
    _create_fallback_segment,
    _create_text_scene,
    _download_asset,
    _image_to_video,
    _prepare_segment_from_file,
)

logger = logging.getLogger(__name__)


def _prepare_frame_segment(
    frame: dict,
    seg_path: Path,
    *,
    task_dir: Path,
    default_type: str,
    width: int,
    height: int,
    asset_index: int | None = None,
) -> bool:
    """渲染单帧为 segment，失败降级占位。返回「是否用了占位」（N2/N3/N4, Task 12）。

    修前 Step 1 里四个渲染调用**全裸**无 try/except：一个坏素材（损坏 mp4 / 过期
    URL / ffmpeg 崩）异常穿出整个 for → 整个 v3 合成栈崩掉，**已成功的其余帧全部
    白做**。v1（video_gen_ai_visual.py）早有完整逐帧降级，v3 没有。

    这条加固必须早于打开 worker 探测（Task 10）——探测修好后 v2/v3 会从「生产不可
    达的死代码」变成活路径，未加固就放开等于把裸管线推给用户。

    占位本身也失败时才向上抛：那说明连 ffmpeg 兜底都不可用，属基础设施故障，
    继续跑只会产出缺段的坏片。
    """
    vs = frame.get("visual_source") or default_type
    duration = frame.get("duration", 3.0)
    visual_url = frame.get("visual_url")
    visual_type = frame.get("visual_type")
    local_path = frame.get("local_visual_path")
    idx = asset_index if asset_index is not None else frame.get("index", 0)

    try:
        if local_path:
            src = task_dir / local_path if not Path(local_path).is_absolute() else Path(local_path)
            _prepare_local_visual(src, visual_type, seg_path, duration, width, height, frame_index=idx)
        elif visual_url:
            asset = _download_asset(visual_url, task_dir / f"asset_{idx:03d}")
            if visual_type == "image":
                # Task 11：传帧序号驱动 Ken Burns 方向交替（偶数推近/奇数拉远）
                _image_to_video(asset, seg_path, duration, width, height, frame_index=idx)
            else:
                _prepare_segment_from_file(asset, seg_path, duration, width, height)
        elif vs == "remotion":
            _create_text_scene(frame, seg_path, width, height, duration)
        else:
            # 无素材本就是「占位」语义（对齐 v1 口径），计入占位统计
            _create_fallback_segment(frame, seg_path, width, height, duration)
            return True
        if not seg_path.exists():
            raise RuntimeError(f"segment not produced for frame {idx}")
        return False
    except Exception:
        logger.exception(
            "segmented: frame %s render failed; falling back to placeholder segment", idx
        )
        seg_path.unlink(missing_ok=True)
        _create_fallback_segment(frame, seg_path, width, height, duration)
        return True


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


# P0-2 同族：本地图片扩展名——visual_type 缺失时按扩展名判图片分流
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _prepare_local_visual(
    src: Path,
    visual_type: str | None,
    output: Path,
    duration: float,
    width: int,
    height: int,
    frame_index: int = 0,
) -> None:
    """本地视觉统一分流：图片走 _image_to_video（-loop 1 拉满时长），视频走 _prepare_segment_from_file。

    P0-2 同族：_prepare_segment_from_file 无 -loop 1，图片输入产 0.033s 坏段
    （v2_mixed 已实测全片定格；v3 latent 同病，与加固计划 Task 13 的 v2 修复配对）。
    v3 当前因 scene_groups 生产路径不填充而不可达，未来接线即引爆——零成本防御。
    """
    if visual_type == "image" or src.suffix.lower() in _IMAGE_EXTS:
        # Task 11：frame_index 驱动 Ken Burns 方向交替
        _image_to_video(src, output, duration, width, height, frame_index=frame_index)
    else:
        _prepare_segment_from_file(src, output, duration, width, height)


def compose_segmented(
    task_dir: Path,
    payload: dict,
    options: dict,
    progress_cb,
    *,
    lease_remaining: float | None = None,
) -> Path:
    # lease_remaining 本管线暂不使用（无 minterpolate 路径）；与 registry 统一签名。

    """Compose video using per-group segmentation with inter-group transitions."""
    frames = payload.get("frames", [])
    config = payload.get("config", {})
    quality = payload.get("quality", "standard")
    output_format = payload.get("output_format", "mp4")
    scene_groups = payload.get("scene_groups", [])
    # E3: 按 min(frame_indices) 升序排序，保证段落拼接顺序与按帧序拼好的音频/SRT 一致。
    # 根因在上游 gateway 产出 scene_groups 时未保证帧序；此处 worker 侧 defense-in-depth 兜底。
    scene_groups = sorted(scene_groups, key=lambda g: min(g.get("frame_indices", [10**9]), default=10**9))

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

    # Compute global config hash (transition, quality, dimensions — affects all segments)
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

    # Invalidate group outputs that contain dirty frames
    if dirty_indices:
        for gi, group in enumerate(scene_groups):
            group_frame_indices = group.get("frame_indices", [])
            if any(fi in dirty_indices for fi in group_frame_indices):
                group_out = task_dir / f"group_{gi:03d}.mp4"
                group_out.unlink(missing_ok=True)
        # Also invalidate flat concat output
        concat_video = task_dir / "concat_video.mp4"
        concat_video.unlink(missing_ok=True)

    # Step 1: Prepare all frame segments (dirty ones were deleted, others cached)
    total = len(frames)
    placeholder_indices: list[int] = []
    for i, frame in enumerate(frames):
        seg_path = segments_dir / f"seg_{i:03d}.mp4"
        if seg_path.exists():
            continue  # reuse cached segment

        if _prepare_frame_segment(
            frame,
            seg_path,
            task_dir=task_dir,
            default_type=default_type,
            width=width,
            height=height,
            asset_index=i,
        ):
            placeholder_indices.append(frame.get("index", i))

        progress_cb(int(5 + 40 * (i + 1) / total))

    # Task 10: 占位上报覆盖 v3 —— 修前 _write_placeholder_sidecar 只存在于 v1，
    # 而 >1 scene_group 走 v3，即课程视频主路径永远不弹占位告警（全帧黑场也报
    # status=ready/progress=100）。
    #
    # 审查发现 2：touched 必须是**本次真正重渲过的帧**，不能是全部帧。v3 有段
    # 缓存（上面 `if seg_path.exists(): continue`），缓存的黑场帧既不进
    # placeholder_indices、又在 touched 里 → 被合并逻辑当作「本次复核为正常」
    # 剔除。空 dirty 的 recompose 会因此把整个 sidecar 删掉，历史黑场告警全丢。
    # 对齐 v1 传 valid_changed 的写法。
    #
    # 命名空间对齐：dirty_indices 以 enumerate 下标 i 为键，而 placeholder_indices
    # 用 frame["index"]，此处按同一口径映射，否则合并会张冠李戴。
    touched_frame_ids = [
        frames[i].get("index", i) for i in sorted(dirty_indices) if i < len(frames)
    ]
    write_placeholder_sidecar(
        task_dir,
        placeholder_indices,
        total=total,
        touched=touched_frame_ids,
    )
    if placeholder_indices:
        logger.warning(
            "segmented: %d/%d frames fell back to placeholder: %s",
            len(placeholder_indices),
            total,
            placeholder_indices,
        )

    # Step 2: Per-group concat (intra-group)
    group_outputs: list[Path] = []
    total_transitions = 0  # E4: 累计 xfade 转场数，供 av_drift sidecar 元数据
    for gi, group in enumerate(scene_groups):
        frame_indices = group.get("frame_indices", [])
        group_segs = [segments_dir / f"seg_{fi:03d}.mp4" for fi in frame_indices if (segments_dir / f"seg_{fi:03d}.mp4").exists()]
        if len(group_segs) <= 1:
            if group_segs:
                group_outputs.append(group_segs[0])
            continue

        group_out = task_dir / f"group_{gi:03d}.mp4"
        if group_out.exists():
            # cache reuse——仍计入转场数（cache 不改变转场事实）
            g_trans = group.get("transition_in", transition)
            if g_trans != "none" and len(group_segs) > 1:
                total_transitions += len(group_segs) - 1
            group_outputs.append(group_out)
            continue

        group_td = group.get("transition_duration", transition_duration)
        group_transition = group.get("transition_in", transition)
        n_group = len(group_segs)
        has_gtrans = group_transition != "none" and n_group > 1
        if has_gtrans:
            total_transitions += n_group - 1
        # Task 18：组内超时按该组总时长（重编码回落耗时随片长线性增长）。
        group_secs = sum(float(frames[fi].get("duration", 0) or 0) for fi in frame_indices if isinstance(fi, int) and 0 <= fi < len(frames))
        gtimeout = _dynamic_concat_timeout(n_group, has_gtrans, total_seconds=group_secs)

        if has_gtrans:
            _ffmpeg_concat_with_transitions(
                group_segs, group_out,
                transition=group_transition, transition_duration=group_td,
                quality=quality, timeout=gtimeout,
            )
        else:
            _ffmpeg_concat(group_segs, group_out, quality=quality, timeout=gtimeout)
        group_outputs.append(group_out)
        progress_cb(int(45 + 20 * (gi + 1) / len(scene_groups)))

    # If no groups or all groups produced nothing, fall back to flat concat
    if not group_outputs:
        group_outputs = sorted(segments_dir.glob("seg_*.mp4"))

    # Step 3: Inter-group concat (longer transitions)
    video_no_audio = task_dir / "concat_video.mp4"
    n_groups = len(group_outputs)
    has_igroup_trans = transition != "none" and n_groups > 1
    # Task 18：组间超时按全片总时长（组间 concat 处理的是整部片子）。
    total_secs = sum(float(f.get("duration", 0) or 0) for f in frames) if isinstance(frames, list) else 0.0
    itimeout = _dynamic_concat_timeout(n_groups, has_igroup_trans, total_seconds=total_secs)

    if has_igroup_trans:
        # Build inter-group transition durations from scene_groups
        inter_tds = []
        for group in scene_groups[:-1]:
            inter_tds.append(group.get("transition_duration", transition_duration))
        _ffmpeg_concat_with_transitions(
            group_outputs, video_no_audio,
            transition=transition, transition_duration=transition_duration,
            transition_durations=inter_tds if len(inter_tds) >= n_groups - 1 else None,
            quality=quality, timeout=itimeout,
        )
    else:
        _ffmpeg_concat(group_outputs, video_no_audio, quality=quality, timeout=itimeout)
    if has_igroup_trans:
        total_transitions += n_groups - 1
    progress_cb(70)

    # Step 4: Burn subtitles + colorgrade — unified finalizer (was: _burn_subtitles only, no colorgrade/status)
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
    progress_cb(80)

    # Step 5: Global audio mix
    audio_path = payload.get("audio_concat_path")
    # E4: 转场 xfade 致视频 < 音频（Σtd 重叠），probe 写 av_drift.json 供 gateway 回传诚实提示
    if total_transitions > 0 and audio_path and Path(audio_path).exists():
        _write_av_drift_sidecar(video_no_audio, Path(audio_path), task_dir, transition_count=total_transitions)
    if audio_path and Path(audio_path).exists():
        with_audio = task_dir / "with_audio.mp4"
        _ffmpeg_mix_audio(video_no_audio, Path(audio_path), with_audio)
        video_no_audio = with_audio
    progress_cb(90)

    # Step 6: Global BGM (ducking 可配置；与 video_gen_mixed.py 保持一致)
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
    progress_cb(95)

    # Step 6b: intro/outro fade — shared finalizer (was: missing on v3_segmented)
    # C3 (Task 8): 响度归一 —— 必须在全部 amix 之后（否则与 normalize=0 抵消）
    video_no_audio = _normalize_loudness(video_no_audio, task_dir / "loudnorm.mp4")

    video_no_audio = _apply_intro_outro_fade(video_no_audio, tmp_dir=task_dir, quality=quality)

    # Step 7: Format conversion + thumbnail
    output_path = task_dir / f"output.{output_format}"
    final = _ffmpeg_convert_format(video_no_audio, output_path, output_format=output_format, quality=quality)
    _finalize_output(final, output_path)  # B3: 原子覆盖（修 mp4→mp4 短路 output 不创建），与 mixed/ai_visual 一致

    thumb = task_dir / "thumbnail.jpg"
    _ffmpeg_extract_thumbnail(output_path, thumb)

    # Save updated manifest
    _save_manifest(task_dir, {
        "frames": new_manifest_frames,
        "global_hash": new_global_hash,
    })

    progress_cb(100)
    return output_path
