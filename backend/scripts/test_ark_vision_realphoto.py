#!/usr/bin/env python
"""Stage 0 follow-up: real-photo vision re-test for doubao (Ark direct), streaming.

Streaming avoids the ReadTimeout that blocked the non-streaming version on
complex images. Two tests:
  A. PIL image: red rect + blue circle + text "CLAUDE 2026" (OCR-level bar).
  B. fresh seedream cat image (complex real image) passed as a URL.

The earlier solid-color test (test_ark_stage0.py TEST 2) already proved the
model accepts + describes image input; this raises the bar to text + complex.

Reads ARK_API_KEY from env.
"""

import base64
import io
import json
import os
import sys

import httpx
from PIL import Image, ImageDraw, ImageFont

ARK = os.environ.get("ARK_API_KEY", "").strip()
BASE = "https://ark.cn-beijing.volces.com/api/v3"
H = {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}
CHAT_MODEL = "doubao-seed-2-1-pro-260628"
IMG_MODEL = "doubao-seedream-5-0-pro-260628"
# read=None → never time out mid-stream (chunks arrive incrementally)
TIMEOUT = httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0)


def make_text_image() -> str:
    img = Image.new("RGB", (320, 160), "white")
    d = ImageDraw.Draw(img)
    d.rectangle([15, 15, 95, 95], fill="red")
    d.ellipse([200, 15, 295, 110], fill="blue")
    font = None
    for fp in ["/System/Library/Fonts/Supplemental/Arial Bold.ttf", "/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNSMono.ttf"]:
        try:
            font = ImageFont.truetype(fp, 44)
            break
        except Exception:
            continue
    if font is None:
        font = ImageFont.load_default()
    d.text((20, 110), "CLAUDE 2026", fill="black", font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def ask_vision(image_ref: str, label: str, is_url: bool = False) -> str:
    print(f"\n--- vision test {label} (streaming) ---", flush=True)
    if is_url:
        content = [
            {"type": "text", "text": "请用中文详细描述这张图片里有什么：文字内容、颜色、物体、形状。"},
            {"type": "image_url", "image_url": {"url": image_ref}},
        ]
    else:
        content = [
            {"type": "text", "text": "请用中文详细描述这张图片里有什么：文字内容、颜色、物体、形状。"},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_ref}"}},
        ]
    body = {"model": CHAT_MODEL, "messages": [{"role": "user", "content": content}], "max_tokens": 400, "stream": True}
    full = ""
    try:
        with httpx.stream("POST", f"{BASE}/chat/completions", headers=H, json=body, timeout=TIMEOUT) as r:
            print(f"HTTP {r.status_code}", flush=True)
            if r.status_code >= 400:
                print(r.read().decode(errors="replace")[:600], flush=True)
                return ""
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except Exception:
                    continue
                delta = (chunk.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    full += delta
                    print(delta, end="", flush=True)
        print(f"\n[full len={len(full)}]", flush=True)
        if "CLAUDE" in full.upper() or "2026" in full:
            print("✅ OCR-level vision CONFIRMED — model read the rendered text", flush=True)
        return full
    except Exception as e:
        print(f"\nstream error: {e}", flush=True)
        return ""


def main():
    if not ARK:
        print("❌ ARK_API_KEY missing")
        sys.exit(1)
    print(f"ARK key: {ARK[:14]}...{ARK[-4:]}")
    # Test B first (real complex image via URL — the main real-photo proof)
    print("\n--- generating fresh seedream image for vision test B ---", flush=True)
    r = httpx.post(f"{BASE}/images/generations", headers=H, json={"model": IMG_MODEL, "prompt": "一只橘猫坐在窗台上看书，旁边有咖啡杯，阳光"}, timeout=180)
    print(f"image gen HTTP {r.status_code}", flush=True)
    try:
        url = r.json()["data"][0]["url"]
        ask_vision(url, "B (seedream cat image, passed as URL)", is_url=True)
    except Exception as e:
        print("image gen/parse error:", e, r.text[:400], flush=True)
    # Test A (OCR bar)
    ask_vision(make_text_image(), "A (PIL image: red rect + blue circle + text 'CLAUDE 2026')")
    print("\n=== vision real-photo re-test done ===")


if __name__ == "__main__":
    main()
