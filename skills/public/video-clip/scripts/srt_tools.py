"""SRT subtitle tools for the video-clip skill.

Provides: parse, chunk by duration, extract text, segment extraction.
All commands output JSON for the agent to consume.
"""
import argparse
import json
import re
import sys
from pathlib import Path


def parse_srt(srt_path: str) -> list[dict]:
    content = Path(srt_path).read_text(encoding="utf-8")
    blocks = re.split(r"\n\s*\n", content.strip())
    entries = []
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        try:
            index = int(lines[0].strip())
        except ValueError:
            continue
        m = re.match(
            r"(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})",
            lines[1],
        )
        if not m:
            continue
        text = "\n".join(lines[2:]).strip()
        if text:
            entries.append({"index": index, "start": m.group(1), "end": m.group(2), "text": text})
    return entries


def time_to_seconds(t: str) -> float:
    t = t.replace(",", ".")
    parts = t.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def seconds_to_time(s: float) -> str:
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:06.3f}".replace(".", ",", 1)


def chunk_srt(entries: list[dict], chunk_minutes: int = 30) -> list[dict]:
    chunks: list[dict] = []
    current: list[dict] = []
    chunk_start = 0.0

    for entry in entries:
        start = time_to_seconds(entry["start"])
        if not current:
            chunk_start = start
        current.append(entry)
        if start - chunk_start >= chunk_minutes * 60:
            chunks.append({
                "chunk_index": len(chunks),
                "entries": current,
                "text": "\n".join(e["text"] for e in current),
                "start_time": current[0]["start"],
                "end_time": current[-1]["end"],
            })
            current = []

    if current:
        chunks.append({
            "chunk_index": len(chunks),
            "entries": current,
            "text": "\n".join(e["text"] for e in current),
            "start_time": current[0]["start"],
            "end_time": current[-1]["end"],
        })

    return chunks


def extract_segment(entries: list[dict], start_time: str, end_time: str) -> list[dict]:
    """Extract entries within a time range."""
    start_s = time_to_seconds(start_time)
    end_s = time_to_seconds(end_time)
    return [
        e for e in entries
        if time_to_seconds(e["start"]) >= start_s - 0.5
        and time_to_seconds(e["end"]) <= end_s + 0.5
    ]


def main():
    parser = argparse.ArgumentParser(description="SRT subtitle tools")
    parser.add_argument("command", choices=["parse", "chunk", "segment"])
    parser.add_argument("input", help="SRT file path")
    parser.add_argument("-o", "--output", default=None, help="Output file path")
    parser.add_argument("--chunk-minutes", type=int, default=30)
    parser.add_argument("--start", default=None, help="Segment start time")
    parser.add_argument("--end", default=None, help="Segment end time")
    args = parser.parse_args()

    entries = parse_srt(args.input)

    if args.command == "parse":
        result = entries
    elif args.command == "chunk":
        result = chunk_srt(entries, args.chunk_minutes)
    elif args.command == "segment":
        if not args.start or not args.end:
            print(json.dumps({"error": "--start and --end required for segment"}))
            sys.exit(1)
        result = extract_segment(entries, args.start, args.end)
    else:
        result = []

    output = json.dumps(result, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(output, encoding="utf-8")
        print(json.dumps({"success": True, "count": len(result), "output": args.output}))
    else:
        print(output)


if __name__ == "__main__":
    main()
