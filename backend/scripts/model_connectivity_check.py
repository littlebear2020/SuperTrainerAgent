"""Model connectivity check for the 360 网关模型清单（2026-07-31 收口后）。

Verifies:
  1. 360 网关侧每个条目（MODELS 表）各自响应一次普通 chat 请求。
  2. 同各条目的 vision 支持度（结论 → config.yaml 的 supports_vision）。
  3. thinking extra_body 透传（结论 → supports_thinking / when_thinking_enabled）。
  4. 360 preset TTS accepts voice ids 332 (male) and 158 (female).

字节 Ark 直连的豆包走 ark.cn-beijing.volces.com，
不在本脚本覆盖范围（本脚本只打 360 网关）。

Run from backend/:
    PYTHONPATH=. uv run python scripts/model_connectivity_check.py
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import httpx


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parent.parent.parent / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# 2026-10-12 花名册调整（docs/plans/2026-10-12-model-config-roster-adjustment-plan.md）：
# 删 6 条 360（mimo-v2.6-flash/pro、gpt-6-luna/sol、gemini-3.8-flash、
# deepseek-v4-flash-vision-exp）+ Ark 聊天 doubao-seed-2.1-pro-ark（Ark 通道保留）；
# 增 claude 三条（spike 留档 temp/results.json：vision 答对主色、thinking 参数被接受
# 但 reasoning_tokens=0 = 无效参数 → supports_thinking: false）；qwen3.8-flash 提为
# models[0]（650 样本压测胜出，docs/2026-10-12-flash-trio-benchmark.md）。
# gpt-6.1-sol spike 证伪不入册（chat/completions 上 function tools 全形态 400）。
# 历史：2026-09-08 批 id 拼写已对 https://api.360.cn/v1/models 目录逐一核实（实查）。
MODELS = [
    ("qwen3.8-flash", "qwen/qwen3.8-flash"),
    ("glm-5.3-flash", "z-ai/glm-5.3-flash"),
    ("qwen3.8-max", "qwen/qwen3.8-max"),
    ("gpt-5.6-terra", "openai/gpt-5.6-terra"),
    ("claude-haiku-5-5", "anthropic/claude-haiku-5-5"),
    ("claude-sonnet-5-5", "anthropic/claude-sonnet-5-5"),
    ("claude-opus-5-5", "anthropic/claude-opus-5-5"),
]

# vision 探测目标：结论直接决定 config.yaml 的 supports_vision 取值。
VISION_PROBES = MODELS

# thinking extra_body 透传探测（360 网关对各家模型是否接受）。
# 已有结论：qwen3.8-max 透传成功；gpt-5.6-terra 结论已回填（08-18）。
# claude 三条按 supports_thinking: false 注册（2026-10-12 spike：thinking 形态被
# 接受但无 reasoning 产出 = 无效参数），不做 thinking 透传探测。
THINKING_PROBES = [
    ("qwen3.8-flash", "qwen/qwen3.8-flash"),
    ("glm-5.3-flash", "z-ai/glm-5.3-flash"),
    ("gpt-5.6-terra", "openai/gpt-5.6-terra"),
    ("qwen3.8-max", "qwen/qwen3.8-max"),
]

_BASE = "https://api.360.cn/v1"


def _vision_probe_image() -> str:
    """256x256 纯红 PNG 的 data URL（无外部依赖，运行时生成）。

    **不要用 1x1 像素图探测 vision**：退化图没有可描述的内容，模型会瞎猜
    （实测（已撤 kimi-k3）把 1x1 图说成「黑色方块」、又说「看不到图片」），据此
    会误判成「不支持 vision」。
    """
    import base64
    import struct
    import zlib

    w = h = 256
    # 每行：filter byte 0 + RGB 像素
    raw = b"".join(b"\x00" + b"\xdc\x1e\x1e" * w for _ in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode()


async def _chat(client: httpx.AsyncClient, api_key: str, model: str, vision: bool) -> tuple[bool, str]:
    if vision:
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "What is the dominant color of this image? Answer with one word."},
                    {"type": "image_url", "image_url": {"url": _vision_probe_image()}},
                ],
            }
        ]
    else:
        msgs = [{"role": "user", "content": "say 'ok'"}]
    # vision 给足 token：思考型模型（qwen3.8-max 等）reasoning 会吃掉 32 token 预算 → 正文空
    body = {"model": model, "messages": msgs, "max_tokens": 300 if vision else 32}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        # 120s：360 网关偶发首包慢（实测 gemini-3.6-flash 60s 超时假阴性，重跑 2s 返回）
        resp = await client.post(f"{_BASE}/chat/completions", json=body, headers=headers, timeout=120.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, str(resp.json()["choices"][0]["message"].get("content", ""))[:60]
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _chat_thinking(client: httpx.AsyncClient, api_key: str, model: str) -> tuple[bool, str]:
    """Test whether the 360 gateway accepts config.yaml 的 thinking 开关。

    注意 body 形状：config.yaml 写 `when_thinking_enabled.extra_body.thinking`，
    而 openai SDK 的 `extra_body` 是**合并进 body 顶层**的（_base_client._build_request
    把 extra_json merge 到 json_data），线上真实请求体是顶层 `"thinking": {...}`，
    不是嵌套的 `"extra_body": {...}`。此处必须复刻顶层形状，否则探测的是生产
    从不发送的 payload（2026-07-31 实测：嵌套形状 gemini 一律 400 假阴性）。
    """
    body = {
        "model": model,
        "messages": [{"role": "user", "content": "think briefly, then say 'ok'"}],
        "max_tokens": 64,
        "thinking": {"type": "enabled"},
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        resp = await client.post(f"{_BASE}/chat/completions", json=body, headers=headers, timeout=120.0)
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
        return True, "thinking 顶层键被接受"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def _tts(client: httpx.AsyncClient, api_key: str, voice_id: int) -> tuple[bool, str]:
    import json

    body = {"model": "360/tts-stream", "input": "测试", "voice": voice_id, "response_format": "base64"}
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    try:
        async with client.stream("POST", "https://api.360.cn/v1/audio/stream_speech", json=body, headers=headers, timeout=60.0) as resp:
            if resp.is_error:
                return False, f"HTTP {resp.status_code}: {(await resp.aread()).decode(errors='replace')[:200]}"
            got = False
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    ev = json.loads(data)
                    code = ev.get("code")
                    # 空串 code 不是错误：360 SSE 末尾的 usage 事件带 code=""，
                    # 按 != "0" 判会把成功流误判成失败（生产已修，见
                    # app/gateway/voice_twin/qihu_tts_client.py:126 同一判据）。
                    if code is not None and str(code).strip() not in ("", "0"):
                        return False, f"code={code}: {ev.get('message', '')}"
                    if ev.get("data", {}).get("content"):
                        got = True
            return got, "ok" if got else "no audio"
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"


async def main() -> int:
    _load_dotenv()
    api_key = os.environ.get("QIHU_API_KEY")
    if not api_key:
        print("QIHU_API_KEY not set", file=sys.stderr)
        return 2
    failures = 0
    async with httpx.AsyncClient() as client:
        print("=== LLM connectivity ===")
        for name, model in MODELS:
            ok, detail = await _chat(client, api_key, model, vision=False)
            print(f"  {name:24} {'OK ' if ok else 'FAIL'} {detail}")
            if not ok:
                failures += 1
        print("\n=== vision 支持度（→ config.yaml supports_vision） ===")
        for name, model in VISION_PROBES:
            ok, detail = await _chat(client, api_key, model, vision=True)
            print(f"  {name:24} {'OK ' if ok else 'FAIL'} {detail}")
            # vision 不通不计 failures：结论写进 supports_vision: false 即可，不阻塞
        print("\n=== thinking extra_body 透传（→ supports_thinking） ===")
        for name, model in THINKING_PROBES:
            ok, detail = await _chat_thinking(client, api_key, model)
            print(f"  {name:24} {'OK ' if ok else 'FAIL'} {detail}")
            # 同上：不通则该条目标 supports_thinking: false
        print("\n=== 360 preset TTS voice ids ===")
        for vid in (332, 158):
            ok, detail = await _tts(client, api_key, vid)
            print(f"  voice {vid}: {'OK ' if ok else 'FAIL'} {detail}")
            if not ok:
                failures += 1
                print(f"  → voice {vid} 不可用,从 voices.py preset 池换 id")
    print(f"\n{'ALL OK' if failures == 0 else f'{failures} FAILURE(S)'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
