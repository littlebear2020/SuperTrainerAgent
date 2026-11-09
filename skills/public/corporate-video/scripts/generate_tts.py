#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TTS 音频生成 + 字幕时间戳脚本
用法: python3 -u scripts/generate_tts.py
输出: public/audio/<VIDEO_NAME>/XX-name.mp3 + scripts/tts_<VIDEO_NAME>.json
"""

import subprocess, json, os, sys, re, shutil

# ============ 配置项 ============
VIDEO_NAME = "corporate-video"    # 修改为你的视频名称
FPS = 30
BUFFER_FRAMES = 30                # 场景间隔缓冲 (30=1秒)
# 音色：SPEAKER 常量显式下发（见下方「音色配置」区）；env 默认只是兜底，
# 不再是事实音色（2026-10-12 C3 同 auto-video-flow 修复——模板裸调曾致
# 音色静默落 env 女声）

# ============ doubao TTS（ONLY engine — edge-tts blocked by sandbox）============
_SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
_DAOBAO_HELPER = os.path.join(_SCRIPTS_DIR, "_doubao_tts.py")

# ============ 音色配置（2026-10-12 C3：显式 speaker，勿静默落 env）============
# 在册授权豆包音色（单一事实源：backend/app/gateway/_shared/voice_catalog.py
# 的 CURATED_VOICES；查不到的 id 勿用）：
#   男声：zh_male_m191_uranus_bigtts（云舟2.0·稳重大气）
#         zh_male_liufei_uranus_bigtts（刘飞2.0·富有表现力）
#   女声：zh_female_vv_uranus_bigtts（DOUBAO_TTS_DEFAULT_SPEAKER env 默认）
# 每次任务按需求确认音色后改此常量（或逐句传 speaker=...）——禁止无脑沿用默认。
SPEAKER = "zh_male_m191_uranus_bigtts"


def _doubao_available():
    """True when doubao env vars are set."""
    return bool(os.getenv("DOUBAO_TTS_DEFAULT_SPEAKER", "").strip()
                and os.getenv("VOLCENGINE_VOICE_ACCESS_KEY", "").strip())


def _doubao_tts_one(text, output_path, speaker=SPEAKER):
    """Synthesize via doubao seed-tts-2.0; write mp3 to output_path.

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底——None 会静默落
    env 默认女声，2026-10-12 事故形态）。
    Raises RuntimeError on any failure (edge-tts 必失败，任何技能不得回退——
    调用方如实报错中止).
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("_doubao_tts", _DAOBAO_HELPER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    audio = mod.synthesize(text, speaker=speaker)
    with open(output_path, "wb") as f:
        f.write(audio)

# 场景名称数组（与文本文件中的场景顺序一一对应）
NAMES = [
    "scene-01",
]

# 讲解稿文本文件路径（每场景用 "=== SCENE N ===" 分隔）
# ⚠️ 重要：讲解稿放在外部 txt 文件中，不要硬编码在 Python 脚本里
#    中文引号（""）会导致 Python 字符串转义问题
TEXTS_FILE = os.path.join(os.path.dirname(__file__), f'{VIDEO_NAME}_texts.txt')

# ============ 以下一般不需要修改 ============

AUDIO_DIR = f"public/audio/{VIDEO_NAME}"
TMP_DIR = f"/tmp/{VIDEO_NAME}_tts"


def split_sentences(text):
    """按中文句号、问号、叹号、分号拆分句子"""
    parts = re.split(r'(?<=[。！？；])', text)
    return [p.strip() for p in parts if p.strip()]


def tts_one(text, output_path, speaker=SPEAKER):
    """生成单句音频 — 只用豆包 TTS（edge-tts 必失败，任何技能不得回退）。

    speaker 显式下发（默认 = SPEAKER 常量，不设 None 兜底）。
    失败抛 SystemExit(1) 并如实报告——不产生静默半截产物。
    """
    if not _doubao_available():
        print(
            "TTS ERROR: doubao env 未设置（VOLCENGINE_VOICE_ACCESS_KEY / "
            "DOUBAO_TTS_DEFAULT_SPEAKER）；edge-tts 在沙箱出网隔离下必失败，不回退",
            file=sys.stderr,
        )
        sys.exit(1)
    try:
        _doubao_tts_one(text, output_path, speaker=speaker)
    except Exception as e:
        print(f"TTS ERROR: doubao 合成失败（{e}）；edge-tts 必失败，不回退", file=sys.stderr)
        sys.exit(1)


def concat_audio(files, output_path):
    """用 ffmpeg 拼接多个音频文件"""
    if len(files) == 1:
        os.rename(files[0], output_path)
        return
    list_file = os.path.join(TMP_DIR, "filelist.txt")
    with open(list_file, "w") as f:
        for fp in files:
            f.write(f"file '{fp}'\n")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
         "-i", list_file, "-c", "copy", output_path],
        capture_output=True, timeout=120,
    )


def get_dur(path):
    """获取音频时长（秒）"""
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True, timeout=30,
    )
    return float(r.stdout.strip())


def main():
    # 读取讲解稿
    with open(TEXTS_FILE, 'r', encoding='utf-8') as f:
        content = f.read()

    scenes_raw = re.split(r'=== SCENE \d+ ===\n', content)
    scenes_raw = [s.strip() for s in scenes_raw if s.strip()]
    assert len(scenes_raw) == len(NAMES), \
        f"场景数量不匹配: 文本中有 {len(scenes_raw)} 个, NAMES 有 {len(NAMES)} 个"

    os.makedirs(AUDIO_DIR, exist_ok=True)
    if os.path.exists(TMP_DIR):
        shutil.rmtree(TMP_DIR)
    os.makedirs(TMP_DIR, exist_ok=True)

    all_durs = []
    all_subtitles = []

    for i, text in enumerate(scenes_raw):
        name = NAMES[i]
        sentences = split_sentences(text)
        tmp_files = []
        scene_subs = []
        scene_start_sec = 0.0

        for j, sent in enumerate(sentences):
            tmp_path = os.path.join(TMP_DIR, f"{i:02d}_{j:02d}.mp3")
            tts_one(sent, tmp_path)
            tmp_files.append(tmp_path)

            dur = get_dur(tmp_path)
            start_frame = round(scene_start_sec * FPS)
            end_frame = round((scene_start_sec + dur) * FPS)
            scene_subs.append({
                "text": sent,
                "startFrame": start_frame,
                "endFrame": end_frame,
            })
            scene_start_sec += dur

        out_path = os.path.join(AUDIO_DIR, f"{i+1:02d}-{name}.mp3")
        concat_audio(tmp_files, out_path)
        total_dur = get_dur(out_path)
        frames = round(total_dur * FPS + BUFFER_FRAMES)
        all_durs.append(frames)
        all_subtitles.append(scene_subs)
        # speaker 随行打印：执行日志自证实际音色（不接受口头宣称）
        print(f"[{i+1}/{len(scenes_raw)}] {name}: {total_dur:.1f}s = {frames}f ({len(sentences)} sentences, speaker={SPEAKER})")

    total = sum(all_durs)
    print(f"\nTOTAL: {total} frames = {total/FPS:.1f}s")

    # 保存 JSON（含字幕时间戳）
    data = {
        "video": VIDEO_NAME, "bufferFrames": BUFFER_FRAMES,
        "fps": FPS, "durations": all_durs, "total": total,
        "scenes": NAMES, "subtitles": all_subtitles,
    }
    json_path = f"scripts/tts_{VIDEO_NAME}.json"
    with open(json_path, "w", encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    # 终端输出：直接复制到 TSX
    print(f"\n// === SCENES (复制到 SecurityVideo.tsx) ===")
    print("const SCENES = [")
    for i, name in enumerate(NAMES):
        print(f"  {{ name: '{name}', duration: {all_durs[i]} }},")
    print("];")
    print(f"// totalFrames = {total}")

    # 同时输出 subtitles.json 供 TSX import
    subs_for_tsx = {}
    for i, subs in enumerate(all_subtitles):
        subs_for_tsx[str(i)] = subs
    subs_json_path = "src/subtitles.json"
    with open(subs_json_path, "w", encoding='utf-8') as f:
        json.dump(subs_for_tsx, f, ensure_ascii=False, indent=2)
    print(f"\n// 字幕数据已保存: {subs_json_path}")
    print(f"// 在 TSX 中: import subtitlesData from './subtitles.json'")

    shutil.rmtree(TMP_DIR, ignore_errors=True)
    print(f"\n时间数据: {json_path}")
    print("Done!")


if __name__ == "__main__":
    main()
