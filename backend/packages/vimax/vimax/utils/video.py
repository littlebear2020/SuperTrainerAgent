import logging
import subprocess
from pathlib import Path

import requests
from moviepy import VideoFileClip, concatenate_videoclips
from vimax.utils.retry import download_retry


@download_retry
def download_video(url, save_path):
    try:
        logging.info(f"Downloading video from {url} to {save_path}")

        response = requests.get(url, stream=True, timeout=(10, 300))
        response.raise_for_status()  # 检查请求是否成功
    
        with open(save_path, 'wb') as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        logging.info(f"Video downloaded successfully to {save_path}")
    
    except Exception as e:
        logging.error(f"Error downloading video: {e}")
        raise e


# VENDOR PATCH (TrainerAI) G4/决策 2: 参数一致时走 ffmpeg stream-copy 快路径。
_UNIFORM_PROBE_FIELDS = "stream=codec_name,width,height,r_frame_rate,pix_fmt,time_base"


def _streams_are_uniform(video_paths):
    """全部输入的视频流参数是否完全一致（决定能否 -c copy）。

    只有 codec/分辨率/帧率/像素格式/时基**全部相同**才安全 —— 否则 concat
    demuxer 的产物会花屏或时间戳错乱。探测失败一律返回 False（宁可慢，
    不可产出坏片）。
    """
    if len(video_paths) <= 1:
        return True
    signatures = set()
    for path in video_paths:
        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", _UNIFORM_PROBE_FIELDS, "-of", "csv=p=0", str(path)],
                capture_output=True, text=True, timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        sig = proc.stdout.strip()
        if proc.returncode != 0 or not sig:
            return False
        signatures.add(sig)
        if len(signatures) > 1:
            return False
    return len(signatures) == 1


def _concat_stream_copy(video_paths, output_path):
    """用 concat demuxer + -c copy 拼接。成功返回 True，失败返回 False 由调用方回落。"""
    # 不能用 with_suffix(".concat.txt")：out.mp4 与 out.mkv 同词干会撞同一个
    # 列表文件。字符串拼接保留完整文件名，彻底消除该碰撞类。
    listing = Path(f"{output_path}.concat.txt")
    try:
        listing.write_text(
            "".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in video_paths),
            encoding="utf-8",
        )
        proc = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
             "-c", "copy", str(output_path)],
            capture_output=True, timeout=600,
        )
        if proc.returncode == 0 and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
            return True
        logging.warning(
            "stream-copy concat failed (rc=%s), falling back to re-encode: %s",
            proc.returncode, proc.stderr.decode(errors="replace")[-300:],
        )
        Path(output_path).unlink(missing_ok=True)
        return False
    except Exception:
        logging.warning("stream-copy concat raised, falling back to re-encode", exc_info=True)
        Path(output_path).unlink(missing_ok=True)
        return False
    finally:
        listing.unlink(missing_ok=True)


def concatenate_video_files(video_paths, output_path, codec="libx264", preset="medium"):
    """Concatenate video files, releasing every ffmpeg reader even on failure.

    Each VideoFileClip keeps an ffmpeg subprocess and file handle open until
    closed; leaking them exhausts file descriptors on long multi-scene runs.

    G4/决策 2（2026-07-31）：所有输入编码参数一致时先试 ffmpeg stream-copy——
    实测 3 段 2s 片 1.206s → 0.027s（44x），且不掉画质。修前每次拼接都全量
    重编码，付了「慢 + 掉一档画质」的代价却没换到转场（镜头间仍是硬切），
    是两个缺点同时存在的最差组合。参数不一致（如 Task 1 的占位帧 1920x1080
    混竖屏真镜）时回落 moviepy compose，宽高比不变量不受影响。
    """
    if _streams_are_uniform(video_paths) and _concat_stream_copy(video_paths, output_path):
        logging.info("concatenated %d clips via stream-copy (no re-encode)", len(video_paths))
        return output_path

    clips = []
    final = None
    try:
        for path in video_paths:
            clips.append(VideoFileClip(path))
        # VENDOR PATCH (TrainerAI) NEW-1: method="compose" —— moviepy 默认 "chain"
        # 不做尺寸归一，而是把后续 clip 拉伸填满首个 clip 的画布。占位帧恒
        # 1920x1080，排在竖屏真镜之前时会把整片画布定为 16:9，其后每个真镜
        # 被横向拉伸 3.16 倍（实测 aspect 0.562 → 1.778）。compose 保持各镜
        # 宽高比并 letterbox 到并集画布。
        final = concatenate_videoclips(clips, method="compose")
        final.write_videofile(output_path, codec=codec, preset=preset)
    finally:
        if final is not None:
            final.close()
        for clip in clips:
            clip.close()
    return output_path
