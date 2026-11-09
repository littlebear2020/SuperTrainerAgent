"""PPT Master agent 真实生成验收驱动 v2（Task 10 Step 3）。

显式 thread_id 复用 turn-1 已完成项目初始化的光合作用线程，发送确认推进
Executor 逐页手写 SVG → finalize → svg_to_pptx，直到产出 .pptx。
"""

from __future__ import annotations

import glob
import sys
import time

from deerflow.client import DeerFlowClient

THREAD_ID = "776428b2-abbc-40c3-8ae4-07bcca8ce645"
CONFIRM = "很好，第 1-2 页 SVG 已完成。请继续手写第 3 页 SVG，然后跑 finalize_svg 和 svg_to_pptx 导出 PPTX，全程无需再问我。"

client = DeerFlowClient(agent_name="ppt-master")
message = CONFIRM

for turn in range(1, 9):
    print(f"\n{'=' * 20} TURN {turn} ({time.strftime('%H:%M:%S')})", flush=True)
    t0 = time.time()
    try:
        reply = client.chat(message, thread_id=THREAD_ID, recursion_limit=300)
    except Exception as e:
        print(f"CHAT ERROR: {type(e).__name__}: {e}", flush=True)
        sys.exit(2)
    print(f"<<< ({time.time() - t0:.0f}s) {reply[-2000:]}", flush=True)

    pptx_hits = glob.glob(f".deer-flow/users/*/threads/{THREAD_ID}/user-data/**/*.pptx", recursive=True)
    if pptx_hits:
        print(f"\nPPTX PRODUCED: {pptx_hits}", flush=True)
        sys.exit(0)
    message = "很好，继续下一步，无需再问我。"

print("\nNO PPTX after 8 turns", flush=True)
sys.exit(1)
