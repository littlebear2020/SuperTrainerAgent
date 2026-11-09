#!/usr/bin/env python3
"""Live acceptance probe for Stage 2a — doubao seed-tts-2.0 TTS (中文视频走 doubao).

Run AFTER wiring the speaker id (.env DOUBAO_TTS_DEFAULT_SPEAKER + VOLCENGINE_VOICE_ACCESS_KEY):
    cd backend && PYTHONPATH=. uv run python scripts/test_doubao_tts_live.py

Verifies the doubao TTS path end-to-end: DoubaoTTSClient.synthesize_sse(speaker, text) → mp3 bytes.
Closes the Stage 2a live-acceptance item. See
backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a 验收.
"""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv()


async def main() -> None:
    access_key = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")
    speaker = os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "")
    if not access_key:
        print("FAIL: VOLCENGINE_VOICE_ACCESS_KEY not set (voice_clone.access_key env).")
        sys.exit(1)
    if not speaker:
        print("FAIL: DOUBAO_TTS_DEFAULT_SPEAKER not set (seed-tts-2.0 console voice id).")
        sys.exit(1)

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

    print(f"speaker={speaker!r}  access_key len={len(access_key)}")
    client = DoubaoTTSClient(access_key=access_key)
    text = "你好，这是一个字节豆包 seed-tts-2.0 语音合成验收测试。"
    try:
        audio = await client.synthesize_sse(speaker=speaker, text=text, speed_ratio=1.0, language="zh")
    except Exception as exc:  # noqa: BLE001 — probe wants the real error
        print(f"FAIL: {type(exc).__name__}: {exc}")
        sys.exit(1)

    out = "/tmp/doubao_tts_live_test.mp3"
    with open(out, "wb") as f:
        f.write(audio)
    print(f"SUCCESS: {len(audio)} bytes -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
