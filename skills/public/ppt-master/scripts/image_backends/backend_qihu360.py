#!/usr/bin/env python3
"""
360 Seedream image generation backend (TrainerAI primary channel).

Contract verified against the 360 gateway (:mod:`backend.app.gateway.video_gen.providers.qihu_image`):
- Text-to-image: ``POST {base_url}/v1/images/generations``
- Reference-image edit: ``POST {base_url}/v1/images/edits`` with the reference
  image carried in ``extra_body.image`` (single data URI).
- Body: ``model`` + ``prompt`` + ``extra_body``; size, watermark, and
  ``output_format=png`` live inside ``extra_body`` (a top-level ``size`` is silently remapped by the gateway, so
  it is never sent). ``sequential_image_generation`` is rejected by
  ``doubao-seedream-5.0-pro`` and is never sent either.
- Size is normalized to the three exact Seedream tiers:
  ``2048x1152`` / ``2048x2048`` / ``1152x2048``.

Configuration keys:
  QIHU_API_KEY    (required)
  QIHU_BASE_URL   (optional; default https://api.360.cn)
  QIHU_MODEL      (optional; only bytedance/doubao-seedream-5.0-pro supported)

Dependencies:
  pip install requests Pillow
"""

import sys
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1]
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from console_encoding import configure_utf8_stdio  # noqa: E402

configure_utf8_stdio()

if __name__ == "__main__":
    print(__doc__)
    print("Use via: python3 skills/ppt-master/scripts/image_gen.py \"prompt\" --backend qihu360")
    raise SystemExit(0 if any(arg in {"-h", "--help", "help"} for arg in sys.argv[1:]) else 1)

import base64
import os
import re
import time

import requests

from image_backends.backend_common import (
    MAX_RETRIES,
    download_image,
    http_error,
    is_permanent_error,
    is_rate_limit_error,
    require_api_key,
    resolve_output_path,
    retry_delay,
    save_image_bytes,
)

DEFAULT_BASE_URL = "https://api.360.cn"
DEFAULT_MODEL = "bytedance/doubao-seedream-5.0-pro"
SUPPORTED_MODELS = {DEFAULT_MODEL}
MAX_REFERENCE_IMAGE_BYTES = 10 * 1024 * 1024

# 360/Seedream 精确三档（16:9 / 1:1 / 9:16 的 2K 档）。
_SIZE_TIERS: tuple[tuple[int, int], ...] = ((2048, 1152), (2048, 2048), (1152, 2048))

# Signals to image_gen.py that this backend accepts --reference-image.
SUPPORTS_REFERENCE_IMAGE = True

_RATIO_RE = re.compile(r"^(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)$")
_LOCAL_REF_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _validate_model(model: str) -> str:
    resolved = (model or "").strip()
    if resolved not in SUPPORTED_MODELS:
        raise ValueError(
            f"Unsupported 360 image model '{model}'. Supported: {sorted(SUPPORTED_MODELS)}"
        )
    return resolved


def _resolve_size(aspect_ratio: str) -> str:
    """Map an aspect ratio to the nearest exact Seedream tier (linear ratio distance)."""
    match = _RATIO_RE.match(str(aspect_ratio).strip())
    if not match:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}' for 360 image backend")
    width, height = float(match.group(1)), float(match.group(2))
    if width <= 0 or height <= 0:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}' for 360 image backend")
    ratio = width / height
    best = min(_SIZE_TIERS, key=lambda tier: abs(tier[0] / tier[1] - ratio))
    return f"{best[0]}x{best[1]}"


def _resolve_url(base_url: str, *, reference_image: str | None) -> str:
    base = base_url.rstrip("/")
    path = "/v1/images/edits" if reference_image else "/v1/images/generations"
    if base.endswith("/v1"):
        return base + path[len("/v1") :]
    return base + path


def _reference_to_data_uri(reference_image: str) -> str:
    """Pass URLs/data URIs through; convert a supported local image up to 10 MB."""
    if reference_image.startswith(("http://", "https://", "data:")):
        return reference_image
    path = Path(reference_image)
    if not path.is_file():
        raise FileNotFoundError(f"Reference image not found: {reference_image}")
    mime = _LOCAL_REF_MIME.get(path.suffix.lower())
    if mime is None:
        supported = ", ".join(sorted(_LOCAL_REF_MIME))
        raise ValueError(
            f"Unsupported reference image extension '{path.suffix}'. "
            f"Supported extensions: {supported}"
        )
    size_bytes = path.stat().st_size
    if size_bytes > MAX_REFERENCE_IMAGE_BYTES:
        raise ValueError(
            f"Reference image exceeds the 10 MB limit ({size_bytes} bytes): "
            f"{reference_image}"
        )
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _generate_image(
    api_key: str,
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str = "2K",
    output_dir: str = None,
    filename: str = None,
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    reference_image: str | None = None,
) -> str:
    """Generate/edit one image with the 360 Seedream gateway."""
    model = _validate_model(model)
    size = _resolve_size(aspect_ratio)
    if str(image_size).strip().upper() != "2K":
        print(
            f"Warning: qihu360 image_size={image_size!r} is not 2K; "
            f"using the fixed 2K tier {size} for aspect_ratio={aspect_ratio}.",
            file=sys.stderr,
        )
    extra_body: dict = {
        "size": size,
        "watermark": False,
        "output_format": "png",
    }
    body: dict = {
        "model": model,
        "prompt": prompt,
        # ⚠️ 顶层不发 size；禁发 sequential_image_generation（5.0-pro 实测 400）。
        "extra_body": extra_body,
    }
    if reference_image:
        extra_body["image"] = _reference_to_data_uri(reference_image)
    url = _resolve_url(base_url, reference_image=reference_image)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    print("[360 Seedream]")
    print(f"  Model:        {model}")
    print(f"  Prompt:       {prompt[:120]}{'...' if len(prompt) > 120 else ''}")
    print(f"  Aspect Ratio: {aspect_ratio}")
    print(f"  Resolution:   {size}")
    if reference_image:
        print(f"  Reference:    {reference_image}")
    print()
    print("  [..] Generating...", end="", flush=True)
    start = time.time()
    response = requests.post(url, headers=headers, json=body, timeout=300)
    elapsed = time.time() - start
    print(f"\n  [DONE] Response received ({elapsed:.1f}s)")

    if response.status_code != 200:
        raise http_error(response, "360 image generation")

    data = response.json()
    items = data.get("data") or []
    if not items:
        raise RuntimeError(f"360 image API returned empty data: {data}")
    item = items[0]
    path = resolve_output_path(prompt, output_dir, filename, ".png")
    image_url = item.get("url")
    encoded_image = item.get("b64_json")
    url_error: Exception | None = None
    if image_url:
        try:
            return download_image(image_url, path)
        except Exception as exc:
            if not encoded_image:
                raise
            url_error = exc
    if encoded_image:
        try:
            image_bytes = base64.b64decode(encoded_image)
            return save_image_bytes(image_bytes, path)
        except Exception as exc:
            if url_error is not None:
                raise RuntimeError(
                    "360 image URL download and b64 fallback failed: "
                    f"{url_error}; {exc}"
                ) from exc
            raise
    raise RuntimeError(f"360 image API response missing url/b64_json: {item}")


def generate(
    prompt: str,
    aspect_ratio: str = "1:1",
    image_size: str = "2K",
    output_dir: str = None,
    filename: str = None,
    model: str = None,
    max_retries: int = MAX_RETRIES,
    reference_image: str = None,
) -> str:
    """Generate one image with retries using the 360 Seedream backend."""
    resolved_model = model or os.getenv("QIHU_MODEL") or DEFAULT_MODEL
    _validate_model(resolved_model)
    _resolve_size(aspect_ratio)
    api_key = require_api_key(
        "QIHU_API_KEY",
        message=(
            "No API key found. Set QIHU_API_KEY in the current environment or a .env file."
        ),
    )
    base_url = os.getenv("QIHU_BASE_URL") or DEFAULT_BASE_URL

    last_error = None
    for attempt in range(max_retries + 1):
        try:
            return _generate_image(
                api_key=api_key,
                prompt=prompt,
                aspect_ratio=aspect_ratio,
                image_size=image_size,
                output_dir=output_dir,
                filename=filename,
                model=resolved_model,
                base_url=base_url,
                reference_image=reference_image,
            )
        except Exception as exc:
            last_error = exc
            if is_permanent_error(exc):
                raise
            if attempt >= max_retries:
                break
            limited = is_rate_limit_error(exc)
            delay = retry_delay(attempt, rate_limited=limited)
            label = "Rate limit hit" if limited else f"Error: {exc}"
            print(f"\n  [WARN] {label}. Retrying in {delay}s...")
            time.sleep(delay)

    raise RuntimeError(f"Failed after {max_retries + 1} attempts. Last error: {last_error}")
