#!/usr/bin/env python
"""Probe A: can Ark-direct seedream 5.0 pro replace NanoBanana for ViMax
character-consistency (reference-image editing)?

ViMax identity retention today = NanoBanana ``/v1/images/edits`` (i2i, keeps
character identity across shots) via 360 gateway (nano_banana.py:126-133).
This probe checks whether Ark-direct seedream (doubao-seedream-5-0-pro-260628)
supports reference-image editing at all, and which request shape works.

Steps:
  1. seedream t2i -> character A portrait (no reference) -> URL A.
  2. Probe candidate reference-edit request shapes with A as the reference:
       a) POST /images/generations  body {model, prompt, image: <A_url>}
       b) POST /images/generations  body {model, prompt, reference_image: <A_url>}
       c) POST /images/edits        body {model, prompt, image: <A_url>}
       d) POST /images/generations  body {model, content:[{text},{image_url}]}
     Print HTTP + raw for each -> identify the working shape(s).
  3. For the first working shape, generate 2 variants of A in different scenes
     -> print URLs for manual identity comparison.

Does NOT import or modify any product code.
"""

import json
import os
import sys
from pathlib import Path

import httpx


def _load_dotenv() -> None:
    for p in (Path(".env"), Path("../.env"), Path("../../.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


_load_dotenv()
ARK = os.environ.get("ARK_API_KEY", "").strip()
BASE = "https://ark.cn-beijing.volces.com/api/v3"
IMG = "doubao-seedream-5-0-pro-260628"


def H() -> dict:
    return {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}


def banner(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}", flush=True)


def t2i(prompt: str) -> str | None:
    r = httpx.post(f"{BASE}/images/generations", headers=H(), json={"model": IMG, "prompt": prompt}, timeout=120)
    print(f"t2i HTTP {r.status_code}: {r.text[:300]}")
    try:
        return (r.json().get("data") or [{}])[0].get("url")
    except Exception:
        return None


def _extract_url(resp: httpx.Response) -> str | None:
    try:
        d = resp.json()
        return (d.get("data") or [{}])[0].get("url")
    except Exception:
        return None


def try_shape(label: str, endpoint: str, body: dict) -> tuple[bool, str | None]:
    banner(f"PROBE {label}: POST {endpoint}")
    print("body:", json.dumps(body, ensure_ascii=False)[:320])
    try:
        r = httpx.post(f"{BASE}{endpoint}", headers=H(), json=body, timeout=120)
    except Exception as e:
        print("err:", e)
        return False, None
    print(f"HTTP {r.status_code}")
    print(r.text[:700])
    ok = r.status_code == 200
    url = _extract_url(r) if ok else None
    return ok, url


SHAPES = [
    ("a) /images/generations + image", "/images/generations", lambda p, a: {"model": IMG, "prompt": p, "image": a}),
    ("b) /images/generations + reference_image", "/images/generations", lambda p, a: {"model": IMG, "prompt": p, "reference_image": a}),
    ("c) /images/edits + image", "/images/edits", lambda p, a: {"model": IMG, "prompt": p, "image": a}),
    ("d) /images/generations + content[image_url]", "/images/generations", lambda p, a: {"model": IMG, "content": [{"type": "text", "text": p}, {"type": "image_url", "image_url": {"url": a}}]}),
]


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY unset")
        sys.exit(1)
    print(f"ARK key: {ARK[:10]}...{ARK[-4:]}")
    banner("STEP 1: seedream t2i -> character A portrait (no reference)")
    A = t2i("一个戴黑框眼镜的年轻女性,齐肩短发,白色衬衫,中性灰色背景,半身肖像,写实摄影,正面平视")
    if not A:
        print("❌ no portrait; abort")
        sys.exit(1)
    print(f"✅ character A: {A[:100]}...")

    edit_prompt = "同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,坐在咖啡厅窗边微笑,写实摄影"
    banner("STEP 2: probe 4 reference-edit shapes with A as reference")
    results: list[tuple[str, bool, str | None]] = []
    for label, endpoint, mk in SHAPES:
        ok, url = try_shape(label, endpoint, mk(edit_prompt, A))
        results.append((label, ok, url))

    banner("PROBE RESULT")
    for label, ok, _ in results:
        print(f"  {label}: {'✅ 200 OK' if ok else '❌'}")
    working = [(label, url) for label, ok, url in results if ok and url]
    if not working:
        print("❌ NO working reference-edit shape — seedream likely cannot replace\n   NanoBanana /v1/images/edits for ViMax identity retention via Ark.\n   Inspect raw errors above for any hint of the correct field.")
        sys.exit(0)

    label0, _ = working[0]
    print(f"\nfirst working shape: {label0}")
    banner("STEP 3: generate 2 variants of A in different scenes (identity check)")
    idx = next(i for i, (lab, _, _) in enumerate(results) if lab == label0)
    _, endpoint0, mk0 = SHAPES[idx]
    scenes = [
        "同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,站在公园樱花树下,白天,写实摄影",
        "同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,在办公室桌前看电脑,侧光,写实摄影",
    ]
    for i, sc in enumerate(scenes, 1):
        print(f"\n--- variant {i}: {sc[:40]} ---", flush=True)
        ok, url = try_shape(f"variant {i}", endpoint0, mk0(sc, A))
        if ok and url:
            print(f"✅ variant {i} URL: {url[:100]}...")
        else:
            print(f"⚠️ variant {i} failed (see raw above)")
    print("\n→ Compare character A portrait + variants visually for identity retention.")
    print(f"  A        : {A}")
    print("=== probe A complete ===")
