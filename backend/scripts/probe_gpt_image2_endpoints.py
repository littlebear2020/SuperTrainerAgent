#!/usr/bin/env python3
"""D2 探针：实测 gpt-image-2 generations 端点稳定性（3 次）。

C1 已实锤 edits 端点 amount=0（12/12 张免费）。本探针验证 generations 端点是否稳定：
3/3 成功 → 切 generations（已拍板，不管成本）；不稳定 → 报告等用户决策。

用法：cd backend && uv run python scripts/probe_gpt_image2_endpoints.py
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

_env_file = Path(__file__).resolve().parents[2] / ".env"
if _env_file.exists():
    for line in _env_file.read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

QIHU_API_KEY = os.environ.get("QIHU_API_KEY", "")
QIHU_BASE_URL = os.environ.get("QIHU_BASE_URL", "https://api.360.cn")

if not QIHU_API_KEY:
    print("ERROR: QIHU_API_KEY not set in .env")
    sys.exit(1)


def probe_generations(client: httpx.Client, attempt: int) -> dict:
    """发一次 generations 端点请求。"""
    body = {
        "model": "openai/gpt-image-2",
        "prompt": "A simple blue square on white background",
    }
    t0 = time.monotonic()
    try:
        resp = client.post(
            f"{QIHU_BASE_URL}/v1/images/generations",
            headers={"Authorization": f"Bearer {QIHU_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        elapsed = time.monotonic() - t0
        result = {
            "attempt": attempt,
            "endpoint": "generations",
            "status": resp.status_code,
            "elapsed_s": round(elapsed, 1),
            "body": resp.text[:500],
        }
        if resp.status_code == 200:
            data = resp.json()
            usage = data.get("usage", {})
            result["amount"] = usage.get("amount")
            items = data.get("data", [])
            result["has_data"] = len(items) > 0
            if items:
                result["has_url"] = bool(items[0].get("url") or items[0].get("b64_json"))
        return result
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return {"attempt": attempt, "endpoint": "generations", "status": "exception", "elapsed_s": round(elapsed, 1), "body": str(exc)[:500]}


def main():
    results = []
    with httpx.Client() as client:
        for i in range(3):
            print(f"\n--- Generations attempt {i + 1}/3 ---")
            r = probe_generations(client, i + 1)
            results.append(r)
            print(f"  Status: {r['status']}  Elapsed: {r['elapsed_s']}s")
            if r["status"] == 200:
                print(f"  Amount: {r.get('amount')}  Has URL: {r.get('has_url')}")
            else:
                print(f"  Body: {r['body'][:200]}")
            if i < 2:
                time.sleep(2)

    print("\n\n=== D2 探针结果汇总 ===")
    success_count = sum(1 for r in results if r["status"] == 200)
    print(f"generations 端点: {success_count}/3 成功")
    if success_count == 3:
        print("✅ 3/3 成功 → 可以切 generations")
    else:
        print(f"⚠️ 不稳定（{success_count}/3）→ 需用户决策")

    print("\n=== JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
