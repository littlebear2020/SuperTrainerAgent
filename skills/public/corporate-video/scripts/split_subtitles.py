#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
字幕拆分脚本 — 将长字幕按逗号拆分为短子句，时间按字符数比例分配
用法: python3 scripts/split_subtitles.py src/subtitles.json [max_chars=35]
"""

import json, re, sys

MAX_CHARS = int(sys.argv[2]) if len(sys.argv) > 2 else 35
INPUT = sys.argv[1] if len(sys.argv) > 1 else "src/subtitles.json"

def split_subtitle(sub):
    text = sub['text']
    sf, ef = sub['startFrame'], sub['endFrame']
    total_frames = ef - sf

    if len(text) <= MAX_CHARS:
        return [sub]

    # 按中文逗号、分号、冒号拆分，分隔符跟后半部分
    parts = re.split(r'(?<=[，；、：])', text)
    parts = [p.strip() for p in parts if p.strip()]

    if len(parts) <= 1:
        return [sub]

    total_chars = sum(len(p) for p in parts)
    result = []
    current_frame = sf

    for i, part in enumerate(parts):
        ratio = len(part) / total_chars
        part_frames = round(total_frames * ratio)
        if i == len(parts) - 1:
            part_frames = ef - current_frame
        result.append({
            "text": part,
            "startFrame": current_frame,
            "endFrame": current_frame + part_frames,
        })
        current_frame += part_frames

    return result

def main():
    with open(INPUT, 'r', encoding='utf-8') as f:
        data = json.load(f)

    new_subs = {}
    total_before = 0
    total_after = 0
    long_after = 0

    for key, scene_subs in data.items():
        expanded = []
        for sub in scene_subs:
            expanded.extend(split_subtitle(sub))
        new_subs[key] = expanded
        total_before += len(scene_subs)
        total_after += len(expanded)
        long_after += sum(1 for s in expanded if len(s['text']) > MAX_CHARS)

    with open(INPUT, 'w', encoding='utf-8') as f:
        json.dump(new_subs, f, ensure_ascii=False, indent=2)

    print(f"Split: {total_before} -> {total_after} subtitles (max {MAX_CHARS} chars)")
    print(f"Still >{MAX_CHARS} chars: {long_after}")
    print(f"Updated: {INPUT}")

if __name__ == "__main__":
    main()
