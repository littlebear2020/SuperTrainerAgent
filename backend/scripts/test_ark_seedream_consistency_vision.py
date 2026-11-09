#!/usr/bin/env python
"""Probe A2: seedream reference-edit identity consistency — vision-assisted check.

Probe A confirmed shape a) ``POST /images/generations`` + top-level ``image=<url>``
is the working seedream i2i shape (usage.input_images=1). This script regenerates
character A + 2 variants via that shape, then asks doubao-seed-2-1-pro-260628
(vision verified) whether the variants retain A's identity — a programmatic
proxy for the identity-retention that NanoBanana /v1/images/edits gives ViMax.

Does NOT import or modify any product code.
"""

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
CHAT = "doubao-seed-2-1-pro-260628"


def H() -> dict:
    return {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}


def t2i(prompt: str, ref: str | None = None) -> str | None:
    body: dict = {"model": IMG, "prompt": prompt}
    if ref:
        body["image"] = ref  # shape a) — the verified i2i reference-edit field
    r = httpx.post(f"{BASE}/images/generations", headers=H(), json=body, timeout=120)
    print(f"  t2i HTTP {r.status_code} input_images={r.json().get('usage', {}).get('input_images') if r.status_code == 200 else 'n/a'}")
    try:
        return (r.json().get("data") or [{}])[0].get("url")
    except Exception:
        print("  raw:", r.text[:300])
        return None


def vision(images: list[str], question: str) -> str:
    content = [{"type": "text", "text": question}]
    for u in images:
        content.append({"type": "image_url", "image_url": {"url": u}})
    # thinking disabled to avoid long reasoning chain on 3-image compare; longer timeout
    r = httpx.post(f"{BASE}/chat/completions", headers=H(), json={"model": CHAT, "messages": [{"role": "user", "content": content}], "max_tokens": 800, "extra_body": {"thinking": {"type": "disabled"}}}, timeout=300)
    print(f"  vision HTTP {r.status_code}")
    if r.status_code != 200:
        print("  raw:", r.text[:400])
        return f"(vision error {r.status_code})"
    try:
        return r.json()["choices"][0]["message"]["content"]
    except Exception:
        print("  raw:", r.text[:400])
        return "(parse error)"


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY unset")
        sys.exit(1)
    print(f"ARK key: {ARK[:10]}...{ARK[-4:]}")
    print("\n[1] generate character A portrait (no reference)")
    A = t2i("一个戴黑框眼镜的年轻女性,齐肩短发,白色衬衫,中性灰色背景,半身肖像,写实摄影,正面平视")
    if not A:
        sys.exit("❌ A failed")
    print(f"  A: {A[:90]}...")
    print("\n[2] generate 2 variants via shape a) image=<A> (reference-edit)")
    v1 = t2i("同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,坐在咖啡厅窗边微笑,写实摄影", ref=A)
    v2 = t2i("同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,在办公室桌前看电脑,侧光,写实摄影", ref=A)
    if not v1 or not v2:
        sys.exit("❌ variant failed")
    print(f"  v1: {v1[:90]}...")
    print(f"  v2: {v2[:90]}...")
    print("\n[3] doubao vision: do v1/v2 retain A's identity?")
    q = "图1是角色参考肖像。图2、图3是用图1作为参考图生成的变体。请:①分别描述三张图中人物的面部特征(性别/大致年龄/发型/是否戴眼镜/脸型);②判断图2、图3中的人物是否与图1是同一个人(是/否/部分相似);③简述判断依据。"
    ans = vision([A, v1, v2], q)
    print("\n=== VISION ANSWER ===")
    print(ans)
    print("\n=== IMAGE URLS (for manual comparison) ===")
    print("A :", A)
    print("v1:", v1)
    print("v2:", v2)
    print("\n=== probe A2 complete ===")
