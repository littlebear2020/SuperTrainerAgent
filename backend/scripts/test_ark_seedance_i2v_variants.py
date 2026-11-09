#!/usr/bin/env python
"""Probe B: harden the 'Ark-direct seedance i2v does not block human portraits'
conclusion (test_ark_seedance_i2v_human.py tested only 1 AI realistic portrait).

Generates 3 human-image types via seedream t2i, then submits each to Ark-direct
seedance i2v and polls — checks none get InputImageSensitiveContentDetected:
  1. realistic-photo-style human  (写实摄影人像 — closest proxy to a real photo;
     recall 360-gateway already flags this exact class as 'may contain real person')
  2. cartoon/anime human           (日系动漫人物)
  3. multi-person scene            (三人朋友场景)

Per image, optionally also hits 360-gateway seedance r2v (same image) for a
same-image contrast (expect 360 to keep blocking). Reads ARK_API_KEY / QIHU_API_KEY.
Does NOT import or modify any product code.
"""

import json
import os
import sys
import time
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
QIHU = os.environ.get("QIHU_API_KEY", "").strip()
ARK_BASE = "https://ark.cn-beijing.volces.com/api/v3"
QIHU_BASE = "https://api.360.cn"
VID_ARK = "doubao-seedance-2-0-260128"
IMG_ARK = "doubao-seedream-5-0-pro-260628"
VID_360 = "volcengine/doubao-seedance-2-0"

VARIANTS = [
    ("realistic-photo", "写实摄影半身肖像:一个戴黑色贝雷帽的年轻男子,清晰五官,自然光,真实照片质感", "这个男子微微转头,自然呼吸,电影感运镜,写实"),
    ("cartoon-anime", "日系动漫风格:一个蓝色短发的少女,大眼睛,校服,明亮色彩,卡通插画", "这个少女眨眼微笑,头发轻轻飘动,动漫风格运镜"),
    ("multi-person", "三个年轻朋友在公园草坪上聊天,两人坐一人站,写实摄影,白天,全身场景", "这三个人自然交谈,有人转头有人挥手,电影感运镜,写实"),
]


def banner(t: str) -> None:
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}", flush=True)


def _ark_h() -> dict:
    return {"Authorization": f"Bearer {ARK}", "Content-Type": "application/json"}


def t2i(prompt: str) -> str | None:
    r = httpx.post(f"{ARK_BASE}/images/generations", headers=_ark_h(), json={"model": IMG_ARK, "prompt": prompt}, timeout=120)
    print(f"  t2i HTTP {r.status_code}: {r.text[:200]}")
    try:
        return (r.json().get("data") or [{}])[0].get("url")
    except Exception:
        return None


def _poll(task_id: str, fetch, label: str, max_s: int = 300) -> dict | None:
    print(f"  polling {label} {task_id} (up to {max_s}s)...", flush=True)
    for i in range(max_s // 5):
        time.sleep(5)
        try:
            pr = fetch(task_id)
        except Exception as e:
            print(f"  [{(i + 1) * 5}s] err: {e}")
            continue
        try:
            pd = pr.json()
        except Exception:
            print(f"  [{(i + 1) * 5}s] HTTP {pr.status_code} {pr.text[:160]}")
            continue
        st = pd.get("status") or (pd.get("data") or {}).get("status") or "?"
        print(f"  [{(i + 1) * 5}s] status={st}", flush=True)
        if str(st).lower() in ("succeeded", "succeed", "completed", "failed", "fail", "error"):
            print(f"  FINAL {label}: {json.dumps(pd, ensure_ascii=False)[:600]}")
            return pd
    print(f"  ⏱️ {label} timeout")
    return None


def ark_i2v(image_url: str, prompt: str) -> dict | None:
    body = {"model": VID_ARK, "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_url}}]}
    r = httpx.post(f"{ARK_BASE}/contents/generations/tasks", headers=_ark_h(), json=body, timeout=60)
    print(f"  Ark submit HTTP {r.status_code}: {r.text[:200]}")
    tid = None
    try:
        tid = r.json().get("id")
    except Exception:
        pass
    if not tid:
        return None
    return _poll(tid, lambda t: httpx.get(f"{ARK_BASE}/contents/generations/tasks/{t}", headers=_ark_h(), timeout=30), "Ark", 300)


def qihu_r2v(image_url: str, prompt: str) -> dict | None:
    H = {"Authorization": f"Bearer {QIHU}", "Content-Type": "application/json"}
    body = {"model": VID_360, "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": image_url}}], "duration": 5, "extra_body": {"role": "reference"}}
    r = httpx.post(f"{QIHU_BASE}/v1/videos/async_generations", headers=H, json=body, timeout=60)
    print(f"  360 submit HTTP {r.status_code}: {r.text[:260]}")
    tid = None
    try:
        tid = (r.json().get("data") or {}).get("id")
    except Exception:
        pass
    if not tid:
        return None
    Hg = {"Authorization": f"Bearer {QIHU}"}
    return _poll(tid, lambda t: httpx.get(f"{QIHU_BASE}/v1/videos/async_generations/{t}", headers=Hg, timeout=30), "360", 180)


def _outcome(j: dict | None, submit_http: int | None, submit_body: str) -> str:
    if j is None:
        # submission itself rejected (e.g. 360 HTTP 400 content-review at submit)
        if submit_http and submit_http >= 400:
            low = submit_body.lower()
            if "inputimagesensitive" in low or "privacyinformation" in low:
                return "BLOCKED-at-submit(content-review)"
            return f"REJECTED-at-submit(HTTP {submit_http})"
        return "NO_TASK"
    body = json.dumps(j, ensure_ascii=False).lower()
    st = str(j.get("status") or (j.get("data") or {}).get("status") or "").lower()
    if st in ("succeeded", "succeed", "completed"):
        return "SUCCEEDED"
    if "inputimagesensitive" in body or "privacyinformation" in body:
        return "BLOCKED(content-review)"
    if st in ("failed", "fail", "error"):
        return f"FAILED({body[:160]})"
    return f"OTHER(status={st})"


if __name__ == "__main__":
    if not ARK:
        print("❌ ARK_API_KEY unset")
        sys.exit(1)
    print(f"ARK key: {ARK[:10]}...{ARK[-4:]} | 360 key: {'SET' if QIHU else 'ABSENT (skip 360)'}")
    summary = []
    for name, img_prompt, vid_prompt in VARIANTS:
        banner(f"VARIANT: {name}")
        img = t2i(img_prompt)
        if not img:
            print(f"  ❌ {name}: image gen failed; skip")
            continue
        print(f"  image: {img[:90]}...")
        print("  --- Ark-direct seedance i2v ---")
        ark = ark_i2v(img, vid_prompt)
        print("  --- 360-gateway seedance r2v (same image) ---")
        q = qihu_r2v(img, vid_prompt) if QIHU else None
        a_out = _outcome(ark, 200, json.dumps(ark or {}, ensure_ascii=False))
        q_out = _outcome(q, None, "") if q is not None else ("SKIPPED" if not QIHU else "NO_TASK")
        summary.append((name, a_out, q_out))
    banner("SUMMARY")
    print(f"{'variant':<18}{'Ark-direct i2v':<32}{'360-gateway r2v':<32}")
    for name, a, q in summary:
        print(f"{name:<18}{a:<32}{q:<32}")
    ark_blocked = [s for s in summary if "BLOCKED" in s[1]]
    if not ark_blocked:
        print("\n✅ Ark-direct seedance i2v: NO human-portrait block across all tested types.")
    else:
        print(f"\n🔴 Ark-direct blocked on: {[s[0] for s in ark_blocked]}")
    print("\n=== probe B complete ===")
