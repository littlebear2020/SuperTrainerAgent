#!/usr/bin/env python3
"""D0 探针：实测 360 网关 vision 图片大小阈值。

构造 5/6/8/12/16/20MB 梯度测试图，经 360 网关发 vision 请求，
记录各档 HTTP 状态与错误体，找出真实拒绝阈值。

用法：cd backend && uv run python scripts/probe_image_size_threshold.py
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path

import httpx
from PIL import Image

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


def make_image_bytes(target_mb: float) -> bytes:
    """构造接近 target_mb 的 JPEG 图片（有效格式）。"""
    # 估算像素数使 JPEG 接近目标大小
    # JPEG ~ 3:1 压缩 → 原始约 target_mb * 3
    total_pixels = int(target_mb * 1024 * 1024 * 3 / 3)  # rough
    side = int(total_pixels**0.5)
    side = max(500, min(side, 5000))

    img = Image.new("RGB", (side, side), color=(100, 150, 200))
    # 用质量参数调节文件大小
    buf = io.BytesIO()
    quality = 95
    img.save(buf, format="JPEG", quality=quality)
    data = buf.getvalue()

    # 如果太小，增大画布或降低压缩
    target = int(target_mb * 1024 * 1024)
    while len(data) < target and side < 5000:
        side = int(side * 1.2)
        img = Image.new("RGB", (side, side), color=(100, 150, 200))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        data = buf.getvalue()

    return data


def probe_size(size_mb: float, client: httpx.Client) -> dict:
    """发一个 vision 请求，返回状态/错误体。"""
    img_bytes = make_image_bytes(size_mb)
    b64 = base64.b64encode(img_bytes).decode("ascii")
    data_uri = f"data:image/jpeg;base64,{b64}"

    body = {
        "model": "google/gemini-3.6-flash",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "用一句话描述这张图片"},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            }
        ],
        "max_tokens": 50,
    }

    t0 = time.monotonic()
    try:
        resp = client.post(
            f"{QIHU_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {QIHU_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=120,
        )
        elapsed = time.monotonic() - t0
        return {
            "size_mb": size_mb,
            "actual_bytes": len(img_bytes),
            "b64_len": len(b64),
            "status": resp.status_code,
            "elapsed_s": round(elapsed, 1),
            "body": resp.text[:500],
        }
    except Exception as exc:
        elapsed = time.monotonic() - t0
        return {
            "size_mb": size_mb,
            "actual_bytes": len(img_bytes),
            "b64_len": len(b64),
            "status": "exception",
            "elapsed_s": round(elapsed, 1),
            "body": str(exc)[:500],
        }


def main():
    sizes = [5, 6, 8, 12, 16, 20]
    results = []

    with httpx.Client() as client:
        for size_mb in sizes:
            print(f"\n--- Probing {size_mb}MB ---", flush=True)
            result = probe_size(size_mb, client)
            results.append(result)
            status = result["status"]
            actual_mb = result["actual_bytes"] / 1024 / 1024
            print(f"  Actual: {actual_mb:.1f}MB  Status: {status}  Elapsed: {result['elapsed_s']}s", flush=True)
            if status != 200:
                print(f"  Body: {result['body'][:200]}", flush=True)
            else:
                print("  OK!", flush=True)

    print("\n\n=== D0 探针结果汇总 ===")
    print(f"{'Size (MB)':<12} {'Actual (MB)':<14} {'Status':<10} {'Elapsed (s)':<12} {'Note'}")
    print("-" * 70)
    for r in results:
        note = ""
        if r["status"] != 200:
            note = r["body"][:60]
        actual_mb = r["actual_bytes"] / 1024 / 1024
        print(f"{r['size_mb']:<12} {actual_mb:<14.1f} {str(r['status']):<10} {r['elapsed_s']:<12} {note}")

    print("\n=== JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
