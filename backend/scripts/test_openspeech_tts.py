#!/usr/bin/env python
"""Stage 0 follow-up: probe openspeech seed-tts-2.0 endpoint.

Validates the doubao_tts_client approach (plan §2a): reuse access_key as
X-Api-Key, POST /api/v3/tts/unidirectional[/sse], resource_id seed-tts-2.0,
req_params body (mirrors the WORKING volcengine_client.py seed-icl-2.0 shape —
verified at app/gateway/voice_twin/volcengine_client.py:290-305).

We don't yet have a confirmed seed-tts-2.0 speaker id (console task), so this
probe uses a placeholder speaker and reads the RAW response to confirm:
  - openspeech.bytedance.com reachable
  - access_key (X-Api-Key) accepted
  - endpoint + resource_id + req_params body shape valid
  - the exact speaker-id requirement (from the error message)
A "speaker invalid" error = auth/endpoint/body all OK, only speaker id missing.

Reads VOLCENGINE_VOICE_ACCESS_KEY from env (project's existing voice_clone key).
"""

import json
import os
import sys
import uuid

import httpx

KEY = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "").strip()
DOMAIN = "https://openspeech.bytedance.com"
RESOURCE = "seed-tts-2.0"


def probe(endpoint_path: str, speaker: str):
    url = f"{DOMAIN}{endpoint_path}"
    headers = {
        "X-Api-Key": KEY,
        "X-Api-Resource-Id": RESOURCE,
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    payload = {
        "user": {"uid": "deerflow"},
        "req_params": {
            "text": "你好，这是一段测试语音。",
            "speaker": speaker,
            "speed_ratio": 1.0,
            "audio_params": {"format": "mp3", "sample_rate": 24000},
            "additions": json.dumps({"explicit_language": "zh"}),
        },
    }
    print(f"\n--- probe POST {endpoint_path} (speaker={speaker!r}) ---", flush=True)
    try:
        r = httpx.post(url, headers=headers, json=payload, timeout=30)
        ct = r.headers.get("content-type", "")
        print(f"HTTP {r.status_code}  content-type={ct}", flush=True)
        if "application/json" in ct or r.status_code >= 400:
            print(r.text[:700], flush=True)
        else:
            # likely binary audio (success!) — don't dump bytes
            print(f"binary body {len(r.content)} bytes — likely AUDIO SUCCESS", flush=True)
            # save a snippet to inspect
            with open("/tmp/openspeech_tts_probe.bin", "wb") as f:
                f.write(r.content)
            print("(saved to /tmp/openspeech_tts_probe.bin for inspection)", flush=True)
        return r.status_code, r.text[:700] if ("application/json" in ct or r.status_code >= 400) else ""
    except Exception as e:
        print(f"request error: {e}", flush=True)
        return None, str(e)


def main():
    if not KEY:
        print("❌ VOLCENGINE_VOICE_ACCESS_KEY missing in env")
        sys.exit(1)
    print(f"access_key loaded: {KEY[:8]}...{KEY[-4:]}")
    found_speaker_hint = False
    for ep in ["/api/v3/tts/unidirectional", "/api/v3/tts/unidirectional/sse"]:
        code, body = probe(ep, "PROBE_PLACEHOLDER")
        if body and "speaker" in body.lower():
            found_speaker_hint = True
            print("→ endpoint+auth+resource+body likely OK; speaker id is the missing piece (console task)", flush=True)
    if not found_speaker_hint:
        print("\n(no 'speaker' error seen — inspect raw responses above)", flush=True)
    print("\n=== openspeech TTS probe done ===")


if __name__ == "__main__":
    main()
