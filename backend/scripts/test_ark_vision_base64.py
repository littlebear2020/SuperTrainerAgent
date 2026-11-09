#!/usr/bin/env python
"""Stage 0 final vision check: complex real image AS BASE64 (the production
view_image path, since ViewImageMiddleware sends base64 data URLs).

Test A (PIL synthetic text image, base64) failed with a dropped connection, so
this confirms whether base64 works for a REAL complex image (not just URL).
If this passes → production view_image with doubao-ark is safe.

Reads ARK_API_KEY from env.
"""

import base64
import json
import os
import sys

import httpx

ARK = os.environ.get("ARK_API_KEY", "").strip()
BASE = "https://ark.cn-beijing.volces.com/api/v3"
H = {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}
CHAT_MODEL = "doubao-seed-2-1-pro-260628"
IMG_MODEL = "doubao-seedream-5-0-pro-260628"
TIMEOUT = httpx.Timeout(connect=30.0, read=None, write=30.0, pool=30.0)


def main():
    if not ARK:
        print("❌ ARK_API_KEY missing")
        sys.exit(1)
    print(f"ARK key: {ARK[:14]}...{ARK[-4:]}")
    print("\n--- gen seedream cat ---", flush=True)
    r = httpx.post(f"{BASE}/images/generations", headers=H, json={"model": IMG_MODEL, "prompt": "一只橘猫坐在窗台上看书，旁边有咖啡杯"}, timeout=180)
    print(f"image gen HTTP {r.status_code}", flush=True)
    url = r.json()["data"][0]["url"]
    print("downloading image → base64...", flush=True)
    img_bytes = httpx.get(url, timeout=60).content
    b64 = base64.b64encode(img_bytes).decode()
    print(f"image {len(img_bytes)} bytes → base64 {len(b64)} chars", flush=True)

    print("\n--- vision: cat image AS BASE64 data URL (streaming) ---", flush=True)
    body = {
        "model": CHAT_MODEL,
        "max_tokens": 300,
        "stream": True,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "这张图里有什么？用中文简要描述主体和主要物体。"},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
    }
    full = ""
    try:
        with httpx.stream("POST", f"{BASE}/chat/completions", headers=H, json=body, timeout=TIMEOUT) as sr:
            print(f"HTTP {sr.status_code}", flush=True)
            if sr.status_code >= 400:
                print(sr.read().decode(errors="replace")[:600], flush=True)
                return
            for line in sr.iter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    ch = json.loads(data)
                except Exception:
                    continue
                d = (ch.get("choices") or [{}])[0].get("delta", {}).get("content", "")
                if d:
                    full += d
                    print(d, end="", flush=True)
        print(f"\n[full len={len(full)}]", flush=True)
        if full and ("猫" in full or "橘" in full or "书" in full):
            print("✅ BASE64 complex-image vision CONFIRMED → production view_image path safe", flush=True)
        elif not full:
            print("⚠️ empty response (same dropped-connection issue as PIL test?)", flush=True)
    except Exception as e:
        print(f"\nstream error: {e}", flush=True)
    print("\n=== base64 vision check done ===")


if __name__ == "__main__":
    main()
