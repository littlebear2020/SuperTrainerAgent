#!/usr/bin/env python
"""Probe: list speakers/voices under the current openspeech account.

The plan's Stage 0 gate needs seed-tts-2.0 speaker ids. Per Volcengine docs,
speakers are purchased per-account and listed via ListSpeakers API (not static
in docs). This probe tries candidate endpoints with the existing access_key to
discover what voices the account already owns — so we can pick a real speaker
id for Stage 2a instead of a placeholder.

Reads voice_clone.access_key from .env. Does NOT modify any product code.
"""

import os
import sys
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
# voice_clone.access_key resolves from $VOLCENGINE_VOICE_ACCESS_KEY (config.yaml voice_clone.access_key)
KEY = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "").strip()
if not KEY:
    print("❌ VOLCENGINE_VOICE_ACCESS_KEY not set in env")
    sys.exit(1)
print(f"using key: {KEY[:8]}...{KEY[-4:]}")

BASE = "https://openspeech.bytedance.com"
# Candidate ListSpeakers endpoints (Volcengine openspeech API variants)
CANDIDATES = [
    ("/api/v1/tts/list_speakers", "GET", {"Authorization": f"Bearer;{KEY}"}),
    ("/api/v1/tts/list_speakers", "POST", {"Authorization": f"Bearer;{KEY}", "Content-Type": "application/json"}),
    ("/api/v3/tts/list_speakers", "GET", {"Authorization": f"Bearer;{KEY}"}),
    ("/api/v1/mega_tts/list_speakers", "GET", {"Authorization": f"Bearer;{KEY}"}),
    ("/api/v1/tts/voice_list", "GET", {"Authorization": f"Bearer;{KEY}"}),
    # X-Api-Key style (new single-header auth, like voice_clone)
    ("/api/v1/tts/list_speakers", "GET", {"X-Api-Key": KEY, "X-Api-Resource-Id": "seed-tts-2.0"}),
    ("/api/v3/tts/list_speakers", "GET", {"X-Api-Key": KEY, "X-Api-Resource-Id": "seed-tts-2.0"}),
]


def banner(t):
    print(f"\n{'=' * 60}\n{t}\n{'=' * 60}", flush=True)


if __name__ == "__main__":
    if not KEY:
        print("❌ no access key found in env")
        sys.exit(1)
    banner(f"Probing ListSpeakers endpoints (key {KEY[:8]}...{KEY[-4:]})")
    found = False
    for path, method, headers in CANDIDATES:
        url = f"{BASE}{path}"
        try:
            if method == "GET":
                r = httpx.get(url, headers=headers, timeout=20)
            else:
                r = httpx.post(url, headers=headers, json={}, timeout=20)
            body = r.text[:400]
            print(f"{method} {path}: HTTP {r.status_code} | {body[:200]}")
            if r.status_code == 200:
                print(f"  ✅ 200! full: {r.text[:800]}")
                found = True
        except Exception as e:
            print(f"{method} {path}: err {e}")
    if not found:
        print("\n→ no 200. Speaker ids likely require purchasing seed-tts-2.0 resource pack first.")
        print("  Stage 2a gate: user must buy seed-tts-2.0 voices in Volcengine console,")
        print("  then list via ListSpeakers. Placeholder speaker won't work (code 55000000).")
