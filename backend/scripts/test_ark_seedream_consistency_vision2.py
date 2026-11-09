#!/usr/bin/env python
"""Probe A4: doubao vision consistency judgment via STREAMING + local base64
(read-timeout-proof). Uses the 3 images already downloaded by probe A3.

Streaming avoids the httpx ReadTimeout that killed probe A2 (doubao multi-image
vision is slow but streams fine). base64 local images = the verified view_image
path (test_ark_vision_base64.py).
"""

import base64
import json
import os
import sys
from pathlib import Path

import httpx

ARK = os.environ.get("ARK_API_KEY", "").strip()
if not ARK:
    # load .env if needed
    for p in (Path(".env"), Path("../.env"), Path("../../.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    ARK = os.environ.get("ARK_API_KEY", "").strip()

BASE = "https://ark.cn-beijing.volces.com/api/v3"
CHAT = "doubao-seed-2-1-pro-260628"
IMG_DIR = Path("/tmp/seedream_consistency")


def H() -> dict:
    return {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}


def vision_stream(images_b64: list[str], question: str) -> str:
    content = [{"type": "text", "text": question}]
    for b64 in images_b64:
        content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    body = {"model": CHAT, "messages": [{"role": "user", "content": content}], "max_tokens": 900, "stream": True, "extra_body": {"thinking": {"type": "disabled"}}}
    ans = ""
    timeout = httpx.Timeout(connect=30.0, read=None, write=60.0, pool=30.0)
    with httpx.stream("POST", f"{BASE}/chat/completions", headers=H(), json=body, timeout=timeout) as r:
        print(f"  vision stream HTTP {r.status_code}", flush=True)
        if r.status_code != 200:
            print("  raw:", r.read()[:400])
            return f"(HTTP {r.status_code})"
        for line in r.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            chunk = line[6:]
            if chunk.strip() == "[DONE]":
                break
            try:
                d = json.loads(chunk)
                delta = d.get("choices", [{}])[0].get("delta", {}).get("content", "")
                if delta:
                    ans += delta
                    print(delta, end="", flush=True)
            except Exception:
                pass
    print()
    return ans


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY unset")
        sys.exit(1)
    files = ["A_portrait.jpg", "v1_cafe.jpg", "v2_office.jpg"]
    imgs = []
    for f in files:
        p = IMG_DIR / f
        if not p.exists():
            sys.exit(f"❌ {p} missing (run probe A3 first)")
        imgs.append(base64.b64encode(p.read_bytes()).decode())
    print(f"loaded 3 local images from {IMG_DIR} (sizes: {[len(b) for b in imgs]} b64-chars)")
    q = (
        "图1是角色参考肖像(戴黑框眼镜齐肩短发白衬衫的年轻女性)。"
        "图2是用图1作参考图生成的变体(咖啡厅窗边微笑)。"
        "图3是用图1作参考图生成的变体(办公室看电脑)。"
        "请:①分别描述三张图中人物的面部特征(性别/大致年龄/发型/是否戴黑框眼镜/脸型/肤色);"
        "②判断图2、图3中人物是否与图1是同一个人(是/否/部分相似);③简述判断依据。"
    )
    print("\n=== VISION STREAM ANSWER ===")
    ans = vision_stream(imgs, q)
    print("\n=== done ===")
