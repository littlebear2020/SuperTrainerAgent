#!/usr/bin/env python
"""Stage 0 pre-check probe for Volcengine Ark (字节直连).

Validates the three Ark-native model ids from the integration plan §0.1:
  - doubao-seed-2-1-pro-260628   (Chat — text + vision)
  - doubao-seedream-5-0-pro-260628 (Image, text-to-image)
  - doubao-seedance-2-0-260128   (Video, t2v — THE human-figure gate)

Reads ARK_API_KEY from env. Prints RAW responses so the API itself confirms
the shape (no endpoint/field guessing asserted as fact). The video t2v result
is the Stage 0 gate:
  conclusion A (succeeds)            -> seedance-ark can be the default ai_video
  conclusion B (content-review rej) -> cannot be default; Hailuo stays default
"""

import base64
import json
import os
import struct
import sys
import time
import zlib

import httpx

ARK = os.environ.get("ARK_API_KEY", "").strip()
BASE = "https://ark.cn-beijing.volces.com/api/v3"
H = {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}

CHAT_MODEL = "doubao-seed-2-1-pro-260628"
IMG_MODEL = "doubao-seedream-5-0-pro-260628"
VID_MODEL = "doubao-seedance-2-0-260128"


def _solid_png_b64(w: int, h: int, rgb=(220, 40, 40)) -> str:
    """Generate a valid w×h solid-color PNG (stdlib only). >=14px for Ark vision."""

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)  # 8-bit truecolor RGB
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))  # filter=0 per row
    idat = zlib.compress(raw)
    return base64.b64encode(sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")).decode()


IMG_B64 = _solid_png_b64(64, 64)  # 64×64 solid red — well above Ark's 14px minimum


def banner(t):
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}", flush=True)


def test_chat_text():
    banner(f"TEST 1: Chat (text) — {CHAT_MODEL}")
    body = {"model": CHAT_MODEL, "messages": [{"role": "user", "content": "用一句话介绍你自己"}], "max_tokens": 100}
    r = httpx.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=60)
    print(f"HTTP {r.status_code}")
    print(r.text[:900])
    try:
        d = r.json()
        if "choices" in d:
            print("✅ Chat OK:", repr(d["choices"][0]["message"]["content"])[:200])
        else:
            print("⚠️ unexpected shape, keys:", list(d.keys()))
    except Exception as e:
        print("parse error:", e)


def test_chat_vision():
    banner("TEST 2: Chat (vision) — validate §1.1 (vision should NOT work)")
    body = {
        "model": CHAT_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe this image"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{IMG_B64}"}},
                ],
            }
        ],
        "max_tokens": 100,
    }
    r = httpx.post(f"{BASE}/chat/completions", headers=H, json=body, timeout=60)
    print(f"HTTP {r.status_code}")
    print(r.text[:900])
    try:
        d = r.json()
        if "choices" in d:
            c = d["choices"][0]["message"]["content"] or ""
            print("content:", repr(c)[:300])
            if not c.strip():
                print("✅ §1.1 confirmed: vision returns EMPTY (doubao vision does not work)")
            else:
                print("⚠️ vision returned text — re-check §1.1 (model may partially process images)")
        else:
            print("⚠️ error shape (vision likely rejected):", d.get("error") or list(d.keys()))
    except Exception as e:
        print("parse error:", e)


def test_image():
    banner(f"TEST 3: Image (t2i) — {IMG_MODEL}")
    body = {"model": IMG_MODEL, "prompt": "一只橘猫坐在窗台上，阳光，写实风格"}
    r = httpx.post(f"{BASE}/images/generations", headers=H, json=body, timeout=120)
    print(f"HTTP {r.status_code}")
    print(r.text[:900])
    try:
        d = r.json()
        if "data" in d and d["data"]:
            print("✅ Image OK, item keys:", list(d["data"][0].keys()))
        elif "error" in d:
            print("⚠️ error:", d["error"])
        else:
            print("⚠️ unexpected shape, keys:", list(d.keys()))
    except Exception as e:
        print("parse error:", e)


def test_video_t2v():
    banner(f"TEST 4: Video (t2v 人像 GATE) — {VID_MODEL}")
    prompt = "一个年轻女孩坐在咖啡厅窗边看书，近景，自然光，电影感"  # human-figure t2v
    body = {"model": VID_MODEL, "content": [{"type": "text", "text": prompt}]}
    print("submitting t2v human-figure prompt...")
    r = httpx.post(f"{BASE}/contents/generations/tasks", headers=H, json=body, timeout=60)
    print(f"submit HTTP {r.status_code}")
    print(r.text[:900])
    task_id = None
    try:
        d = r.json()
        task_id = d.get("id") or d.get("task_id") or (d.get("data") or {}).get("id")
    except Exception:
        pass
    if not task_id:
        print("❌ No task id returned — endpoint/body shape may differ. Raw response above; adjust and re-run.")
        return
    print(f"task_id={task_id} — polling (up to 300s)...")
    poll_url = f"{BASE}/contents/generations/tasks/{task_id}"
    for i in range(60):  # 60 x 5s = 300s
        time.sleep(5)
        try:
            pr = httpx.get(poll_url, headers=H, timeout=30)
        except Exception as e:
            print(f"[{(i + 1) * 5}s] poll error: {e}")
            continue
        try:
            pd = pr.json()
        except Exception:
            print(f"[{(i + 1) * 5}s] poll HTTP {pr.status_code} body={pr.text[:200]}")
            continue
        status = pd.get("status") or (pd.get("data") or {}).get("status") or "?"
        print(f"[{(i + 1) * 5}s] status={status}", flush=True)
        if str(status).lower() in ("succeeded", "succeed", "completed", "failed", "fail", "error"):
            print("FINAL:", json.dumps(pd, ensure_ascii=False)[:1400])
            if str(status).lower() in ("succeeded", "succeed", "completed"):
                vurl = (pd.get("content") or {}).get("video_url") or pd.get("video_url") or ((pd.get("data") or {}).get("content") or {}).get("video_url")
                print(f"✅ GATE CONCLUSION A: t2v human-figure SUCCEEDED — video_url={vurl}")
            else:
                err = pd.get("error") or (pd.get("data") or {}).get("error")
                print(f"🔴 GATE CONCLUSION B: t2v FAILED — error={err}")
            return
    print("⏱️ timeout — task still running after 300s (check task_id manually)")


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY not set in env")
        sys.exit(1)
    print(f"ARK key loaded: {ARK[:14]}...{ARK[-4:]}")
    test_chat_text()
    test_chat_vision()
    test_image()
    test_video_t2v()
    print("\n=== Stage 0 probe complete ===")
