# -*- coding: utf-8 -*-
"""
TTS Audio Generation Template for Remotion Videos

This script generates TTS audio files for each scene and calculates
subtitle timing based on actual audio durations.

Usage:
1. Edit SCENE_TEXTS below with your video content
2. Edit NAMES to match your scene names
3. Run: python3 scripts/generate_audio_template.py
4. Output:
   - Audio files in public/audio/<video-name>/
     (staticFile() only reads the project's public/ dir — NEVER relocate
      audio outside it, rendering 404s otherwise; see references/gotchas.md)
   - Timing data in scripts/tts_<video-name>.json:
     * segments / segments_global — sentence-level frames from ffprobe
       (legacy contract, always emitted, shape unchanged)
     * captions — word-level GLOBAL-MILLISECOND captions
       (@remotion/captions shape: {text, startMs, endMs, timestampMs,
       confidence}), emitted ONLY when every sentence got doubao word
       timestamps; absence = degraded to sentence-level (use segments
       as before). Feed props.captions / KaraokeCaptions.tsx directly.

词级换算契约（与 SKILL.md 第二步同口径，勿引入双口径）：
  startMs = round((句全局起始秒 + word.startTime) * 1000)，endMs 同理；
  句全局起始秒 = 场景起点（Σ 前序场景 durationInFrames / FPS）+ 句内偏移。
  豆包 word.startTime/endTime 是【每次合成调用 session 相对秒】，逐句调用
  各自从 0 计——直接拼接原始 words 会使第 2 句起 karaoke 字幕静默错位。

⚠️ 复制本脚本时必须连同同目录的 `_doubao_tts.py` 一起复制——
本脚本运行时按同目录相对路径动态加载它（见下方 _DAOBAO_HELPER），
漏拷即报模块缺失。SKILL.md 第三步同样写明此要求。
"""
import subprocess, json, os, sys

# ============ doubao TTS (ONLY engine — edge-tts blocked by sandbox) ============
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DAOBAO_HELPER = os.path.join(_SCRIPTS_DIR, "_doubao_tts.py")

# ============ 音色配置（2026-10-12 C1：显式 speaker，勿静默落 env）============
# 在册授权豆包音色（单一事实源：backend/app/gateway/_shared/voice_catalog.py
# 的 CURATED_VOICES；查不到的 id 勿用——2026-10-12 事故会话 1 即测了未授权
# 音色 id 报 "resource ID is mismatched"）：
#   男声：zh_male_m191_uranus_bigtts（云舟2.0·稳重大气，课程视频主流）
#         zh_male_liufei_uranus_bigtts（刘飞2.0·富有表现力）
#   女声：zh_female_vv_uranus_bigtts（DOUBAO_TTS_DEFAULT_SPEAKER env 默认）
# 每次任务按需求确认音色后改此常量（或逐句传 speaker=...）——禁止无脑沿用默认
# （2026-10-12 事故：模板裸调 synthesize(text) 致音色静默落 env 女声，成片
# 女声 + 假注释谎称男声）。
SPEAKER = "zh_male_m191_uranus_bigtts"


def _doubao_missing_env(speaker=None):
    missing = []
    if not os.getenv("VOLCENGINE_VOICE_ACCESS_KEY", "").strip():
        missing.append("VOLCENGINE_VOICE_ACCESS_KEY")
    if speaker is None and not os.getenv("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip():
        missing.append("DOUBAO_TTS_DEFAULT_SPEAKER")
    return missing


def _doubao_available(speaker=None):
    """True when the access key and any fallback speaker are available."""
    return not _doubao_missing_env(speaker)


def _require_doubao(speaker):
    if _doubao_available(speaker=speaker):
        return
    missing = " / ".join(_doubao_missing_env(speaker))
    raise RuntimeError(
        f"doubao TTS env not set ({missing}); edge-tts is blocked in sandbox — not falling back"
    )


def _load_doubao_module():
    """Load the sibling _doubao_tts.py by same-dir relative path."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("_doubao_tts", _DAOBAO_HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _doubao_tts_generate(text, output_path, speaker=SPEAKER):
    """Synthesize via doubao seed-tts-2.0; write mp3 to output_path.

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底——None 会静默落
    env 默认女声，2026-10-12 事故形态）。Returns True on success, False on
    failure (caller raises RuntimeError — edge-tts is blocked by sandbox,
    no fallback per SKILL.md).
    """
    try:
        audio = _load_doubao_module().synthesize(text, speaker=speaker)
        with open(output_path, "wb") as f:
            f.write(audio)
        return True
    except Exception as e:
        print(f"WARNING: doubao TTS failed ({e}); edge-tts is blocked, no fallback")
        return False


def _doubao_tts_generate_timestamped(text, output_path, speaker=SPEAKER):
    """PREFERRED word-level mode: synthesize with enable_subtitle=true.

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底，同 _doubao_tts_generate）。
    Writes the mp3 to output_path; returns the word-level timestamp list
    ([{word, startTime, endTime, confidence}], session-relative seconds —
    convert to global ms via words_to_captions, see module docstring).
    Raises on synthesis failure. Returns [] when the service sent no subtitle
    events (additions.cache_config hit / ssml input) — that is NOT a failure:
    the audio is fine, only word captions are unavailable.
    """
    audio, words = _load_doubao_module().synthesize_with_timestamps(text, speaker=speaker)
    with open(output_path, "wb") as f:
        f.write(audio)
    return words


# ============ CONFIG ============
VIDEO_NAME = "my-video"  # Change this for each video
AUDIO_DIR = f"public/audio/{VIDEO_NAME}"
FPS = 30
BUFFER_FRAMES = 30  # 1s buffer after each scene (3s is too much dead air)
# 音色：见文件头「音色配置」区的 SPEAKER 常量（2026-10-12 起 template 显式
# 携带 speaker 参数——env 默认只是兜底，不再是事实音色）

# Scene texts - each sublist is one scene, each string is one sentence
SCENE_TEXTS = [
    # Scene 0: Intro
    [
        "大家好，欢迎观看这个视频。",
        "今天我们来了解一个有趣的主题。",
        "让我们开始吧！",
    ],
    # Scene 1: Main content
    [
        "这是视频的主要内容。",
        "我们会一步步讲解。",
    ],
    # Add more scenes as needed...
]

# Scene names (for file naming)
NAMES = ["intro", "main", "conclusion"]  # Match your scene count

# ============ SCRIPT ============
TMP_DIR = f"/tmp/{VIDEO_NAME}_tts"


def get_mp3_duration(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True
    )
    return float(r.stdout.strip())


def concat_mp3s(mp3_files, output_path):
    """Concatenate multiple mp3 files into one. Raises RuntimeError on ffmpeg failure."""
    list_file = os.path.join(TMP_DIR, "filelist.txt")
    with open(list_file, "w") as f:
        for p in mp3_files:
            f.write(f"file '{p}'\n")
    r = subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", output_path],
        capture_output=True
    )
    os.remove(list_file)
    if r.returncode != 0:
        raise RuntimeError(
            f"ffmpeg concat failed (rc={r.returncode}): "
            f"{r.stderr.decode(errors='replace')[:500]}"
        )


def generate_tts(text, output_path, speaker=SPEAKER):
    """Generate TTS audio via doubao ONLY. Returns None; raises RuntimeError on failure.

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底）——音色是可验证的
    交付参数，不是 env 隐式状态。
    edge-tts is blocked by sandbox egress isolation, so there is NO fallback —
    a doubao failure (env unset or synthesis error) raises RuntimeError so the
    agent reports honestly and stops, per SKILL.md ("不要回退 edge-tts").
    """
    _require_doubao(speaker)
    if not _doubao_tts_generate(text, output_path, speaker=speaker):
        raise RuntimeError(
            "doubao TTS synthesis failed; edge-tts is blocked in sandbox — "
            "not falling back (see auto-video-flow SKILL.md)"
        )


def generate_tts_timestamped(text, output_path, speaker=SPEAKER):
    """Word-level-first synthesis for one sentence; degrade per sentence.

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底，同 generate_tts）。
    Returns the words list ([] = audio fine but subtitles unavailable), or
    None after degrading this sentence to plain synthesis. A real synthesis
    failure still raises RuntimeError via generate_tts → 现状 abort semantics
    unchanged (edge-tts remains blocked, no engine fallback).
    """
    _require_doubao(speaker)
    try:
        return _doubao_tts_generate_timestamped(text, output_path, speaker=speaker)
    except Exception as e:
        print(f"WARNING: word-level TTS failed ({e}); degrading this sentence to "
              f"plain synthesis (captions fall back to ffprobe sentence-level)")
        generate_tts(text, output_path, speaker=speaker)
        return None


def words_to_captions(words, sentence_global_start_sec):
    """Convert one sentence's doubao words to global-ms captions (pure).

    契约见模块 docstring：startMs = round((句全局起始秒 + word.startTime) * 1000)。
    输出形状 = @remotion/captions Caption JSON（消费端 KaraokeCaptions.tsx 的
    WordCaption，全局毫秒时间轴——字幕组件挂 Composition 根部）。
    """
    captions = []
    for w in words:
        if not isinstance(w, dict) or "word" not in w:
            continue
        start_ms = round((sentence_global_start_sec + float(w["startTime"])) * 1000)
        end_ms = round((sentence_global_start_sec + float(w["endTime"])) * 1000)
        captions.append({
            "text": w["word"],
            "startMs": start_ms,
            "endMs": end_ms,
            "timestampMs": start_ms,
            "confidence": float(w.get("confidence", 0.0)),
        })
    return captions


def main():
    os.makedirs(TMP_DIR, exist_ok=True)
    os.makedirs(AUDIO_DIR, exist_ok=True)

    # Main generation loop
    all_subtitle_segments = []         # 场景内局部帧（VideoTemplate 路径）
    all_subtitle_segments_global = []  # 全局帧（DataDrivenVideo props 路径）
    all_captions = []                  # 词级全局毫秒（@remotion/captions 契约）
    captions_complete = True           # 任一句词级降级 → 整体不发 captions（防半截时间轴误导 karaoke）
    scene_durations = []
    completed_sentences = []  # 已完成的句子清单（失败中止时如实报告，不留静默半截产物）

    for si in range(len(SCENE_TEXTS)):
        print(f"\n=== Scene {si}: {NAMES[si]} ===")
        sentences = SCENE_TEXTS[si]
        mp3_files = []
        sentence_words = []  # 每句词级时间戳（None = 该句已降级普通合成）

        for j, text in enumerate(sentences):
            tmp_path = os.path.join(TMP_DIR, f"scene{si}_{j}.mp3")
            try:
                words = generate_tts_timestamped(text, tmp_path, speaker=SPEAKER)
            except RuntimeError as e:
                # 失败句记录后中止，并报告已完成的句子清单
                print(f"  ERROR generating: {text[:50]}... ({e})")
                print(f"\nABORTED at scene {si} ({NAMES[si]}) sentence {j}.")
                print(f"Completed sentences before abort: {len(completed_sentences)}")
                for s in completed_sentences:
                    print(f"    OK: {s[:60]}")
                sys.exit(1)
            completed_sentences.append(text)
            mp3_files.append(tmp_path)
            sentence_words.append(words)
            # speaker 随行打印：执行日志自证实际音色（不接受口头宣称，SKILL.md 第二步）
            print(f"  Generated: {text[:50]}... (speaker={SPEAKER})")

        # Concatenate all sentences for this scene
        output_path = os.path.join(AUDIO_DIR, f"{si+1:02d}-{NAMES[si]}.mp3")
        concat_mp3s(mp3_files, output_path)

        # Get total duration
        total_dur = get_mp3_duration(output_path)
        duration_frames = round(total_dur * FPS + BUFFER_FRAMES)
        scene_durations.append(duration_frames)

        # Calculate per-sentence timestamps for subtitles
        # 两套帧约定（勿混用）：
        #   all_subtitle_segments        场景内局部帧 → VideoTemplate.tsx 自定义组件
        #                                （字幕随场景挂 <Sequence> 内）
        #   all_subtitle_segments_global 全局帧（含前序场景 buffer 偏移）→
        #                                DataDrivenVideo 的 props.subtitleSegments
        #                                （字幕挂 Composition 根部，与后端 SRT 契约一致）
        scene_start_frame = sum(scene_durations[:-1])
        current_time = 0.0
        for j, text in enumerate(sentences):
            tmp_path = os.path.join(TMP_DIR, f"scene{si}_{j}.mp3")
            if not os.path.exists(tmp_path):
                continue
            dur = get_mp3_duration(tmp_path)
            sf = round(current_time * FPS)
            ef = round((current_time + dur) * FPS)
            all_subtitle_segments.append([si, sf, ef, text])
            all_subtitle_segments_global.append(
                [si, scene_start_frame + sf, scene_start_frame + ef, text]
            )
            # 词级 captions：豆包 words 是该句合成 session 相对秒，须换算全局毫秒
            # （契约见模块 docstring；句内偏移 = 前序句 ffprobe 实测时长和）
            words = sentence_words[j]
            if words:
                sentence_global_start_sec = scene_start_frame / FPS + current_time
                try:
                    all_captions.extend(
                        words_to_captions(words, sentence_global_start_sec)
                    )
                except Exception as e:
                    # 畸形 word 条目（缺时间键/非数值）不算合成失败——此时音频已全部
                    # 生成，崩在这里会丢整份 JSON；按降级语义继续出 segments
                    print(f"WARNING: word→caption conversion failed ({e}); "
                          f"captions degrade to ffprobe sentence-level")
                    captions_complete = False
            else:
                captions_complete = False
            current_time += dur
            print(f"  Subtitle: [{sf}-{ef}f local / {scene_start_frame + sf}-{scene_start_frame + ef}f global] {text[:40]}...")

        # Cleanup tmp files
        for f in mp3_files:
            if os.path.exists(f):
                os.remove(f)

        print(f"  Scene total: {total_dur:.1f}s = {duration_frames}f")

    # Summary
    total = sum(scene_durations)
    print(f"\n{'='*60}")
    print(f"TOTAL: {total} frames = {total/FPS:.1f}s")
    print(f"SUBTITLES: {len(all_subtitle_segments)}")

    # Save results
    output = {
        "video": VIDEO_NAME,
        "durations": scene_durations,
        "total": total,
        "segments": all_subtitle_segments,
        "segments_global": all_subtitle_segments_global,
    }
    if captions_complete and all_captions:
        output["captions"] = all_captions
        print(f"CAPTIONS: {len(all_captions)} words, global ms (@remotion/captions shape) — "
              f"props.captions 直用（消费端 KaraokeCaptions.tsx，挂 Composition 根部）")
    else:
        print("CAPTIONS: word-level unavailable (degraded) — "
              "use segments (ffprobe sentence-level) as before")

    output_file = f"scripts/tts_{VIDEO_NAME}.json"
    os.makedirs(os.path.dirname(output_file) or ".", exist_ok=True)
    with open(output_file, "w") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\nSaved to {output_file}")

    # Also output TypeScript format for easy copy
    def _ts_escape(s):
        """Escape text for embedding inside a TypeScript double-quoted string literal."""
        return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "")

    print("\n" + "="*60)
    print("TypeScript SCENES array (copy to your Video.tsx):")
    print("="*60)
    print("const SCENES = [")
    for i, name in enumerate(NAMES[:len(scene_durations)]):
        print(f"  {{ name: '{name}', duration: {scene_durations[i]} }},")
    print("];")

    print("\n" + "="*60)
    print("TypeScript SUBTITLE_SEGMENTS (copy to your Video.tsx):")
    print("="*60)
    print("const SUBTITLE_SEGMENTS: [number, number, number, string][] = [")
    for seg in all_subtitle_segments:
        print(f"  [{seg[0]}, {seg[1]}, {seg[2]}, \"{_ts_escape(seg[3])}\"],")
    print("];")

    return output


if __name__ == "__main__":
    main()
