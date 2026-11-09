"""Probe: seedance-ark 不传 duration/ratio 时的真实默认值（坐实 P0-1/P0-2 严重度）。

用法：cd backend && ARK_API_KEY=... uv run python scripts/probe_ark_seedance_defaults.py
发一笔最小 t2v 任务（不带 duration/ratio），轮询返回体打印 resolution/ratio/duration。
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx

BASE = "https://ark.cn-beijing.volces.com/api/v3"
MODEL = "doubao-seedance-2-0-260128"


async def main() -> None:
    key = os.environ["ARK_API_KEY"]
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{BASE}/contents/generations/tasks",
            headers=headers,
            json={
                "model": MODEL,
                "content": [{"type": "text", "text": "a red apple on a wooden table, static shot"}],
                "generate_audio": False,
            },
        )
        resp.raise_for_status()
        task_id = resp.json()["id"]
        print(f"submitted: {task_id}")
        for _ in range(40):
            await asyncio.sleep(15)
            r = await client.get(f"{BASE}/contents/generations/tasks/{task_id}", headers=headers)
            data = r.json()
            status = str(data.get("status", "")).lower()
            print(f"  status={status}")
            if status in ("succeeded", "success", "failed", "error"):
                # 返回体含 resolution/ratio/duration（API 参考 :161）
                print(json.dumps(data, ensure_ascii=False, indent=2))
                return
        print("timeout")


if __name__ == "__main__":
    asyncio.run(main())
