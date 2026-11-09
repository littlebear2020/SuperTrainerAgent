"""Worker-side ffmpeg pipeline for PPT-to-video composition.

Extracted from backend/app/gateway/voice_twin/production_service.py.
This module runs inside the production-worker container (python:3.12-slim + ffmpeg).
"""

import __future__

import logging
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

# 对齐 worker 固定租约（worker.py:27 LEASE_DURATION）。pipeline 不 import worker，
# 直接读同一 env，解耦且与 worker 默认值（1800）一致。
_LEASE_DURATION = int(os.environ.get("LEASE_DURATION", "1800"))


# ---------------------------------------------------------------------------
# ffmpeg helpers
# ---------------------------------------------------------------------------


def _ffmpeg_err(stderr_bytes: bytes, prefix: str) -> str:
    """Extract useful error lines from ffmpeg stderr output."""
    raw = stderr_bytes.decode(errors="replace")
    # Filter lines containing error indicators
    err_lines = [line.strip() for line in raw.splitlines() if any(kw in line.lower() for kw in ("error", "invalid", "cannot", "no such", "not found", "failed"))]
    if err_lines:
        return f"{prefix}: {'; '.join(err_lines)}"
    # Fallback: last 500 chars (skip the banner)
    return f"{prefix}: ...{raw[-500:]}"


def _run_ffmpeg(cmd: list[str], timeout: int, friendly_timeout_msg: str) -> subprocess.CompletedProcess:
    """运行 ffmpeg；超时转中文 RuntimeError（进 DB/前端），完整异常链仍由 worker 记入 _failure_context.txt。

    ⚠️ 必须 ``raise ... from exc``——``from None`` 会抑制异常链，丢失 TimeoutExpired
    携带的完整 ffmpeg 命令和 timeout 值（_write_failure_context 用 traceback.format_exception）。
    """
    t0 = time.monotonic()
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=timeout)
        elapsed = time.monotonic() - t0
        logger.info("ffmpeg stage elapsed=%.1fs timeout=%d rc=%d", elapsed, timeout, result.returncode)
        return result
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - t0
        logger.warning("ffmpeg stage TIMEOUT elapsed=%.1fs timeout=%d", elapsed, timeout)
        raise RuntimeError(friendly_timeout_msg) from exc


def _build_zoompan_filter(total_frames: int) -> str:
    """Build a static zoompan filter — converts image to video of correct duration."""
    return f"zoompan=z='1.0':x='0':y='0':d={total_frames}:s=1920x1080:fps=30"


def _ffmpeg_slide_to_video(
    image_path: Path,
    audio_path: Path,
    output_path: Path,
) -> None:
    """Generate a static video segment from a slide image + audio."""
    # Get audio duration to set video length
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(audio_path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    duration = float(probe.stdout.strip())

    total_frames = int(duration * 30)
    vf = _build_zoompan_filter(total_frames)

    cmd = [
        "ffmpeg",
        "-y",
        "-loop",
        "1",
        "-i",
        str(image_path),
        "-i",
        str(audio_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        "-shortest",
        str(output_path),
    ]
    logger.debug("ffmpeg slide-to-video cmd: %s", " ".join(cmd))
    seg_timeout = min(max(120, int(duration * 2)), _LEASE_DURATION - 300)
    proc = _run_ffmpeg(
        cmd,
        timeout=seg_timeout,
        friendly_timeout_msg=f"幻灯片视频生成超时（限 {seg_timeout} 秒，音频时长约 {duration:.0f} 秒）。请减少页数或缩短每页讲解。",
    )
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg slide-to-video failed"))


def _ffmpeg_concat_video(segment_paths: list[Path], output_path: Path) -> None:
    """Concatenate video segments using ffmpeg concat demuxer."""
    missing = [str(p) for p in segment_paths if not p.exists()]
    if missing:
        raise RuntimeError(f"Missing segment files: {missing}")

    # Write filelist in the segments directory with relative filenames
    seg_dir = segment_paths[0].parent
    filelist = seg_dir / "filelist.txt"
    try:
        with open(filelist, "w") as f:
            for p in segment_paths:
                f.write(f"file '{p.name}'\n")

        proc = _run_ffmpeg(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(filelist), "-c", "copy", str(output_path)],
            timeout=300,
            friendly_timeout_msg="视频拼接超时（限 300 秒）。请尝试减少页数。",
        )
        if proc.returncode != 0:
            raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg video concat failed"))
    finally:
        if filelist.exists():
            filelist.unlink()


def _ffmpeg_xfade(
    segment_paths: list[Path],
    output_path: Path,
    transition: str = "fade",
    duration: float = 0.5,
    progress_callback=None,
) -> None:
    """Concatenate video segments with xfade transition + acrossfade for audio."""
    n = len(segment_paths)
    if n < 2:
        raise ValueError("xfade requires at least 2 segments")

    # Get durations for each segment
    durations: list[float] = []
    for p in segment_paths:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        durations.append(float(probe.stdout.strip()))

    # Guard: if any segment is shorter than the transition duration, fall back
    if any(d < duration * 2 for d in durations):
        logger.warning(
            "One or more segments shorter than 2x transition (%.1fs); falling back to concat demuxer",
            duration,
        )
        _ffmpeg_concat_video(segment_paths, output_path)
        return

    # For long videos (>20 segments), batch in groups of 10
    if n > 20:
        _ffmpeg_xfade_batched(segment_paths, output_path, transition, duration, durations, progress_callback=progress_callback)
        return

    _ffmpeg_xfade_with_durations(segment_paths, output_path, transition, duration, durations)


def _ffmpeg_xfade_batched(
    segment_paths: list[Path],
    output_path: Path,
    transition: str,
    duration: float,
    durations: list[float],
    progress_callback=None,
) -> None:
    """For >20 segments: batch xfade in groups, then concat the batches."""
    batch_size = 10
    batch_outputs: list[Path] = []
    work_dir = output_path.parent / "_xfade_batches"
    work_dir.mkdir(exist_ok=True)

    try:
        for b in range(0, len(segment_paths), batch_size):
            batch = segment_paths[b : b + batch_size]
            batch_dur = durations[b : b + batch_size]
            batch_out = work_dir / f"batch_{b // batch_size:03d}.mp4"

            if len(batch) < 2:
                shutil.copy2(batch[0], batch_out)
                batch_outputs.append(batch_out)
            else:
                _ffmpeg_xfade_with_durations(batch, batch_out, transition, duration, batch_dur)
                batch_outputs.append(batch_out)

            # 批间续租：每个 batch 完成后（不论 xfade 还是 copy）续一次，
            # compose_ppt_video 在进入 xfade 前已调一次 progress_callback(95) 刷新租约到满额，
            # 此处每完成一个 batch 再续一次，确保下一个 batch 有足够租约余量。
            if progress_callback is not None:
                progress_callback(95)

        # Final concat of all batches
        _ffmpeg_concat_video(batch_outputs, output_path)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _ffmpeg_xfade_with_durations(
    segment_paths: list[Path],
    output_path: Path,
    transition: str,
    duration: float,
    durations: list[float],
) -> None:
    """xfade helper with pre-computed durations."""
    n = len(segment_paths)

    # Guard: fall back to concat if any segment is too short
    if any(d < duration * 2 for d in durations):
        _ffmpeg_concat_video(segment_paths, output_path)
        return

    inputs: list[str] = []
    for p in segment_paths:
        inputs.extend(["-i", str(p)])

    offsets: list[float] = []
    cumulative = 0.0
    for i in range(n - 1):
        offsets.append(cumulative + durations[i] - duration)
        cumulative += durations[i] - duration

    vfilters: list[str] = []
    afilters: list[str] = []
    prev_v = "[0:v]"
    prev_a = "[0:a]"

    for i in range(n - 1):
        out_v = f"[v{i}]" if i < n - 2 else "[vout]"
        out_a = f"[a{i}]" if i < n - 2 else "[aout]"
        vfilters.append(f"{prev_v}[{i + 1}:v]xfade=transition={transition}:duration={duration}:offset={offsets[i]:.3f}{out_v}")
        afilters.append(f"{prev_a}[{i + 1}:a]acrossfade=d={duration}{out_a}")
        prev_v = out_v
        prev_a = out_a

    filter_complex = ";".join(vfilters + afilters)
    cmd = [
        "ffmpeg",
        "-y",
        *inputs,
        "-filter_complex",
        filter_complex,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",
        "-c:a",
        "aac",
        "-pix_fmt",
        "yuv420p",
        str(output_path),
    ]
    total_dur = sum(durations)
    raw_timeout = max(600, int(n * 120 + total_dur * 0.5))
    # 上限 = 租约 − 300s 缓冲：xfade 是单阻塞 subprocess，运行期间无法续租，
    # 必须在租约过期前结束或友好超时（配合④），否则触发 recover_stale_tasks 静默重排。
    xfade_timeout = min(raw_timeout, _LEASE_DURATION - 300)
    proc = _run_ffmpeg(
        cmd,
        timeout=xfade_timeout,
        friendly_timeout_msg=f"视频转场合成超时（限 {xfade_timeout} 秒，{n} 段、总时长约 {total_dur:.0f} 秒）。可在「预设动画」选「无」规避，或减少页数/缩短每页讲解。",
    )
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "ffmpeg xfade failed"))


# ---------------------------------------------------------------------------
# Subtitle helpers (SRT-based, replaces legacy drawtext approach)
# ---------------------------------------------------------------------------


def _probe_duration(path: Path) -> float:
    """Get media file duration via ffprobe."""
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(probe.stdout.strip())


def _split_sentences(text: str) -> list[str]:
    """Split narration text into sentences at Chinese/English sentence-end punctuation."""
    parts = re.split(r"(?<=[。！？；!?])", text)
    sentences = [p.strip() for p in parts if p.strip()]
    if not sentences:
        sentences = [text] if text.strip() else []
    return sentences


def _srt_time(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _generate_srt_for_slide(
    sentences: list[str],
    audio_duration: float,
    output_path: Path,
    line_max_chars: int = 24,
) -> None:
    """Generate SRT for a single slide, splitting long sentences into subtitle lines."""
    if not sentences:
        return

    total_chars = sum(len(s) for s in sentences)
    if total_chars == 0:
        return

    lines: list[str] = []
    idx = 1
    current_time = 0.0

    for sent in sentences:
        # Split long sentences into multiple subtitle entries
        if len(sent) > line_max_chars:
            chunks = [sent[i : i + line_max_chars] for i in range(0, len(sent), line_max_chars)]
        else:
            chunks = [sent]

        chars_per_chunk = len(sent) / len(chunks)
        duration_per_chunk = (chars_per_chunk / total_chars) * audio_duration

        for chunk in chunks:
            start = current_time
            end = current_time + duration_per_chunk
            lines.append(str(idx))
            lines.append(f"{_srt_time(start)} --> {_srt_time(end)}")
            lines.append(chunk)
            lines.append("")
            idx += 1
            current_time = end

    output_path.write_text("\n".join(lines), encoding="utf-8")


def _escape_filter_path(path: str) -> str:
    """转义 ffmpeg filtergraph 路径。委托给 pipelines._ffmpeg_helpers 的实现，
    消除同名反义的三副本分歧（本版此前不加引号，含 [ ] 的路径会
    `Trailing garbage after a filter`；空格实测无害，勿据此判 P0）。"""
    from pipelines._ffmpeg_helpers import _escape_subtitle_filter_path

    return _escape_subtitle_filter_path(path)


def _ffmpeg_burn_srt(video_path: Path, srt_path: Path, output_path: Path, task_dir: Path | None = None) -> None:
    """Burn SRT subtitles into video using ffmpeg subtitles filter.

    Tries subtitles filter first (needs libass). Falls back to drawtext on failure.
    P0-6: On fallback, writes subtitle_status=failed if task_dir is provided.
    """
    escaped = _escape_filter_path(str(srt_path))
    is_ass = str(srt_path).lower().endswith(".ass")
    # T9/P1-5: .ass 自带样式（PlayRes/FontSize/WrapStyle），不得用 force_style 覆盖
    if not is_ass:
        force_style_escaped = (
            "FontSize=24\\,PrimaryColour=&H00FFFFFF\\,OutlineColour=&H00000000"
            "\\,Outline=2\\,Alignment=2\\,MarginV=30"
        )
        vf = f"subtitles={escaped}:force_style={force_style_escaped}"
    else:
        vf = f"subtitles={escaped}"
    cmd = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(output_path)]
    proc = _run_ffmpeg(cmd, timeout=300, friendly_timeout_msg="字幕烧录超时（限 300 秒）。可尝试关闭「显示字幕」规避。")
    if proc.returncode == 0:
        return
    logger.warning("subtitles filter failed: %s", _ffmpeg_err(proc.stderr, "subtitles"))

    # Attempt 2: subtitles filter without force_style
    vf2 = f"subtitles={escaped}"
    cmd2 = ["ffmpeg", "-y", "-i", str(video_path), "-vf", vf2, "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "copy", str(output_path)]
    proc2 = _run_ffmpeg(cmd2, timeout=300, friendly_timeout_msg="字幕烧录超时（限 300 秒）。可尝试关闭「显示字幕」规避。")
    if proc2.returncode == 0:
        return
    logger.warning("subtitles filter (no style) also failed: %s", _ffmpeg_err(proc2.stderr, "subtitles"))

    # Fallback: copy without subtitles
    shutil.copy2(video_path, output_path)
    # P0-6: 字幕烧录失败写 subtitle_status=failed（而非留给读侧猜 skipped）
    if task_dir is not None:
        from pipelines._ffmpeg_helpers import _write_subtitle_status
        _write_subtitle_status(task_dir, "failed", f"字幕烧录失败：{_ffmpeg_err(proc2.stderr, 'subtitles')}")


# ---------------------------------------------------------------------------
# Main composition entry point
# ---------------------------------------------------------------------------


def compose_ppt_video(
    task_dir: Path,
    preset: str,
    subtitle: bool,
    progress_callback,
    *,
    lease_remaining: float | None = None,
) -> Path:
    # lease_remaining 本管线暂不使用（xfade 自有用时核算）；与 registry 统一签名。

    """Execute composing stage: images + audios -> segments -> final video.

    Preset controls inter-slide transition:
      - none / cut: hard cut (no transition)
      - fade: crossfade between slides
      - smooth: longer dissolve (PPT Morph-like)
      - wipe: wipe-right between slides
    """
    import json

    slides_dir = task_dir / "slides"
    audio_dir = task_dir / "audio"
    segments_dir = task_dir / "segments"
    segments_dir.mkdir(exist_ok=True)

    image_paths = sorted(slides_dir.glob("slide-*.jpg"))
    # Accept both mp3 and wav audio files (Gateway now normalizes to mp3, but
    # keep wav fallback for backward compatibility with in-flight tasks)
    audio_paths = sorted(
        list(audio_dir.glob("slide-*.mp3")) or list(audio_dir.glob("slide-*.wav"))
    )

    assert len(image_paths) == len(audio_paths) > 0, (
        f"Slide/audio mismatch: {len(image_paths)} images vs {len(audio_paths)} audios"
    )

    # Load subtitle data if available
    subtitles_map: dict[int, str] = {}
    ass_map: dict[int, str] = {}  # T9: per-slide ASS path from gateway
    if subtitle:
        subtitles_file = task_dir / "subtitles.json"
        if subtitles_file.exists():
            subtitles_data = json.loads(subtitles_file.read_text(encoding="utf-8"))
            subtitles_map = {item["slide"]: item["text"] for item in subtitles_data}
            # T9: gateway may provide ass_file per slide
            for item in subtitles_data:
                af = item.get("ass_file")
                if af and Path(af).exists():
                    ass_map[item["slide"]] = af

    # 1. slide-to-video for each pair (static, no per-slide animation)
    segment_paths = []
    for i, (img, aud) in enumerate(zip(image_paths, audio_paths)):
        seg = segments_dir / f"seg-{i + 1:02d}.mp4"
        _ffmpeg_slide_to_video(img, aud, seg)
        segment_paths.append(seg)
        progress = 85 + int((i + 1) / max(len(image_paths), 1) * 10)
        progress_callback(progress)

    # 1.5. Burn subtitles (T9: prefer gateway-generated ASS; fall back to SRT generation)
    if subtitle and subtitles_map:
        burned_segments: list[Path] = []
        for i, seg in enumerate(segment_paths):
            narration = subtitles_map.get(i + 1, "")
            if not narration:
                burned_segments.append(seg)
                continue

            # T9: Prefer ASS from gateway (自带样式 + 引擎切分)
            ass_path = ass_map.get(i + 1)
            if ass_path:
                burned_seg = seg.parent / f"seg_burned-{i + 1:02d}.mp4"
                _ffmpeg_burn_srt(seg, Path(ass_path), burned_seg, task_dir=task_dir)
                if burned_seg.exists():
                    burned_segments.append(burned_seg)
                    progress_callback(95)
                    seg.unlink(missing_ok=True)
                    continue
                else:
                    logger.warning("ASS burn failed for slide %d, falling back to SRT", i + 1)

            # Fallback: worker generates SRT (legacy path, no ASS from gateway)
            sentences = _split_sentences(narration)
            if not sentences:
                burned_segments.append(seg)
                continue

            duration = _probe_duration(seg)
            srt_path = seg.parent / f"sub-{i + 1:02d}.srt"
            _generate_srt_for_slide(sentences, duration, srt_path)

            burned_seg = seg.parent / f"seg_burned-{i + 1:02d}.mp4"
            _ffmpeg_burn_srt(seg, srt_path, burned_seg, task_dir=task_dir)
            burned_segments.append(burned_seg)

            # 续租 + 取消检查点：字幕逐段重编码耗时较长，每段调一次 progress_callback。
            # 值被高水位钳制在 95（非推进度）；调用同时触发 renew_lease 续租，
            # 以及 is_task_cancelled 检查——用户取消或任务被回收时 renew_lease 会抛
            # TaskCancelled，使烧录提前干净中止（正向行为）。
            progress_callback(95)

            srt_path.unlink(missing_ok=True)
            seg.unlink(missing_ok=True)

        segment_paths = burned_segments

    # 2. concat with transition based on preset
    output_path = task_dir / "output.mp4"
    # xfade 是单个阻塞式 subprocess，运行期间无法回调；调用前先续一次租约刷新到满额。
    # 这是问题② timeout 上限（租约−300）成立的前提。配合①的 ultrafast，xfade 实际耗时
    # 远小于租约；若未来视频更长需改 Popen 轮询续租（结构层）。
    progress_callback(95)
    if len(segment_paths) == 1:
        shutil.move(str(segment_paths[0]), str(output_path))
    elif preset == "fade":
        _ffmpeg_xfade(segment_paths, output_path, "fade", 0.5, progress_callback=progress_callback)
    elif preset == "smooth":
        _ffmpeg_xfade(segment_paths, output_path, "fade", 1.0, progress_callback=progress_callback)
    elif preset == "wipe":
        _ffmpeg_xfade(segment_paths, output_path, "wiperight", 0.5, progress_callback=progress_callback)
    elif preset == "cut":
        # Fast fade (~0.1s) for near-instant cut feel
        _ffmpeg_xfade(segment_paths, output_path, "fade", 0.1, progress_callback=progress_callback)
    else:
        # none / unknown: hard cut
        _ffmpeg_concat_video(segment_paths, output_path)

    # 3. cleanup intermediates
    for d in (audio_dir, segments_dir, slides_dir, task_dir / "_tmp_pdf"):
        if d.exists():
            shutil.rmtree(d, ignore_errors=True)
    subtitles_file = task_dir / "subtitles.json"
    if subtitles_file.exists():
        subtitles_file.unlink()

    return output_path
