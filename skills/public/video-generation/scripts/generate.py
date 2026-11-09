"""
Video generation using MiniMax Hailuo 2.3 via 360 API (api.360.cn).

API spec:
  POST https://api.360.cn/v1/videos/async_generations   (create task)
  GET  https://api.360.cn/v1/videos/async_generations/{id}  (poll status)
  Auth: Bearer {QIHU_API_KEY}
  Model: minimax/MiniMax-Hailuo-2.3-no-safety_checker
"""

import os
import sys
import time

import requests

# Bypass Docker-inherited HTTP proxy for external API calls
_NO_PROXY = {"http": None, "https": None}

QIHU_BASE_URL = os.getenv("QIHU_BASE_URL", "https://api.360.cn").rstrip("/")
MODEL = "minimax/MiniMax-Hailuo-2.3-no-safety_checker"
DEFAULT_DURATION = 8  # seconds


def generate_video(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    duration: int = DEFAULT_DURATION,
) -> str:
    # Stage 5.3: seedance-ark preferred (字节默认首选, supports ref i2v/r2v); 360 Hailuo fallback.
    if os.getenv("ARK_API_KEY"):
        try:
            return _generate_with_seedance_ark(
                prompt_file, reference_images, output_file, duration
            )
        except Exception as e:
            print(f"Warning: seedance-ark failed ({e}); falling back to 360 Hailuo")
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read().strip()

    api_key = os.getenv("QIHU_API_KEY")
    if not api_key:
        return "QIHU_API_KEY is not set"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    # Build content array
    content = [{"type": "text", "text": prompt}]
    if reference_images:
        for img_path in reference_images:
            img_url = _resolve_image_url(img_path)
            content.append({
                "type": "image_url",
                "image_url": {"url": img_url},
            })

    payload = {
        "model": MODEL,
        "content": content,
        "duration": duration,
    }

    print(f"Creating video task (model={MODEL}, duration={duration}s)...")
    task_id = _create_task(headers, payload)
    video_url = _poll_task(headers, task_id)
    _download_video(video_url, output_file)

    return f"The video has been generated successfully to {output_file}"


def _resolve_image_url(img_path: str) -> str:
    """If the path looks like a URL, return as-is; otherwise base64-encode."""
    if img_path.startswith(("http://", "https://")):
        return img_path
    with open(img_path, "rb") as f:
        import base64
        ext = img_path.rsplit(".", 1)[-1].lower()
        mime_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}
        mime = mime_map.get(ext, "jpeg")
        b64 = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/{mime};base64,{b64}"


def _generate_with_seedance_ark(
    prompt_file: str,
    reference_images: list[str],
    output_file: str,
    duration: int = DEFAULT_DURATION,
) -> str:
    """ByteDance Ark seedance-ark video generation (Stage 5.3).

    Ark /contents/generations/tasks (submit+poll), generate_audio=false (防双音轨).
    Refs → content entries with role:first_frame (first) / last_frame (rest). 360 Hailuo
    is the fallback if this raises. See backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 5.3.
    """
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read().strip()

    api_key = os.getenv("ARK_API_KEY")
    if not api_key:
        raise Exception("ARK_API_KEY is not set")
    base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").rstrip("/")
    model = os.getenv("SEEDANCE_ARK_VIDEO_MODEL", "doubao-seedance-2-0-260128")
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    content: list[dict] = [{"type": "text", "text": prompt}]
    for i, img_path in enumerate(reference_images):
        role = "first_frame" if i == 0 else "last_frame"
        content.append({"type": "image_url", "image_url": {"url": _resolve_image_url(img_path)}, "role": role})

    payload = {"model": model, "content": content, "generate_audio": False}
    print(f"Creating seedance-ark video task (model={model}, duration={duration}s)...")
    task_id = _create_ark_task(headers, payload, base_url)
    video_url = _poll_ark_task(headers, task_id, base_url)
    _download_video(video_url, output_file)
    return f"The video has been generated successfully to {output_file}"


def _create_ark_task(headers: dict, payload: dict, base_url: str) -> str:
    url = f"{base_url}/contents/generations/tasks"
    resp = requests.post(url, headers=headers, json=payload, timeout=120, proxies=_NO_PROXY)
    resp.raise_for_status()
    data = resp.json()
    task_id = data.get("id")
    if not task_id:
        raise Exception(f"No task id in seedance-ark response: {resp.text[:500]}")
    print(f"seedance-ark task created: {task_id}")
    return task_id


def _poll_ark_task(headers: dict, task_id: str, base_url: str, interval: int = 15, max_wait: int = 600) -> str:
    url = f"{base_url}/contents/generations/tasks/{task_id}"
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        resp = requests.get(url, headers=headers, timeout=60, proxies=_NO_PROXY)
        resp.raise_for_status()
        data = resp.json()
        status = str(data.get("status", "unknown")).lower()
        print(f"seedance-ark task status: {status} ({elapsed}s elapsed)")
        if status in ("succeeded", "success"):
            content = data.get("content") or {}
            video_url = content.get("video_url") if isinstance(content, dict) else None
            if not video_url:
                raise Exception(f"seedance-ark task succeeded but no video_url: {data}")
            return video_url
        elif status in ("failed", "error"):
            # content-review etc → user-visible error (no fallback per requirement #4)
            c = data.get("content") if isinstance(data.get("content"), dict) else {}
            reason = c.get("fail_code") or c.get("failure_reason") or data.get("error") or "unknown"
            raise Exception(f"seedance-ark task failed: {reason}")
    raise Exception(f"seedance-ark task timed out after {max_wait}s")


def _create_task(headers: dict, payload: dict) -> str:
    url = f"{QIHU_BASE_URL}/v1/videos/async_generations"
    resp = requests.post(url, headers=headers, json=payload, timeout=120, proxies=_NO_PROXY)
    resp.raise_for_status()
    data = resp.json()
    task_data = data.get("data", {})
    task_id = task_data.get("id")
    if not task_id:
        raise Exception(f"No task id in response: {resp.text[:500]}")
    status = task_data.get("status", "unknown")
    print(f"Task created: {task_id} (status={status})")
    return task_id


def _poll_task(headers: dict, task_id: str, interval: int = 15, max_wait: int = 600) -> str:
    url = f"{QIHU_BASE_URL}/v1/videos/async_generations/{task_id}"
    elapsed = 0
    while elapsed < max_wait:
        time.sleep(interval)
        elapsed += interval
        resp = requests.get(url, headers=headers, timeout=60, proxies=_NO_PROXY)
        resp.raise_for_status()
        data = resp.json()
        task_data = data.get("data", {})
        status = task_data.get("status", "unknown")
        print(f"Task status: {status} ({elapsed}s elapsed)")

        if status.lower() == "success":
            video_url = task_data.get("url")
            if not video_url:
                raise Exception("Task succeeded but no video url returned")
            return video_url
        elif status.lower() in ("failed", "error"):
            reason = task_data.get("failure_reason", "Unknown")
            raise Exception(f"Task failed: {reason}")
        # status in ("created", "processing", "running") -> keep polling

    raise Exception(f"Task timed out after {max_wait}s")


def _download_video(video_url: str, output_file: str):
    print(f"Downloading video from: {video_url[:120]}...")
    resp = requests.get(video_url, timeout=300, proxies=_NO_PROXY)
    resp.raise_for_status()
    with open(output_file, "wb") as f:
        f.write(resp.content)
    size_mb = len(resp.content) / (1024 * 1024)
    print(f"Video saved to {output_file} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate videos using Google Veo 3.0 via 360 API")
    parser.add_argument(
        "--prompt-file",
        required=True,
        help="Absolute path to text prompt file",
    )
    parser.add_argument(
        "--reference-images",
        nargs="*",
        default=[],
        help="Absolute paths to reference images (space-separated)",
    )
    parser.add_argument(
        "--output-file",
        required=True,
        help="Output path for generated video (e.g. .mp4)",
    )
    parser.add_argument(
        "--duration",
        type=int,
        default=DEFAULT_DURATION,
        help=f"Video duration in seconds (default: {DEFAULT_DURATION})",
    )

    args = parser.parse_args()

    try:
        result = generate_video(
            prompt_file=args.prompt_file,
            reference_images=args.reference_images,
            output_file=args.output_file,
            duration=args.duration,
        )
        print(result)
    except Exception as e:
        print(f"Error while generating video: {e}")
        sys.exit(1)
