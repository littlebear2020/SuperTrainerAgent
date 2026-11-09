#!/usr/bin/env python
"""Probe A3: download seedream character A + 2 reference-edit variants to /tmp
so Claude can visually inspect identity retention directly (doubao multi-image
vision API kept timing out — visual inspection is more reliable here).

Shape a) POST /images/generations + image=<ref> (verified in probe A).
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
OUT = Path("/tmp/seedream_consistency")
OUT.mkdir(exist_ok=True)


def H() -> dict:
    return {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}


def t2i(prompt: str, ref: str | None = None) -> str | None:
    body: dict = {"model": IMG, "prompt": prompt}
    if ref:
        body["image"] = ref
    r = httpx.post(f"{BASE}/images/generations", headers=H(), json=body, timeout=120)
    ii = r.json().get("usage", {}).get("input_images") if r.status_code == 200 else "n/a"
    print(f"  t2i HTTP {r.status_code} input_images={ii}")
    try:
        return (r.json().get("data") or [{}])[0].get("url")
    except Exception:
        print("  raw:", r.text[:300])
        return None


def download(url: str, dest: Path) -> bool:
    try:
        r = httpx.get(url, timeout=60)
        if r.status_code == 200:
            dest.write_bytes(r.content)
            print(f"  downloaded {dest.name} ({len(r.content)} bytes)")
            return True
        print(f"  download {dest.name} HTTP {r.status_code}")
        return False
    except Exception as e:
        print(f"  download {dest.name} err: {e}")
        return False


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY unset")
        sys.exit(1)
    print(f"ARK key: {ARK[:10]}...{ARK[-4:]}")
    print("\n[1] character A (no reference)")
    A = t2i("一个戴黑框眼镜的年轻女性,齐肩短发,白色衬衫,中性灰色背景,半身肖像,写实摄影,正面平视")
    if not A:
        sys.exit("❌ A failed")
    print("\n[2] variants via image=<A>")
    v1 = t2i("同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,坐在咖啡厅窗边微笑,写实摄影", ref=A)
    v2 = t2i("同一个戴黑框眼镜齐肩短发白衬衫的年轻女性,在办公室桌前看电脑,侧光,写实摄影", ref=A)
    if not v1 or not v2:
        sys.exit("❌ variant failed")
    print("\n[3] download for visual inspection")
    download(A, OUT / "A_portrait.jpg")
    download(v1, OUT / "v1_cafe.jpg")
    download(v2, OUT / "v2_office.jpg")
    print("\n=== URLS ===")
    print("A :", A)
    print("v1:", v1)
    print("v2:", v2)
    print(f"\n=== files at {OUT} ===")
    for p in sorted(OUT.iterdir()):
        print(" ", p)
    print("\n=== probe A3 complete ===")
