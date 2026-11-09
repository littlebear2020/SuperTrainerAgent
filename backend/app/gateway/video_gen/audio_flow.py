"""音频编排与时长对账（2026-08-03 Task 15 从 service.py 抽出）。

这些函数内聚于一件事：**音频是时间轴的唯一真相**。frame.duration 由 TTS 实测决定，
出 worker payload 前再断言一次；拼接与补静音都必须保持「实测 = 记录」。

纯搬迁，零行为变化——所有 docstring 原样保留（它们承载实测结论，如 ``-c copy``
漂 +2.200s/50 帧、libmp3lame 漂 0.000s，丢了就要重新踩坑）。
守护测试见 tests/test_audio_flow_extraction.py。
"""

from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path

from .storyboard import Storyboard

logger = logging.getLogger(__name__)

_RECONCILE_TOLERANCE_SEC = 0.3


def _probe_audio_seconds(path: str) -> float:
    """音频时长（解码口径，失败返 0.0）。独立封装便于测试 patch。"""
    from .orchestration.tts import _get_audio_duration

    return _get_audio_duration(path)


def reconcile_durations_with_audio(storyboard: Storyboard, task_dir) -> bool:  # noqa: ANN001
    """出 worker payload 前校验：frame.duration 必须与音频文件实测一致。

    2026-07-21 漂移根因环 3 兜底：无论 storyboard 时长因何陈旧（CAS 跨代覆盖/并发
    竞态/旧快照），帧有 audio_path 就以实测为准重推 duration/speech_duration
    （>0.3s 容差防探测抖动）；任一帧被修正 → recompute_duration 并返回 True
    （调用方须重建 SRT + 落盘）。spf 场景：补静音产物另存为 `{hash}_pad{spf}.mp3`
    且 `frame.audio_path` 指向它（C9/Task 7 起不再原地覆写 CAS 原件）→ 本函数探到
    的就是补静音后的文件，实测=spf=记录，不触发；若补静音失败，
    `_pad_audio_to_duration` 回落返回原路径，实测=真实语音<spf → 此时改短是**正确**的
    （frame.audio_path 指向的本就是未补文件，视频须与实际音频同步）。
    同步函数（解码口径探测，~50-100ms/帧——2026-08-20 起经 `_get_audio_duration`
    全解码计时，见该函数 docstring）。
    """
    changed = False
    for frame in storyboard.frames:
        if not frame.audio_path:
            continue
        actual = _probe_audio_seconds(frame.audio_path)
        if actual <= 0:
            continue
        if abs(actual - (frame.duration or 0)) > _RECONCILE_TOLERANCE_SEC:
            frame.duration = actual
            frame.speech_duration = actual
            changed = True
    if changed:
        storyboard.recompute_duration()
    return changed


def _concat_audio(audio_paths: list[str], output: str, expected_seconds: float | None = None) -> None:
    """Concatenate mp3 files into one using ffmpeg concat demuxer（重编码，消除漂移）。

    ``expected_seconds``（可选，2026-08-20）：传入 Σframe.duration 时，落盘后按
    解码口径实测拼接产物并比对，差 >0.5s 打 warning（只观测不改行为）。

    C7 真因（Task 8）：**不得用 ``-c copy``**。每个 mp3 帧固定 1152 samples，文件
    时长几乎不可能是帧长整数倍 → 每个文件尾部带 padding；``-c copy`` 保留全部
    padding 并顺序堆叠，产生**累积时长漂移**。

    实测量化（50 个 1.000s mp3）：
        -c copy          → 52.200s（漂 +2.200s，约 44ms/帧）+ 49 条 DTS 非单调警告
        重编码 libmp3lame → 50.000s（漂 0.000s）

    这是「字幕和语音不同步」（C8）的独立真因之一：字幕时间轴按各帧**标称**时长
    累加，实际音频每帧多出约 44ms → 视频越长，后半段字幕与语音偏离越大。

    ⚠️ 量化样本的边界（2026-08-20 补，vg_3300179a11f0 复盘）：上述 50 个文件是
    **合成** 1s mp3（lavfi sine），无真实 TTS 产物的 LAME gapless 特征——真实
    TTS mp3 的 ffprobe 格式时长含 encoder delay/padding（每文件 ~55ms），解码
    拼接时会裁掉。故帧时长探测必须同为解码口径（见
    ``orchestration.tts._get_audio_duration``，2026-08-20 起），否则「视频/字幕轴
    按格式口径、音轨按解码口径」两口径错位在多帧任务累积成秒级渐进音画错位
    （实测 94 帧漂 5.2s）。本函数拼接行为不变——变的是上游的计时口径。

    注：审计原以为该行的问题是「衔接处咔哒爆音」，实跑已证伪（边界跳变仅为中段
    1.4 倍，听不出）。真问题是漂移。代价是一次 CPU 编码，音频体量下可忽略。
    """
    dropped = [p for p in audio_paths if p and not Path(p).exists()]
    if dropped:
        # 缺失输入不再由 ffmpeg 响亮报错（exists 过滤为 07-22 审计计划既定形态）——至少留可追查的告警
        logger.warning("concat_audio dropping %d missing inputs for %s: %s", len(dropped), output, dropped)
    valid = [p for p in audio_paths if p and Path(p).exists()]
    if not valid:
        raise ValueError("no audio to concatenate")
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in valid:
            f.write(f"file '{p}'\n")
        list_path = f.name
    # 原子写：先输出到 tmp 再 replace——直接覆写 output 会让并发读者（recompose
    # worker payload）读到写了一半的文件（2026-07-22 审计 P1#2）。
    tmp_output = str(Path(output).with_name(Path(output).name + ".tmp.mp3"))
    try:
        # 重编码（非 -c copy）：按样本重新对齐，消除 mp3 padding 累积漂移
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-acodec", "libmp3lame", "-q:a", "4", tmp_output]
        proc = subprocess.run(cmd, capture_output=True, timeout=180)
        if proc.returncode != 0:
            raise RuntimeError(f"audio concat failed: {proc.stderr[-500:]!r}")
        Path(tmp_output).replace(output)
        # 2026-08-20 对账告警（只观测不改行为）：拼接产物实测（解码口径）vs
        # Σframe.duration——口径切换残留/单帧探测异常导致的漂移给出可追查日志
        # （vg_3300179a11f0：94 帧累积 5.2s 渐进音画错位，此前无任何告警）。
        if expected_seconds is not None:
            try:
                actual = _probe_audio_seconds(output)
                if actual > 0 and abs(actual - expected_seconds) > 0.5:
                    logger.warning(
                        "audio concat drift: %.3fs actual vs %.3fs expected (%d inputs, %s)",
                        actual,
                        expected_seconds,
                        len(valid),
                        output,
                    )
            except Exception:
                logger.warning("audio concat drift check failed for %s", output, exc_info=True)
    finally:
        Path(list_path).unlink(missing_ok=True)
        Path(tmp_output).unlink(missing_ok=True)


def _pad_audio_to_duration(path: Path, target_seconds: float) -> str:
    """G1: 把音频补静音到 target_seconds（apad），使 frame.duration 与 seconds_per_frame 一致。

    TTS 旁白时长常短于用户设的 seconds_per_frame（如 TTS 3s < spf 8s），导致每帧画面
    闪太快、总时长≠target。补静音到 spf 使画面定格足够久。

    C9（Task 7）：产物**另存**为 ``{stem}_pad{spf}.mp3``，**不再原地覆盖**入参。
    入参通常是 CAS 缓存文件（键 = md5(narration|voice|speed)，**不含 spf**），原地
    覆写会让 spf=8 的任务把补到 8s 的音频写回缓存；之后 spf=4 的任务命中同一键，
    拿到被污染的 8s 音频 → 画面 4s / 音频 8s，且永久残留（除非改文案/音色/语速）。
    CAS 只存「纯 TTS 产物」，padding 属消费侧变换，本就不该回写缓存。

    返回实际应使用的音频路径：成功 → 补静音产物；失败 → 原路径（不阻断出片）。
    **调用方必须使用返回值**更新 frame.audio_path。
    """
    padded = path.with_name(f"{path.stem}_pad{target_seconds:g}.mp3")
    proc = subprocess.run(
        ["ffmpeg", "-y", "-i", str(path), "-af", f"apad=whole_dur={target_seconds:.2f}", "-t", str(target_seconds), "-acodec", "libmp3lame", "-q:a", "9", str(padded)],
        capture_output=True,
        timeout=30,
    )
    if proc.returncode == 0 and padded.exists() and padded.stat().st_size > 0:
        return str(padded)
    logger.warning("pad audio to %.2fs failed for %s; using unpadded original", target_seconds, path)
    padded.unlink(missing_ok=True)
    return str(path)
