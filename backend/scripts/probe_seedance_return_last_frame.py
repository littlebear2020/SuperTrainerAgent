"""探针：坐实 Seedance return_last_frame 响应字段名。

文档（BYTEDANCE_ARK_API_REFERENCE.md:147）只给了请求参数 return_last_frame: true，
响应示例（:158-163）只含 content.video_url，尾帧 URL 字段名未载。

用法：
  cd backend && uv run python scripts/probe_seedance_return_last_frame.py

需要 ARK_API_KEY 环境变量。提交一个 5s i2v 任务（带 return_last_frame: true），
打印完整响应 JSON 以人工确认尾帧字段名。

Phase 2 Task 5 前置闸——探针不通过则在 changelog 记录限制并继续（本地抽帧 fallback）。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import httpx


async def main() -> None:
    api_key = os.environ.get("ARK_API_KEY", "")
    if not api_key:
        print("ERROR: ARK_API_KEY not set — cannot probe live API")
        print("Falling back to local frame extraction (see changelog limitation note)")
        sys.exit(1)

    base_url = "https://ark.cn-beijing.volces.com/api/v3"
    model = "doubao-seedance-2-0-260128"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # 提交一个简单 t2v 任务（不传图，减少审核风险），带 return_last_frame: true
    body = {
        "model": model,
        "content": [{"type": "text", "text": "一只小猫在草地上奔跑"}],
        "generate_audio": False,
        "duration": 5,
        "return_last_frame": True,
    }

    async with httpx.AsyncClient(timeout=120) as client:
        print("Submitting task with return_last_frame=true ...")
        resp = await client.post(f"{base_url}/contents/generations/tasks", headers=headers, json=body)
        print(f"Submit status: {resp.status_code}")
        data = resp.json()
        print(f"Submit response: {json.dumps(data, ensure_ascii=False, indent=2)}")

        task_id = data.get("id")
        if not task_id:
            print("ERROR: no task_id in submit response")
            sys.exit(1)

        # 轮询
        poll_url = f"{base_url}/contents/generations/tasks/{task_id}"
        for _ in range(40):  # 最多 600s
            await asyncio.sleep(15)
            resp = await client.get(poll_url, headers=headers)
            data = resp.json()
            status = str(data.get("status", "")).lower()
            print(f"Poll [{time.strftime('%H:%M:%S')}] status={status}")

            if status in ("succeeded", "success"):
                print("\n=== SUCCESS RESPONSE (full) ===")
                print(json.dumps(data, ensure_ascii=False, indent=2))

                # 打印 content 下所有 key，人工确认尾帧字段名
                content = data.get("content", {})
                if isinstance(content, dict):
                    print("\n=== content keys ===")
                    for k, v in content.items():
                        val_preview = str(v)[:100] if not isinstance(v, str) else v[:100]
                        print(f"  {k}: {val_preview}")
                sys.exit(0)

            if status in ("failed", "error"):
                print(f"\n=== FAILED ===\n{json.dumps(data, ensure_ascii=False, indent=2)}")
                sys.exit(1)

        print("TIMEOUT after 600s")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
