#!/usr/bin/env python3
"""C2a 探针：双向探测 doubao seed-tts 与 360 TTS 是否支持情感/语调控制参数。

360 侧：SSML 已实测不支持，探的是非 SSML 的原生参数（extra_body.speed/style 等）。
doubao 侧：openspeech SSE 接口是否支持 emotion/style 参数。

用法：cd backend && uv run python scripts/probe_tts_emotion.py
"""

from __future__ import annotations

import json
import os
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
VOLCENGINE_VOICE_ACCESS_KEY = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")


def probe_360_tts_emotion(client: httpx.Client) -> dict:
    """探测 360 TTS 是否接受情感参数（非 SSML）。"""
    results = []
    # 尝试几种可能的情感参数名
    test_params = [
        {"emotion": "happy"},
        {"style": "cheerful"},
        {"emotion": "sad"},
        {"voice_style": "friendly"},
    ]
    for params in test_params:
        body = {
            "model": "qwen-tts",
            "input": {"text": "你好，这是一个测试。"},
            "voice": 146,
            **params,
        }
        try:
            resp = client.post(
                f"{QIHU_BASE_URL}/v1/audio/speech",
                headers={"Authorization": f"Bearer {QIHU_API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            results.append(
                {
                    "params": params,
                    "status": resp.status_code,
                    "body": resp.text[:300],
                }
            )
        except Exception as exc:
            results.append({"params": params, "status": "exception", "body": str(exc)[:300]})
    return {"provider": "360_tts", "results": results}


def probe_360_tts_extra_body(client: httpx.Client) -> dict:
    """探测 360 TTS 是否在 extra_body 中接受情感参数。"""
    results = []
    test_emotions = ["happy", "sad", "angry", "neutral"]
    for emotion in test_emotions:
        body = {
            "model": "qwen-tts",
            "input": {"text": "你好，这是一个测试。"},
            "voice": 146,
            "extra_body": {"emotion": emotion},
        }
        try:
            resp = client.post(
                f"{QIHU_BASE_URL}/v1/audio/speech",
                headers={"Authorization": f"Bearer {QIHU_API_KEY}", "Content-Type": "application/json"},
                json=body,
                timeout=30,
            )
            results.append(
                {
                    "emotion": emotion,
                    "status": resp.status_code,
                    "body": resp.text[:300],
                }
            )
        except Exception as exc:
            results.append({"emotion": emotion, "status": "exception", "body": str(exc)[:300]})
    return {"provider": "360_tts_extra_body", "results": results}


async def probe_doubao_tts_emotion() -> dict:
    """探测 doubao seed-tts SSE 是否支持 emotion 参数。"""
    if not VOLCENGINE_VOICE_ACCESS_KEY:
        return {"provider": "doubao_tts", "error": "VOLCENGINE_VOICE_ACCESS_KEY not set"}

    try:
        from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient

        results = []
        test_emotions = ["happy", "sad", "neutral"]
        for emotion in test_emotions:
            client = DoubaoTTSClient(access_key=VOLCENGINE_VOICE_ACCESS_KEY)
            try:
                # 尝试带 emotion 参数调用
                audio = await client.synthesize_sse(
                    speaker="zh_male_shaoergushi",
                    text="你好，这是一个测试。",
                    speed_ratio=1.0,
                    emotion=emotion,  # 测试参数
                )
                results.append(
                    {
                        "emotion": emotion,
                        "status": "success",
                        "audio_len": len(audio) if audio else 0,
                    }
                )
            except TypeError as exc:
                # 如果 synthesize_sse 不接受 emotion 参数
                results.append(
                    {
                        "emotion": emotion,
                        "status": "param_not_accepted",
                        "error": str(exc)[:200],
                    }
                )
            except Exception as exc:
                results.append(
                    {
                        "emotion": emotion,
                        "status": "error",
                        "error": str(exc)[:200],
                    }
                )
        return {"provider": "doubao_tts", "results": results}
    except Exception as exc:
        return {"provider": "doubao_tts", "error": str(exc)[:300]}


async def main():
    all_results = []

    print("=== C2a 探针：TTS 情感参数双向探测 ===\n")

    with httpx.Client() as client:
        # 360 TTS 原生情感参数
        print("--- 360 TTS 原生参数探测 ---")
        r1 = probe_360_tts_emotion(client)
        all_results.append(r1)
        for item in r1["results"]:
            print(f"  {item['params']} → {item['status']}")

        # 360 TTS extra_body 情感参数
        print("\n--- 360 TTS extra_body 探测 ---")
        r2 = probe_360_tts_extra_body(client)
        all_results.append(r2)
        for item in r2["results"]:
            print(f"  emotion={item['emotion']} → {item['status']}")

    # doubao TTS 情感参数
    print("\n--- doubao seed-tts 情感参数探测 ---")
    r3 = await probe_doubao_tts_emotion()
    all_results.append(r3)
    if "results" in r3:
        for item in r3["results"]:
            print(f"  emotion={item['emotion']} → {item['status']}")
    else:
        print(f"  Error: {r3.get('error', 'unknown')}")

    print("\n\n=== JSON ===")
    print(json.dumps(all_results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
