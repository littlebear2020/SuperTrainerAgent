"""Video download script for the video-dl skill.

Wraps yt-dlp with proper options for Bilibili.
Outputs JSON result for the agent to consume.

2026-09-09 起仅支持 Bilibili（bilibili.com / b23.tv）——YouTube 等境外
平台在公司网络不可达，相关识别与指引已剥离（自闭环拍板项 1）。
"""
import argparse
import ipaddress
import json
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


def validate_url(url: str) -> None:
    """P2-11: Reject file://, private/internal IPs, and bare hostnames (SSRF).

    yt-dlp already blocks file:// by default, but http(s) SSRF is still possible--
    the sandbox shares a Docker network with gateway/ppt-converter, and the latter's
    shared secret defaults to empty. This guard prevents the sandbox from being used
    as a pivot to internal services.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"URL scheme '{parsed.scheme}' not allowed (only http/https)")
    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("URL has no hostname")
    # Bare hostnames (no dots) are internal Docker DNS names (e.g. "gateway")
    if "." not in hostname and hostname not in ("localhost",):
        raise ValueError(f"Internal hostname '{hostname}' not allowed")
    # Check if it's an IP literal
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        pass  # not an IP, it's a domain name - OK
    else:
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Private/reserved IP '{hostname}' not allowed")
    # Also check localhost
    if hostname == "localhost":
        raise ValueError(f"Localhost not allowed")


def detect_platform(url: str) -> str:
    if any(x in url for x in ["bilibili.com", "b23.tv"]):
        return "bilibili"
    return "unknown"


def download_video(url: str, output_dir: str, browser: str | None = None) -> dict:
    validate_url(url)  # P2-11: SSRF guard
    if detect_platform(url) != "bilibili":
        return {
            "success": False,
            "error": "仅支持 Bilibili 视频下载（bilibili.com / b23.tv）。YouTube 等境外平台已不支持。",
        }
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    cmd = [
        "yt-dlp",
        "--format", "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
        "--write-subs", "--write-auto-subs",
        "--sub-langs", "en,zh-Hans,zh,en-US",
        "--sub-format", "srt",
        "--output", str(out / "input.%(ext)s"),
        "--no-playlist",
        "--no-warnings",
        "--progress",
    ]
    if browser:
        cmd.extend(["--cookies-from-browser", browser])
    cmd.append(url)

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        return {"success": False, "error": result.stderr[-500:]}

    video_file = subtitle_file = None
    for f in sorted(out.iterdir()):
        if f.suffix == ".mp4":
            video_file = str(f)
        elif f.suffix == ".srt":
            subtitle_file = str(f)

    if video_file:
        target = out / "input.mp4"
        if Path(video_file) != target:
            Path(video_file).rename(target)
            video_file = str(target)
    if subtitle_file:
        target = out / "input.srt"
        if Path(subtitle_file) != target:
            Path(subtitle_file).rename(target)
            subtitle_file = str(target)

    return {
        "success": True,
        "video": video_file,
        "subtitle": subtitle_file,
        "platform": detect_platform(url),
        "output_dir": str(out),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Download video via yt-dlp")
    parser.add_argument("url", help="Video URL")
    parser.add_argument("-o", "--output-dir", required=True, help="Output directory")
    parser.add_argument("-b", "--browser", default=None, help="Browser for cookies (chrome, firefox, etc.)")
    args = parser.parse_args()

    result = download_video(args.url, args.output_dir, args.browser)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result["success"] else 1)
