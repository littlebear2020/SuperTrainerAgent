"""ViMax per-shot narration generator (Phase 3).

After ViMax renders the silent video, this module generates per-shot narration
from ViMax's own storyboard (``scene_N/storyboard.json`` or root
``storyboard.json`` for script2video), TTS-synthesizes each, and concatenates
them into a single audio file that matches the video's total duration.

This replaces the old flow where TrainerAI's 22s audio was overlaid onto a
222s ViMax video → ``-shortest`` truncated to 22s.

Data source compatibility:
- idea2video: ``working_dir/scene_N/storyboard.json`` (per-scene)
- script2video: ``working_dir/storyboard.json`` (root)

Reuses ``orchestration.tts.synthesize`` so voice/speed match the main pipeline.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path
from typing import Any

from app.gateway._shared.executors import run_ffmpeg
from app.gateway.video_gen.degradations import (
    KIND_ALIGN_FAILED,
    KIND_DIALOGUE_TRUNCATED,
    KIND_FORMAT_DRIFT,
    KIND_SFX_HEURISTIC,
    record_degradation,
)
from app.gateway.video_gen.engines.vimax.dialogue import (
    _DIALOGUE_TAGS,
    build_speaker_voice_map,
    is_heuristic_sound_effect,
    is_sound_effect_tagged,
    parse_dialogue,
    strip_unrecognized_tags,
)
from app.gateway.video_gen.engines.vimax.voice_policy import VIMAX_TTS_POLICY
from app.gateway.video_gen.orchestration import DEFAULT_LLM_TIMEOUT, _invoke_llm_bounded
from app.gateway.video_gen.orchestration import tts as tts_mod

logger = logging.getLogger(__name__)

_NARRATION_SYSTEM_PROMPT = "你是一位专业的视频旁白编剧。根据给定的画面描述和原始故事，为这一镜头写一句简短的旁白。要求：口语化、15-25个中文字符、与画面内容紧密相关。只输出旁白文本，不要其他内容。"

# 单镜默认时长兜底（ffprobe 失败/视频缺失时）——曾散写 5.0/5.2 三处字面量。
_DEFAULT_SHOT_SECONDS = 5.2


def _find_storyboard_files(working_dir: Path) -> list[Path]:
    """Find ViMax storyboard.json files in scene order.

    idea2video: working_dir/scene_0/storyboard.json, scene_1/...
    script2video: working_dir/storyboard.json (root)
    """
    scene_dirs = sorted(
        [d for d in working_dir.iterdir() if d.is_dir() and d.name.startswith("scene_")],
        key=lambda d: int(d.name.split("_")[1]),
    )
    if scene_dirs:
        result = []
        for d in scene_dirs:
            sb = d / "storyboard.json"
            if sb.exists():
                result.append(sb)
        return result
    # script2video: root storyboard.json
    root_sb = working_dir / "storyboard.json"
    return [root_sb] if root_sb.exists() else []


def _load_shots(storyboard_path: Path) -> list[dict[str, Any]]:
    """Load shot descriptions from a ViMax storyboard.json.

    Coerces bare-string elements to ``{"description": <str>}`` so downstream
    ``shot.get(...)`` always sees a dict. script2video writes list-of-dict
    (model_dump); idea2video's per-scene format is unverified, so this is
    defensive — a string-only shot becomes a description-only dict.
    """
    data = json.loads(storyboard_path.read_text(encoding="utf-8"))
    raw: list = []
    if isinstance(data, list):
        raw = data
    elif isinstance(data, dict) and "shots" in data:
        raw = data["shots"]
    return [s if isinstance(s, dict) else {"description": str(s)} for s in raw]


def _format_srt_time(seconds: float) -> str:
    """Format seconds as an SRT timestamp HH:MM:SS,mmm."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _merge_cue_text(a: str, b: str) -> str:
    """极短 cue 并入前 cue 的文本拼接（AI 评审 #4 + 七轮 low）。

    CJK 相邻直接拼（中文无词间空格）；任一侧为拉丁/数字即补空格——与
    subtitle_engine 的 D2 契约一致（Latin↔Latin "worldhello" 词粘连 +
    CJK↔Latin "好OK" 观感割裂，全引擎统一 "好 OK" 口径）。
    """
    if a and b:
        a_lat = a[-1].isascii() and a[-1].isalnum()
        b_lat = b[0].isascii() and b[0].isalnum()
        if a_lat or b_lat:
            return a + " " + b
    return a + b


def _enforce_sequential(cues: list, orientation: str = "portrait") -> list:
    """T2（2026-10-14 字幕重影修复）：相邻 cue 硬约束 ``end_i <= start_{i+1}``。

    修前镜内 cue 为宽度比估算，segment 后处理（min_dur 合并/让步间隙）可能
    造出与下一镜重叠的 cue（事故帧证据 00:19/00:28-30 重影）。本函数在全部
    cue 汇合后统一钳制：后 cue 起点钳到前 cue 终点；钳后不足 0.2s 的极短 cue
    并入前 cue（文本合并，绝不丢字）。

    AI 评审二轮 #1（2026-10-14，bug·medium）：合并有宽度上限——并入后超
    两行宽（line_width×2）会产出溢屏字幕；超宽时保留独立钳制 cue（同
    subtitle_engine 自身合并的 ``<= max_width * 2`` 口径）。
    AI 评审五轮（bug·medium）：合并发生在逐 cue 折行**之后**——总量 ≤ 两行宽
    不保证逐行 ≤ 单行宽（如 ``6\\N6`` + 10 字 → 第二行 16 溢屏），合并产物须
    **重新折行**（layout_two_lines）。宽度口径引用 subtitle_engine._MAX_WIDTH
    （五轮 low：勿硬编码防上游调参漂移）。
    T3（Lane 2, 2026-10-15）：宽度按 ``orientation`` 取 _MAX_WIDTH（修前硬编码
    portrait——横屏任务合并判定过窄）。
    """
    from app.gateway.video_gen.subtitle_engine import _MAX_WIDTH, Cue, _text_display_width, layout_two_lines

    max_line_width = _MAX_WIDTH.get(orientation, _MAX_WIDTH["portrait"])
    max_join_width = max_line_width * 2
    ordered = sorted(cues, key=lambda c: c.start)
    out: list = []
    for cue in ordered:
        start = max(0.0, cue.start)
        end = max(cue.end, start + 0.01)
        if out and start < out[-1].end:
            remaining = end - out[-1].end
            if remaining >= 0.2:
                start = out[-1].end  # 钳起点，cue 仍可读
            else:
                prev = out[-1]  # 钳不动的极短 cue 并入前 cue
                merged_flat = _merge_cue_text(prev.text.replace("\\N", ""), cue.text.replace("\\N", ""))
                if _text_display_width(merged_flat) <= max_join_width:
                    out[-1] = Cue(text=layout_two_lines(merged_flat, max_line_width), start=prev.start, end=max(prev.end, end))
                    continue
                # 超宽不并：保留独立钳制 cue（极短但可读），绝不产出溢屏字幕
                start = prev.end
        # AI 评审七轮（bug·high）：钳制发生在 end 计算之后——前 cue 时间窗完全吞没
        # 当前 cue 时 start=prev.end 可能 > 已算好的 end，产出倒挂负时长 cue；钳后重算。
        end = max(end, start + 0.01)
        out.append(Cue(text=cue.text, start=start, end=end))
    return out


def _write_srt(entries: list[tuple], output_path: Path, *, orientation: str = "portrait", video_w: int = 1080, video_h: int = 1920) -> None:
    """Write SRT (canonical) + derived ASS from cue entries.

    T7（2026-08-13）：改用 subtitle_engine.segment() 统一切分（收敛四套实现）。
    双写 subtitle.srt (canonical) + subtitle.ass (派生, PlayRes=ViMax 输出分辨率)。
    空 cue 防护（libass 崩溃）+ 两行折行 (D1) + CJK↔Latin 空格 (D2) 全部继承引擎。

    2026-10-14 配音修复批：
    - T2：entry 文本剥未识别 ``[...]`` 前缀标签（双保险，漂移标签绝不进字幕）；
      全部 cue 汇合后过 ``_enforce_sequential`` 硬约束不重叠。
    - T7c：entry 可带第 4 元素 word_cues（doubao 字级时间戳，**全局时间轴**），
      该镜走真实时间戳切分而非宽度估算。
    T3（Lane 2, 2026-10-15）：orientation/宽高参数化（修前硬编码 portrait/
    1080x1920——横屏任务折行过窄 + ASS PlayRes 失真）。默认竖屏 = 存量行为。
    entries 形态：(text, start, end) 或 (text, start, end, word_cues | None)。
    """
    if not entries:
        logger.warning("ViMax narration: 0 个字幕 cue（所有镜头无对白文本）→ 不写 subtitle.srt；成品视频将无配音无字幕。检查 storyboard audio_desc 是否含 [Speaker] 对白标记。")
        return

    from app.gateway.video_gen.subtitle_engine import Cue, segment, write_ass, write_srt

    all_cues: list[Cue] = []
    for entry in entries:
        text, start, end = entry[0], entry[1], entry[2]
        word_cues = entry[3] if len(entry) > 3 else None
        shot_dur = max(0.0, end - start)
        if shot_dur <= 0:
            continue
        # T2 双保险：cue 文本不含未识别标签（漂移时宁可字幕少个标签，不烧 [xx] 上屏）
        text, _drift_tags = strip_unrecognized_tags(text)
        if not text.strip():
            continue
        if word_cues:
            # T7c：字级时间戳已是全局时间轴，直接映射（不再按镜偏移）
            frame_cues = segment(text, word_cues=word_cues, audio_duration=shot_dur, orientation=orientation)
            all_cues.extend(frame_cues)
        else:
            frame_cues = segment(text, audio_duration=shot_dur, orientation=orientation)
            for cue in frame_cues:
                all_cues.append(Cue(text=cue.text, start=cue.start + start, end=cue.end + start))

    # T2：cue 时间硬约束——相邻不重叠（修字幕重影）；宽度口径同朝向（T3）
    all_cues = _enforce_sequential(all_cues, orientation=orientation)

    if not all_cues:
        logger.warning("ViMax narration: 切分后 0 个有效 cue（台词全为标点/空白）→ 不写 subtitle.srt，避免空 SRT 让 libass 崩。")
        return

    # Write SRT (canonical)
    write_srt(all_cues, out_path=str(output_path))

    # Write ASS (derived, PlayRes=任务输出分辨率——T3 前硬编码 1080×1920)
    ass_path = str(output_path).rsplit(".", 1)[0] + ".ass"
    try:
        write_ass(all_cues, out_path=ass_path, video_w=video_w, video_h=video_h)
    except Exception:
        logger.warning("ViMax subtitle ASS generation failed, falling back to SRT-only", exc_info=True)


def _get_shot_duration(video_path: Path) -> float:
    """Get video.mp4 duration in seconds via ffprobe."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(proc.stdout.strip())
    except Exception:
        return _DEFAULT_SHOT_SECONDS  # fallback


def _probe_audio_duration(path: Path) -> float:
    """Get an mp3/wav duration in seconds via ffprobe."""
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return float(proc.stdout.strip())
    except Exception:
        return 0.0


_ATEMPO_CAP = 1.3


def _align_chunk_to_shot(
    chunk_path: Path,
    target_duration: float,
    task_dir: Path,
    chunk_idx: int,
) -> str:
    """把一个 TTS 对白 chunk 对齐到该镜视频实际时长，消除累积漂移。

    - 短于目标 → apad 补尾部静音到 target_duration。
    - 长于目标(ratio ≤ 1.3) → atempo 加速 + -t 截断到 target_duration。
    - 极长(ratio > 1.3) → atempo 1.3 + -t 硬截断（避免变调，但会切掉台词尾词，
      写 warning 便于验收评估影响面；治本在上游约束每镜台词字数）。
    - 已接近目标(差 <0.1s) → 原样返回。
    """
    if not chunk_path.exists():
        return str(chunk_path)
    actual = _probe_audio_duration(chunk_path)
    if actual <= 0:
        return str(chunk_path)

    if abs(actual - target_duration) < 0.1:
        return str(chunk_path)

    aligned = task_dir / f"narration_chunk_{chunk_idx:04d}_aligned.mp3"

    if actual < target_duration:
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(chunk_path),
            "-af",
            f"apad=whole_dur={target_duration}",
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(aligned),
        ]
    else:
        ratio = actual / target_duration
        atempo = min(ratio, _ATEMPO_CAP)
        if ratio > _ATEMPO_CAP:
            overflow = actual / atempo - target_duration
            logger.warning(
                "Shot %d: TTS %.2fs 远超镜头 %.2fs，加速到 %.2fx 后仍需截断约 %.2fs 尾词（治本见上游台词字数约束）",
                chunk_idx,
                actual,
                target_duration,
                atempo,
                overflow,
            )
            # Task 23 / C6: 硬截尾词要让用户看见。修前只有这条 warning，
            # 用户侧听到的是「话没说完就切镜」却无任何说明。
            record_degradation(
                task_dir,
                kind=KIND_DIALOGUE_TRUNCATED,
                detail=f"第 {chunk_idx + 1} 镜台词过长：加速到 {atempo:.2f}x 后仍截断约 {overflow:.1f}s 尾词",
                frame_index=chunk_idx,
            )
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(chunk_path),
            "-af",
            f"atempo={atempo:.4f}",
            "-t",
            str(target_duration),
            "-c:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(aligned),
        ]

    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=120)
        if proc.returncode == 0 and aligned.exists() and aligned.stat().st_size > 0:
            chunk_path.unlink(missing_ok=True)
            return str(aligned)
    except Exception:
        logger.exception("Chunk alignment failed for shot %d", chunk_idx)
    logger.warning("Chunk alignment failed for shot %d, keeping raw TTS", chunk_idx)
    # Task 23 / C13: 返回未对齐的原 chunk 意味着该镜时长≠镜头时长，音频/画面/字幕
    # 三者从这镜起累积错位，且下游无从得知。留痕让用户至少知道哪一镜开始漂。
    record_degradation(
        task_dir,
        kind=KIND_ALIGN_FAILED,
        detail=f"第 {chunk_idx + 1} 镜音频对齐失败，沿用未对齐配音（该镜起可能音画错位）",
        frame_index=chunk_idx,
    )
    return str(chunk_path)


async def _generate_narration_text(
    llm: Any,
    shot_description: str,
    original_script: str,
    *,
    timeout: float = DEFAULT_LLM_TIMEOUT,
) -> str:
    """Use LLM to generate ~15-25 char narration from a shot description."""
    messages = [
        {"role": "system", "content": _NARRATION_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": json.dumps(
                {"shot_description": shot_description[:500], "story_context": original_script[:500]},
                ensure_ascii=False,
            ),
        },
    ]
    # E5 扩展：vimax 旁白 LLM 调用也走有界超时（与主 orchestration 路径一致，防 hang）
    resp = await _invoke_llm_bounded(llm, messages, timeout=timeout)
    content = resp.content if hasattr(resp, "content") else str(resp)
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    return content.strip()[:100]  # cap at 100 chars


def _fallback_trainerai_audio(task_dir: Path, working_dir: Path) -> Path | None:
    """无可用 narration 时的 TrainerAI 音频兜底。

    skip_tts=True 下 task_dir/audio.mp3 从未生成——返回不存在的路径会让
    audio_overlay 静默交付无声成片（仅 warning 返回原视频）。文件不存在时
    返回 None，由 engine.py 已有的 loud-fail 分支把任务打成失败。
    """
    fallback = task_dir / "audio.mp3"
    if fallback.exists():
        logger.warning("No ViMax narration from %s; falling back to TrainerAI audio", working_dir)
        return fallback
    logger.error("No ViMax narration from %s and no fallback audio.mp3 (skip_tts) — failing loud", working_dir)
    return None


async def generate_vimax_narration(
    *,
    working_dir: Path,
    task_dir: Path,
    original_script: str,
    llm: Any,
    voice_id: str,
    characters: list[dict] | None = None,
    speed: float = 1.2,  # 2026-07-22: 透传 payload tts_speed；默认 1.2 保持既有行为
    orientation: str = "portrait",  # T3（Lane 2）：字幕朝向（EngineContext.aspect 推导）
    video_w: int = 1080,  # T3：ASS PlayRes 宽（随朝向）
    video_h: int = 1920,  # T3：ASS PlayRes 高（随朝向）
) -> Path | None:
    """Generate per-shot narration audio matching the ViMax video's total duration.

    Returns the path to the concatenated audio file (task_dir/vimax_narration.mp3).
    Falls back to task_dir/audio.mp3 only when that file exists; returns None
    otherwise (engine loud-fails the task instead of delivering a silent video).

    P1/P2 — dialogue mode (characters provided):
      - Parse each shot's audio_desc into (speaker, line) via dialogue.parse_dialogue.
      - Use the actual character line for TTS (NOT an LLM-rewritten narration).
      - Assign a distinct voice per speaker via build_speaker_voice_map.
      - Sound-effect shots are SILENT; non-dialogue narration is read by narrator_voice
        (2026-07-27 fix — was muted like sound effects).
    Legacy mode (characters=None):
      - LLM rewrites each shot into a ~15-25 char third-person narration (Phase 3).
      - Single voice_id for all shots. Preserved for backward compat / non-dialogue videos.

    T3（Lane 2, 2026-10-15）：orientation/宽高贯通到 segment/_enforce_sequential/
    write_ass（修前硬编码竖屏——横屏任务折行过窄 + PlayRes 失真）。默认竖屏 =
    存量行为。
    """
    storyboard_files = _find_storyboard_files(working_dir)
    if not storyboard_files:
        return _fallback_trainerai_audio(task_dir, working_dir)

    # Plan A Task 2（2026-10-18）：voice map 构建前预扫描全部分镜，收集全片
    # **实际发声者**集合（parse_dialogue 判定为对白的 speaker；旁白/音效不计）
    # 供 build_speaker_voice_map 应用单口播例外。shots 同时预载，主循环复用
    # （避免二次 IO）。
    # 评审修复（2026-10-19）：只统计**带对白标签**（[Speaker]/[说话人]）的段——
    # 主正则对无标签 `X："…"` 形态宽松（X 无须是角色名），中文旁白常见的引用句
    # `俗话说："一寸光阴一寸金"` 会被解析成幻影 speaker="俗话说"，把集合撑到 ≥2
    # 人后单口播继承静默失效。无标签引用句由 default/narrator 念出，不是发声者。
    preloaded_shots: list[tuple[Path, list[dict]]] = []
    spoken_speakers: set[str] = set()
    for sb_path in storyboard_files:
        shots = _load_shots(sb_path)
        preloaded_shots.append((sb_path, shots))
        for shot in shots:
            audio_text = (shot.get("audio_desc") or "").strip()
            if not any(audio_text.startswith(tag) for tag in _DIALOGUE_TAGS):
                continue
            seg = parse_dialogue(audio_text)
            if seg is not None and seg.is_dialogue and seg.speaker:
                spoken_speakers.add(seg.speaker)

    # T7a（2026-10-14）：设置 TTS 降级上下文，使 record_tts_degradation 能把
    # vimax 音色降级写进 degradations sidecar（前端可见）。对齐 service.py 两处
    # 既有形态（:1976/:3303）；TTS 只发生在下方循环内，循环后清除上下文。
    tts_mod.set_tts_degradation_context(task_dir)

    # P2: resolve the per-speaker voice map. In dialogue mode, load characters.json
    # (working_dir/characters.json) unless the caller passed an explicit list.
    voice_map = None
    if characters is not None:
        if not characters:
            characters_path = working_dir / "characters.json"
            if characters_path.exists():
                try:
                    characters = json.loads(characters_path.read_text(encoding="utf-8"))
                except Exception:
                    logger.exception("Failed to load characters.json; falling back to legacy narration")
                    characters = None
        if characters:
            voice_map = build_speaker_voice_map(characters, default_voice=voice_id, narrator_voice=voice_id, spoken_speakers=spoken_speakers)

    audio_chunks: list[str] = []

    attempted_speech = 0  # N-1: 有台词/旁白可念的镜头数
    narration_fallback_shots = 0  # T6: 非对白走旁白 fallback 的镜数（占比统计）
    drift_shots = 0  # T6: 命中未识别 [...] 标签的镜数
    # T7c：entry = (text, start, end, word_cues|None)——word_cues 为 doubao 字级
    # 时间戳（全局时间轴）；None = 宽度估算（preset/edge/无字幕通道）。
    srt_entries: list[tuple] = []
    cumulative_start = 0.0
    chunk_idx = 0

    # AI 评审 #6（2026-10-14）：主循环包 try/finally 清降级上下文——
    # 异常穿出（engine 兜住）也不残留；正常路径语义不变。
    try:
        for sb_path, shots in preloaded_shots:
            scene_dir = sb_path.parent
            for shot in shots:
                shot_idx = shot.get("idx", shot.get("index", chunk_idx))
                # ViMax's ShotBriefDescription uses `visual_desc` (rich scene description)
                # and `audio_desc` (sound/dialogue) — NOT `description`/`shot_description`.
                visual = shot.get("visual_desc") or shot.get("description") or shot.get("shot_description") or ""
                audio = shot.get("audio_desc") or ""
                video_path = scene_dir / "shots" / str(shot_idx) / "video.mp4"
                # T4（Lane 2, 2026-10-15）：ffprobe 改经线程池——sync 直调在 N 镜循环里
                # 逐次阻塞事件循环。用 asyncio.to_thread 而非 run_ffmpeg：存量测试把
                # ng.run_ffmpeg 桩成 fire-and-forget（不执行 fn 恒返 None），本处返回值
                # 承重会碎；to_thread 同为 off-loop（:551 已有先例）且真执行。
                shot_duration = await asyncio.to_thread(_get_shot_duration, video_path) if video_path.exists() else _DEFAULT_SHOT_SECONDS

                # Resolve narration text + voice for this shot.
                # T6（2026-10-16 收口批）：sidecar 镜号口径统一为**全局 chunk_idx**——
                # 与 frame_index、vimax_shots manifest 的全局连续 gid 同口径（修前
                # detail 用 scene 局部 idx、frame_index 用全局，前端按 frame_index
                # 定位多场景会错镜——PR#19 评审十一轮 low 收口）。
                segment_emotion = ""
                fellthrough_tag_stripped = False
                if voice_map is not None:
                    # 八轮 low：每镜只解析一次——seg 同时喂解析与 T6 占比统计
                    # （此前 _is_dialogue_text 二次解析，词表改动会静默分叉）
                    seg = parse_dialogue(audio)
                    fellthrough_tag_stripped = seg is not None and getattr(seg, "tag_fellthrough", False)
                    narration_text, segment_voice, segment_emotion = _resolve_dialogue_segment(audio, voice_map, visual, seg=seg)
                    # T4：无标签启发式音效命中（已静音）→ sidecar 留痕（可审计，防误杀旁白）。
                    # 仅记**无标签**路径——带 [音效] 标签是既有语义，compact 后常同 hit
                    # 启发式（如 "[音效] 小刀刻木声…" ≤20 字以声结尾），不记防噪声。
                    if narration_text == "" and is_heuristic_sound_effect(audio) and not is_sound_effect_tagged(audio):
                        record_degradation(
                            task_dir,
                            kind=KIND_SFX_HEURISTIC,
                            detail=f"第 {chunk_idx + 1} 镜无标签音效描述按音效静音：{audio.strip()[:60]}",
                            frame_index=chunk_idx,
                        )
                    if narration_text and not seg.is_dialogue:
                        narration_fallback_shots += 1
                else:
                    # Legacy LLM-narration path.
                    shot_desc = f"{visual}\n[音频] {audio}" if audio else visual
                    # N-3 (Task 8b): LLM 调用必须自带 except。此前它在 per-shot try
                    # **之外**（try 只包 TTS），且 _generate_narration_text 内部无
                    # try/except → 第 30 镜 LLM 超时会穿出整个循环 → engine 的 except
                    # 兜住 → narration_audio=None → **前 29 镜已成功的 TTS 全部丢弃**。
                    # 回落 shot_desc[:50] 的逻辑本就存在，只是够不着。
                    try:
                        narration_text = await _generate_narration_text(llm, shot_desc, original_script)
                    except Exception:
                        logger.exception(
                            "narration LLM failed for shot %d; falling back to shot description",
                            chunk_idx,
                        )
                        narration_text = ""
                    if not narration_text:
                        narration_text = shot_desc[:50]
                    segment_voice = voice_id

                # T6：未识别 [...] 前缀标签 → 剥离后再念（宁剥错不念出），并记 drift。
                # 八轮（bug·medium）：在两分支**汇合处**统一做——legacy 分支的 LLM 旁白
                # /shot_desc 回落同样不得念出漂移标签（修前只覆盖 dialogue 模式）。
                # T6（2026-10-16 收口批）：已知标签 fallthrough 剥标签（parse_dialogue
                # 内已剥，此处 narration_text 无标签可剥）同样记 drift——修前只
                # logger.warning，可审计留痕断链。
                if narration_text:
                    cleaned, drift_tags = strip_unrecognized_tags(narration_text)
                    if drift_tags:
                        drift_shots += 1
                        record_degradation(
                            task_dir,
                            kind=KIND_FORMAT_DRIFT,
                            detail=f"第 {chunk_idx + 1} 镜配音格式漂移：{'/'.join(drift_tags)}；样本：{(audio or narration_text).strip()[:80]}",
                            frame_index=chunk_idx,
                        )
                        narration_text = cleaned
                    elif fellthrough_tag_stripped:
                        drift_shots += 1
                        record_degradation(
                            task_dir,
                            kind=KIND_FORMAT_DRIFT,
                            detail=f"第 {chunk_idx + 1} 镜配音格式漂移：已知对白标签三级解析未命中，已剥标签回落旁白；样本：{(audio or '').strip()[:80]}",
                            frame_index=chunk_idx,
                        )

                if not narration_text:
                    # No audio content → silence matching the shot duration.
                    # T4：静音生成（ffmpeg）经线程池（to_thread 口径见上方时长探测注释）。
                    await asyncio.to_thread(_append_silence_chunk, task_dir, chunk_idx, video_path, audio_chunks)
                    cumulative_start += shot_duration
                    chunk_idx += 1
                    continue

                # N-1: 记录「本镜有话要说」——用于区分「全片纯音效（正常）」与
                # 「有台词但全部 TTS 失败（故障）」。
                attempted_speech += 1
                chunk_path = task_dir / f"narration_chunk_{chunk_idx:04d}.mp3"
                # T5：解析出的情绪标注 → doubao emotion 参数（仅 doubao 音色且映射表
                # 命中才下发；clone:/preset:/edge 静默忽略，行为不变；降级路径自动丢弃）。
                emotion_kwargs: dict[str, Any] = {}
                if segment_emotion and segment_voice.startswith("doubao:"):
                    mapped_emotion = tts_mod.resolve_doubao_emotion(segment_emotion)
                    if mapped_emotion:
                        emotion_kwargs["emotion"] = mapped_emotion
                try:
                    # Plan B（2026-10-19，D4）：正式 narration 显式携带 ViMax 策略——
                    # preset 防漏底线 + 豆包可降级故障直落同性别 Edge（不经 360）。
                    result = await tts_mod.synthesize(
                        narration_text,
                        voice_id=segment_voice,
                        speed=speed,
                        output_path=str(chunk_path),
                        policy=VIMAX_TTS_POLICY,
                        **emotion_kwargs,
                    )
                    # 十轮（bug·high）：原始时长须在**对齐前**探测——_align_chunk_to_shot
                    # 成功后会 unlink 原 chunk，事后探测恒得 0.0 → cue_scale 恒 1.0，
                    # atempo 缩放永不生效（字级字幕系统性晚于音频）。仅 doubao（有
                    # word_cues）才探测，保持其余路径零开销。
                    word_cues_raw = getattr(result, "word_cues", None)
                    orig_chunk_dur = 0.0
                    if word_cues_raw:
                        orig_chunk_dur = await asyncio.to_thread(_probe_audio_duration, Path(result.audio_path))
                    # T4：对齐 ffmpeg 命令（timeout=120 的 sync 直调）经线程池（to_thread 口径见上方注释）。
                    aligned_path = await asyncio.to_thread(_align_chunk_to_shot, Path(result.audio_path), shot_duration, task_dir, chunk_idx)
                    audio_chunks.append(aligned_path)
                    # T7c：doubao chunk 的字级时间戳（chunk 局部秒）→ 全片时间轴。
                    # 对齐可能 atempo 加速（audio 压缩 factor=atempo）→ 字级时间同除；
                    # 超出镜窗口（-t 截断/apad）钳到镜边界。
                    word_cues_global: list[tuple[str, float, float]] | None = None
                    if word_cues_raw:
                        shot_start = cumulative_start
                        # 九轮（bug·medium）：scale 与 _align_chunk_to_shot **同式**
                        # min(orig/target, _ATEMPO_CAP)——「封顶 + -t 截断」分支的实际
                        # 加速是 cap 而非 ratio（截断只切尾部，存留词时间轴 = word/cap）。
                        # 对齐失败（沿用原路径）→ 恒 1.0。
                        cue_scale = 1.0
                        if aligned_path != str(result.audio_path) and orig_chunk_dur - shot_duration >= 0.1:
                            cue_scale = min(orig_chunk_dur / shot_duration, _ATEMPO_CAP)
                        word_cues_global = []
                        for w, ws, we in result.word_cues:
                            gs = shot_start + min(ws / cue_scale, shot_duration)
                            ge = shot_start + min(we / cue_scale, shot_duration)
                            if ge > gs:
                                word_cues_global.append((w, gs, ge))
                        if not word_cues_global:
                            word_cues_global = None
                    # SRT cue 只在 TTS 成功后入列——失败镜是静音，烧出台词字幕=音字不符
                    srt_entries.append((narration_text, cumulative_start, cumulative_start + shot_duration, word_cues_global))
                except Exception:
                    logger.exception("TTS failed for shot %d; inserting silence", chunk_idx)
                    # T4：静音生成经线程池（to_thread 口径见上方注释）。
                    await asyncio.to_thread(_append_silence_chunk, task_dir, chunk_idx, video_path, audio_chunks)

                cumulative_start += shot_duration
                chunk_idx += 1

    finally:
        tts_mod.set_tts_degradation_context(None)

    if not audio_chunks:
        return _fallback_trainerai_audio(task_dir, working_dir)

    # Concatenate all chunks
    output_path = task_dir / "vimax_narration.mp3"
    await run_ffmpeg(_concat_chunks, audio_chunks, str(output_path))

    # Cleanup chunks
    for chunk in audio_chunks:
        Path(chunk).unlink(missing_ok=True)

    # Write the SRT subtitle file from per-shot narration text + cumulative timings.
    # ViMax's render path skips _synthesize_audio (skip_tts=True) so the TrainerAI SRT
    # writer (_write_storyboard_srt) never runs — this is the sole SRT source for ViMax.
    _write_srt(srt_entries, task_dir / "subtitle.srt", orientation=orientation, video_w=video_w, video_h=video_h)
    spoken = sum(1 for entry in srt_entries if entry[0].strip())

    # N-1 (Task 11): 全静音成片不得标「已完成」。
    #
    # 每镜 TTS 失败会 _append_silence_chunk 塞静音保 A/V 同步（本身是对的），但
    # 静音 chunk 让 audio_chunks **非空** → 上面 `if not audio_chunks` 的守卫放行 →
    # engine.py 只在 narration_audio is None 时 loud-fail → 用户拿到一个**完全无声**
    # 的视频、状态「已完成」，有画面有字幕就是没人说话。
    #
    # 判据用 `attempted_speech`（有台词/旁白文本可念的镜头数）而非镜头总数：整片
    # 纯音效（audio_desc 全是 [音效]）本就没有台词，那是正常内容不是故障，误判会
    # 把好片打成失败。
    if attempted_speech and spoken == 0:
        logger.error(
            "ViMax narration: %d 个镜头有台词但全部 TTS 失败（%d 个 cue 全空）——拒绝交付无声成片，交给 engine loud-fail",
            attempted_speech,
            len(srt_entries),
        )
        return None

    # T6 统计：旁白 fallback 占比（对白视频该值应接近 0；高占比 = 分镜格式漂移信号）
    if attempted_speech:
        logger.info(
            "ViMax narration stats: 旁白 fallback %d/%d 镜（%.0f%%）、格式漂移 %d 镜",
            narration_fallback_shots,
            attempted_speech,
            narration_fallback_shots / attempted_speech * 100,
            drift_shots,
        )

    logger.info("ViMax narration generated: %d shots → %s (SRT: %d cues)", chunk_idx, output_path, len(srt_entries))
    return output_path


def _resolve_dialogue_segment(
    audio_desc: str,
    voice_map: Any,
    visual: str,
    seg: Any = None,
) -> tuple[str, str, str]:
    """P1/P2: parse audio_desc → (tts_text, voice_id, emotion).

    - Dialogue shot: tts_text = the character's line (speaker name stripped),
      voice_id = that speaker's assigned voice, emotion = 说话人括号注释（T5）。
    - Sound effect ([Sound Effect]/[音效]，含 T4 无标签启发式命中): tts_text = ""
      (silent). Sound-effect descriptions like '微弱的鸟鸣声' must NOT be read
      aloud by TTS — the actual ambient sound comes from the video's audio track,
      not a voice saying 'birdsong'. The caller inserts a silent chunk matching
      the shot's video duration so audio stays aligned with the timeline.
    - Non-dialogue narration (plain text): read aloud by narrator_voice
      (2026-07-27 fix — was muted like sound effects; SpeakerVoiceMap.narrator_voice
      was a dead field: resolved by voice_for("") yet never spoken).
    - No audio_desc at all: SILENT (reading the visual description aloud is
      the 'reads the script' bug).
    Returns ("", voice, "") when there's nothing to speak (caller → silence path).
    八轮 low：``seg`` 可传入调用方已解析的 DialogueSegment（每镜只解析一次，
    消除 T6 占比统计的二次解析与口径分叉可能）。
    """
    from app.gateway.video_gen.engines.vimax.dialogue import is_sound_effect

    if seg is None:
        seg = parse_dialogue(audio_desc)
    if seg is None:
        # No audio_desc at all — silent (don't fall back to visual excerpt;
        # reading a scene description aloud is the 'reads the script' bug).
        return ("", voice_map.voice_for(""), "")
    if seg.is_dialogue:
        return (seg.line, voice_map.voice_for(seg.speaker), seg.emotion)
    if is_sound_effect(seg.line):
        return ("", voice_map.voice_for(""), "")
    # 非音效的非对白文本（旁白句）→ narrator 念出（2026-07-27：曾一律静音）
    return (seg.line, voice_map.narrator_voice, "")


def _append_silence_chunk(task_dir: Path, chunk_idx: int, video_path: Path, audio_chunks: list[str]) -> None:
    """Append a silent mp3 chunk matching the shot's video duration."""
    chunk_path = task_dir / f"narration_chunk_{chunk_idx:04d}.mp3"
    if video_path.exists():
        duration = _get_shot_duration(video_path)
    else:
        duration = _DEFAULT_SHOT_SECONDS
    _create_silence(chunk_path, duration)
    audio_chunks.append(str(chunk_path))


def _container_duration(path: str) -> float:
    """ffprobe 容器口径时长（format=duration，即文件**声明**的时长）；失败 0.0。

    仅用于分片自洽性检查（声明 vs 解码）：真实半截文件的 Xing 头仍声明完整
    帧数 → 容器口径远大于解码口径 → 入口拦截（见 ``_concat_chunks``）。拼接
    对账本身不用这个口径。
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True,
            timeout=10,
        )
        return float(proc.stdout.strip()) if proc.returncode == 0 else 0.0
    except (ValueError, OSError, subprocess.TimeoutExpired):
        return 0.0


def _concat_chunks(chunk_paths: list[str], output: str) -> None:
    """Concatenate mp3 files（重编码，消除漂移）。

    T3（Lane 2, 2026-10-15）：修前首选 ``-c copy``——mp3 每帧 1152 samples，
    文件时长几乎不可能是帧长整数倍 → 尾部 padding 顺序堆叠产生 ~44ms/段
    累积漂移（``audio_flow._concat_audio`` C7 同因同修，50×1s 实测 copy 漂
    +2.2s / 重编码 0.0s）。对齐主管线形态**默认重编码 libmp3lame**，copy 快
    路径移除（拍板记 changelog：一次 CPU 编码换时间轴精确，体量可忽略）。

    P1-6: rc=0 不等于成功。concat demuxer 对损坏输入会 exit 0 并静默截断，
    故按预期总时长双向校验产物——截断（数据丢失）raise；过冲（T3 补的方向，
    重编码下不应发生，发生即异常信号）warning 留痕。

    Plan A Task 1（2026-10-18）双口径对账事故 vg_da6419f0c0e6（failed@99%）：
    分片容器口径（ffprobe format=duration）含 LAME encoder delay/padding，
    19 段对齐 chunk 每段虚高 ~60-80ms、累积 1.22s → 完整旁白被旧「容器口径
    求和 vs 容器口径产物」校验误判截断。修复后是**两层检查**，阈值同源 0.5s
    （系统对任何位置音频损失的容忍度都是 0.5s，不是放宽）：

    1. 分片自洽（声明 vs 解码）：真实半截 chunk 的 Xing 头仍声明完整帧数，
       在第 2 层解码对账里是「自洽」的（expected 只统计可解码部分），必须
       分片级拦截——合法 gapless 文件声明−解码差仅 encoder delay/padding
       （事故分片实测 60-78ms），对 0.5s 有 ~6 倍安全边际。
    2. 拼接对账（解码 vs 解码）：expected/actual 双侧统一解码口径
       （``orchestration.tts._get_audio_duration``，ffmpeg -f null 计时），
       不复制第二套 stderr 解析器；完整输入两侧一致（事故 Σchunk 152.51
       vs 产物 152.56，差 0.05s），demuxer 静默截断时产物解码时长塌陷 → raise。

    解码探测不可得（分片非空但解码为 0 / 全和为 0）拒绝放行——「无法校验」
    不得当「校验通过」。
    """
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as f:
        for p in chunk_paths:
            f.write(f"file '{p}'\n")
        list_path = f.name
    try:
        cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c:a", "libmp3lame", "-q:a", "4", output]
        proc = subprocess.run(cmd, capture_output=True, timeout=600)
        if proc.returncode != 0:
            raise RuntimeError(f"narration concat failed: {proc.stderr[-2000:]!r}")
    finally:
        Path(list_path).unlink(missing_ok=True)

    decoded_chunks: list[float] = []
    for p in chunk_paths:
        # 边界收口（2026-10-19 P2）：零字节任意位置直接 raise——不再依赖 ffmpeg concat
        # 对位置的不同容忍度（中间位 rc≠0 会拦，首/末位曾静默放行；「无法校验不得放行」）。
        if Path(p).stat().st_size == 0:
            raise RuntimeError(f"narration chunk is empty (zero bytes): {p}")
        decoded = tts_mod._get_audio_duration(p)
        if decoded <= 0:
            raise RuntimeError(f"narration concat verify unavailable: 分片解码时长探测失败 {p}")
        declared = _container_duration(p)
        if declared - decoded > 0.5:
            raise RuntimeError(f"narration chunk truncated: declared ~{declared:.2f}s but decodes {decoded:.2f}s: {p}")
        decoded_chunks.append(decoded)

    expected = sum(decoded_chunks)
    if chunk_paths and expected <= 0:
        raise RuntimeError("narration concat verify unavailable: 全部分片解码时长探测为 0——无法校验不得放行")
    actual = tts_mod._get_audio_duration(output)
    if actual < expected - 0.5:
        raise RuntimeError(f"narration concat truncated: expected ~{expected:.2f}s, got {actual:.2f}s")
    if actual > expected + 0.5:
        logger.warning("narration concat overshoot: expected ~%.2fs, got %.2fs（重编码下不应发生——检查输入 chunk 时长口径）", expected, actual)


def _create_silence(path: Path, duration: float) -> None:
    """Create a silent mp3 of the given duration."""
    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=24000:cl=mono",
        "-t",
        str(duration),
        "-c:a",
        "libmp3lame",
        str(path),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=30)
    # P1-6: 绝不写空文件--concat demuxer 遇 0 字节 mp3 会打印
    # "Error during demuxing" 却 exit 0，其后的全部音频被丢弃
    # （实测 8.0s -> 2.0s），且 SRT 仍按完整时间轴写 -> 字幕音频错位。
    if proc.returncode != 0 or not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"silence generation failed ({duration}s): {proc.stderr[-300:]!r}")
