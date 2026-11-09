#!/usr/bin/env python3
"""B3 前置探针：补测 alibaba/qwen3.7-plus 的 thinking 是否生效。

已测模型：gemini-3.6-flash（不生效）、doubao-seed-1.6-pro（生效）、qwen3-235b（生效）。
唯一未测：alibaba/qwen3.7-plus。

用法：cd backend && uv run python scripts/probe_thinking_models.py
"""

from __future__ import annotations

import json
import os
import sys
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


def probe_thinking(model: str, client: httpx.Client) -> dict:
    """探测模型是否在 thinking 模式下产出 reasoning_content。"""
    body = {
        "model": model,
        "messages": [
            {"role": "user", "content": "1+1=?  请仔细推理后回答。"},
        ],
        "max_tokens": 500,
        "extra_body": {
            "chat_template_kwargs": {"enable_thinking": True},
            "thinking": {"type": "enabled"},
        },
    }

    try:
        resp = client.post(
            f"{QIHU_BASE_URL}/v1/chat/completions",
            headers={"Authorization": f"Bearer {QIHU_API_KEY}", "Content-Type": "application/json"},
            json=body,
            timeout=60,
        )
        if resp.status_code != 200:
            return {"model": model, "status": resp.status_code, "body": resp.text[:300]}

        data = resp.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})

        # 检查是否有 reasoning_content 或 thinking 字段
        has_reasoning = bool(message.get("reasoning_content"))
        has_thinking = bool(message.get("thinking"))
        content = message.get("content", "")

        # 也检查 usage 中的 reasoning_tokens
        usage = data.get("usage", {})
        completion_tokens_details = usage.get("completion_tokens_details", {})
        reasoning_tokens = completion_tokens_details.get("reasoning_tokens", 0)

        return {
            "model": model,
            "status": 200,
            "has_reasoning_content": has_reasoning,
            "has_thinking": has_thinking,
            "reasoning_tokens": reasoning_tokens,
            "content_preview": content[:200],
            "reasoning_preview": (message.get("reasoning_content") or message.get("thinking") or "")[:200],
        }
    except Exception as exc:
        return {"model": model, "status": "exception", "body": str(exc)[:300]}


def main():
    models = [
        "alibaba/qwen3.7-plus",
        "google/gemini-3.6-flash",  # 对照组（已知不生效）
    ]

    results = []
    with httpx.Client() as client:
        for model in models:
            print(f"\n--- Probing {model} ---")
            r = probe_thinking(model, client)
            results.append(r)
            if r.get("status") == 200:
                thinking_ok = r.get("has_reasoning_content") or r.get("has_thinking") or r.get("reasoning_tokens", 0) > 0
                print(f"  Thinking: {'✅ 生效' if thinking_ok else '❌ 不生效'}")
                print(f"  reasoning_tokens: {r.get('reasoning_tokens')}")
            else:
                print(f"  Status: {r['status']}")
                print(f"  Body: {r.get('body', '')[:200]}")

    print("\n\n=== B3 前置探针结果 ===")
    for r in results:
        model = r.get("model", "?")
        if r.get("status") == 200:
            thinking_ok = r.get("has_reasoning_content") or r.get("has_thinking") or r.get("reasoning_tokens", 0) > 0
            print(f"  {model}: thinking={'✅' if thinking_ok else '❌'}")
        else:
            print(f"  {model}: error ({r.get('status')})")

    print("\n=== JSON ===")
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
