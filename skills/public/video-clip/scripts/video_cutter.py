"""Video cutting tools for the video-clip skill.

Provides: single clip cut, batch cut from JSON, concatenation, batch concat, thumbnail.
All commands output JSON for the agent to consume.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path


def sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"|?*\\/]', "", name)
    name = name.strip(" .")
    return name[:100]


def time_to_seconds(t: str) -> float:
    t = t.replace(",", ".")
    parts = t.split(":")
    return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])


def cut_clip(video: str, start: str, end: str, output: str) -> dict:
    start_s = time_to_seconds(start)
    end_s = time_to_seconds(end)
    duration = end_s - start_s

    result = subprocess.run(
        ["ffmpeg", "-ss", str(start_s), "-i", video,
         "-t", str(duration),
         "-c:v", "copy", "-c:a", "copy",
         "-avoid_negative_ts", "make_zero",
         "-y", output],
        capture_output=True, text=True,
    )
    return {
        "success": result.returncode == 0,
        "output": output,
        "error": result.stderr[-300:] if result.returncode != 0 else None,
    }


def concat_clips(clip_paths: list[str], output: str) -> dict:
    concat_file = str(Path(output).parent / "_concat_list.txt")
    with open(concat_file, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")

    result = subprocess.run(
        ["ffmpeg", "-f", "concat", "-safe", "0", "-i", concat_file,
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
         "-c:a", "aac", "-b:a", "128k",
         "-movflags", "+faststart",
         "-y", output],
        capture_output=True, text=True,
    )
    Path(concat_file).unlink(missing_ok=True)
    return {
        "success": result.returncode == 0,
        "output": output,
        "error": result.stderr[-300:] if result.returncode != 0 else None,
    }


def thumbnail(video: str, timestamp: str, output: str) -> dict:
    sec = time_to_seconds(timestamp)
    result = subprocess.run(
        ["ffmpeg", "-i", video, "-ss", str(sec),
         "-vframes", "1", "-q:v", "2", "-y", output],
        capture_output=True, text=True,
    )
    return {"success": result.returncode == 0, "output": output}


def batch_cut(video: str, json_file: str, output_dir: str) -> dict:
    clips = json.loads(Path(json_file).read_text(encoding="utf-8"))
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for clip in clips:
        cid = clip.get("id", "0")
        title = sanitize_filename(
            clip.get("generated_title") or clip.get("title") or f"clip_{cid}"
        )
        out = str(Path(output_dir) / f"{cid}_{title}.mp4")
        r = cut_clip(video, clip["start_time"], clip["end_time"], out)
        r["clip_id"] = cid
        r["title"] = title
        results.append(r)
    ok = all(r["success"] for r in results)
    return {"batch_results": results, "total": len(results), "success": ok}


def batch_concat(clips_json: str, output_dir: str) -> dict:
    """Concat clips into collection videos."""
    collections = json.loads(Path(clips_json).read_text(encoding="utf-8"))
    Path(output_dir).mkdir(parents=True, exist_ok=True)
    results = []
    for coll in collections:
        title = sanitize_filename(coll.get("collection_title", f"collection_{coll.get('id', 0)}"))
        out = str(Path(output_dir) / f"{title}.mp4")
        r = concat_clips(coll["clip_paths"], out)
        r["collection_title"] = title
        results.append(r)
    ok = all(r["success"] for r in results)
    return {"batch_results": results, "total": len(results), "success": ok}


def main():
    parser = argparse.ArgumentParser(description="Video cutting tools")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("cut")
    p.add_argument("-v", "--video", required=True)
    p.add_argument("-s", "--start", required=True)
    p.add_argument("-e", "--end", required=True)
    p.add_argument("-o", "--output", required=True)

    p = sub.add_parser("batch-cut")
    p.add_argument("-v", "--video", required=True)
    p.add_argument("-j", "--json-file", required=True)
    p.add_argument("-o", "--output-dir", required=True)

    p = sub.add_parser("concat")
    p.add_argument("-i", "--inputs", nargs="+", required=True)
    p.add_argument("-o", "--output", required=True)

    p = sub.add_parser("batch-concat")
    p.add_argument("-j", "--json-file", required=True)
    p.add_argument("-o", "--output-dir", required=True)

    p = sub.add_parser("thumbnail")
    p.add_argument("-v", "--video", required=True)
    p.add_argument("-t", "--timestamp", required=True)
    p.add_argument("-o", "--output", required=True)

    args = parser.parse_args()

    if args.command == "cut":
        result = cut_clip(args.video, args.start, args.end, args.output)
    elif args.command == "batch-cut":
        result = batch_cut(args.video, args.json_file, args.output_dir)
    elif args.command == "concat":
        result = concat_clips(args.inputs, args.output)
    elif args.command == "batch-concat":
        result = batch_concat(args.json_file, args.output_dir)
    elif args.command == "thumbnail":
        result = thumbnail(args.video, args.timestamp, args.output)
    else:
        parser.print_help()
        sys.exit(1)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
