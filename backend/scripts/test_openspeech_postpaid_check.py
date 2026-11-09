#!/usr/bin/env python
"""Probe: after user opened Volcengine openspeech post-paid, re-check seed-tts-2.0.

Stage 0 originally got code 55000000 "resource ID mismatched with speaker" —
that was BEFORE post-paid activation. User asks: does post-paid mean we no
longer need a speaker id? This probe checks:
  1. TTS with no speaker field (does post-paid give a default voice?)
  2. TTS with a few known system speaker ids (does post-paid unlock presets?)
  3. ListSpeakers API variants (can we now enumerate owned voices?)

Reads voice_clone access_key/appid/token from .env. Does NOT modify product code.
"""

import os
import sys
import uuid
from pathlib import Path

import httpx


def _load_env():
    for p in (Path(".env"), Path("../.env"), Path("../../.env")):
        if p.exists():
            for line in p.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            return


_load_env()
KEY = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "").strip()
APPID = os.environ.get("VOLCENGINE_VOICE_APPID", "").strip()
TOKEN = os.environ.get("VOLCENGINE_VOICE_TOKEN", "").strip()
if not KEY:
    print("❌ VOLCENGINE_VOICE_ACCESS_KEY not set")
    sys.exit(1)
print(f"access_key: {KEY[:8]}...{KEY[-4:]} | appid: {APPID or '(none)'}")

BASE = "https://openspeech.bytedance.com"
REQ_ID = str(uuid.uuid4())


def headers(resource_id: str) -> dict:
    h = {"X-Api-Key": KEY, "X-Api-Resource-Id": resource_id, "X-Api-Request-Id": REQ_ID, "Content-Type": "application/json"}
    if APPID:
        h["X-Api-App-Id"] = APPID
    if TOKEN:
        h["X-Api-Access-Key"] = TOKEN
    return h


def try_tts(speaker, resource_id="seed-tts-2.0", label=""):
    body = {
        "user": {"uid": "trainerai-probe"},
        "req_params": {
            "text": "测试一句话",
            "speaker": speaker or "",
            "speed_ratio": 1.0,
            "audio_params": {"format": "mp3", "sample_rate": 24000},
        },
    }
    url = f"{BASE}/api/v3/tts/unidirectional/sse"
    try:
        r = httpx.post(url, headers=headers(resource_id), json=body, timeout=30)
        print(f"  [{label}] speaker={speaker!r} -> HTTP {r.status_code} | {r.text[:220]}")
        return r.status_code
    except Exception as e:
        print(f"  [{label}] speaker={speaker!r} -> err {e}")
        return None


def try_list_speakers():
    print("\n--- ListSpeakers API (post-paid unlocked?) ---")
    for path in ("/api/v1/tts/list_speakers", "/api/v3/tts/list_speakers", "/api/v1/mega_tts/list_speakers", "/api/v1/tts/get_speaker_list"):
        for method in ("GET", "POST"):
            url = f"{BASE}{path}"
            try:
                if method == "GET":
                    r = httpx.get(url, headers=headers("seed-tts-2.0"), timeout=15)
                else:
                    r = httpx.post(url, headers=headers("seed-tts-2.0"), json={}, timeout=15)
                if r.status_code != 404:
                    print(f"  {method} {path}: HTTP {r.status_code} | {r.text[:200]}")
            except Exception as e:
                print(f"  {method} {path}: err {e}")


if __name__ == "__main__":
    print("=" * 60)
    print("1. TTS with NO speaker (post-paid default voice?)")
    try_tts(speaker=None, label="no-speaker")
    print("\n2. TTS with known system speaker ids (seed-tts-2.0 presets):")
    for sp in ("zh_male_anqi_moon_bigtts", "zh_female_wanxiang_moon_bigtts", "zh_male_Moon_bigtts", "zh_female_Moon_bigtts", "S_xxxxxxxx", "zh_male_seed2", "zh_female_seed2"):
        try_tts(speaker=sp, label=f"preset:{sp[:24]}")
    try_list_speakers()
