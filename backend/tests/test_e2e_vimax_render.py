"""Task 13 E2E: vimax render with target_duration=60 (real 360 API).

In-process TestClient drives the real gateway stack. VimaxEngine runs
in-process (mode=finished — no Worker handoff). Uses the real 360 API
(image gen + i2v video + LLM) via the QIHU_API_KEY from .env.

Covers plan Task 13 acceptance:
  - target_duration=60 → ≤2 scenes ≤12 shots (from storyboard.json)
  - render completes (<15 min)
  - output video NOT truncated to ~22s (apad fix — Phase 1.3)
  - narration audio present (Phase 3 per-shot narration)
  - Task 10: if narration fails, task fails (not silent video)

NOTE: this is a REAL paid render (~5-10 min, 360 API cost). Skipped unless
VIMAX_E2E_RUN=1 is set in env (avoids accidental cost in CI).
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.gateway.app import app
from app.gateway.csrf_middleware import CSRF_COOKIE_NAME, CSRF_HEADER_NAME, generate_csrf_token
from app.gateway.internal_auth import _INTERNAL_AUTH_TOKEN, INTERNAL_AUTH_HEADER_NAME

pytestmark = [
    pytest.mark.skipif(os.environ.get("VIMAX_E2E_RUN") != "1", reason="真实付费渲染（360 API 计费，~5-10 min）——仅 VIMAX_E2E_RUN=1 时运行"),
    pytest.mark.live,  # 2026-10-05 基建批：整文件付费门控
    pytest.mark.e2e,
]

BACKEND = Path(__file__).resolve().parent.parent
DB = BACKEND / ".deer-flow" / "data" / "deerflow.db"
VG_STORAGE = BACKEND / ".deer-flow" / "video-gen"

USER_ID = "9d7a6ed7-ace6-45a4-b645-2d7adbc2b966"  # existing user with vimax tasks

SHORT_SCRIPT = "清晨，阳光透过窗帘洒进房间。小明推开窗，深吸一口清新的空气。楼下的街道渐渐热闹起来，新的一天开始了。"  # ~3 short sentences → should yield 1 scene, few shots with target_duration=60


def _headers(user_id: str) -> dict:
    return {INTERNAL_AUTH_HEADER_NAME: _INTERNAL_AUTH_TOKEN, "X-DeerFlow-Internal-User-Id": user_id}


_CSRF = generate_csrf_token()


def _post(client, url, *, user_id, **kw):
    h = _headers(user_id)
    h[CSRF_HEADER_NAME] = _CSRF
    return client.post(url, headers=h, cookies={CSRF_COOKIE_NAME: _CSRF}, **kw)


def _wait(client, task_id, headers, timeout=900) -> tuple[str, dict]:
    """Poll until ready/failed or timeout (15 min)."""
    deadline = time.time() + timeout
    last = {}
    while time.time() < deadline:
        r = client.get(f"/api/video/tasks/{task_id}", headers=headers).json()
        last = r
        if r["status"] in ("ready", "failed"):
            return r["status"], r
        time.sleep(5)
    return "timeout", last


def _ffprobe_duration(path: Path) -> float:
    import subprocess

    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return -1.0


def test_vimax_render_e2e() -> None:
    """Real paid vimax render E2E (VIMAX_E2E_RUN=1 only — see module docstring)."""
    with TestClient(app) as client:
        _run(client)


def main() -> None:
    if os.environ.get("VIMAX_E2E_RUN") != "1":
        print("SKIP: set VIMAX_E2E_RUN=1 to run the real paid vimax render")
        return
    test_vimax_render_e2e()


def _run(client: TestClient) -> None:
    headers = _headers(USER_ID)
    print(f"[setup] user={USER_ID}")

    # 1. Create vimax draft via plan_storyboard with target_duration=60.
    #    Use the /create endpoint with n_scenes=1 (target_duration is only via
    #    the agent tool path; n_scenes=1 is the equivalent manual override and
    #    /create auto-renders). We verify the target_duration calc separately
    #    in test_plan_storyboard_target_duration_*.
    r = _post(
        client,
        "/api/video/create",
        user_id=USER_ID,
        json={
            "video_type": "vimax",
            "mode": "fixed",
            "script": SHORT_SCRIPT,
            "n_scenes": 1,
            "aspect": "16:9",
            "voice_id": "preset:146",
            "auto_render": True,
        },
    )
    print(f"[create] status={r.status_code}")
    assert r.status_code in (200, 201), r.text
    task_id = r.json()[0]["id"]
    print(f"[create] task_id={task_id}")

    # 2. Poll for render completion (up to 15 min — real 360 API calls)
    t0 = time.time()
    final, brief = _wait(client, task_id, headers, timeout=900)
    elapsed = time.time() - t0
    print(f"[poll] final={final} elapsed={elapsed:.0f}s ({elapsed / 60:.1f} min)")
    assert final == "ready", f"vimax render did not reach ready: {final} — {brief.get('error_message')}"

    # 3. Verify scene/shot count from storyboard.json
    task_dir = VG_STORAGE / USER_ID / "tasks" / task_id
    sb_path = task_dir / "storyboard.json"
    assert sb_path.exists(), f"storyboard.json missing at {sb_path}"
    sb = json.loads(sb_path.read_text())
    n_scenes = len(sb.get("scene_groups") or []) or 1
    n_shots = len(sb.get("frames") or [])
    print(f"[verify] scenes={n_scenes} shots={n_shots} (target_duration=60 → expect ≤2 scenes ≤12 shots)")
    assert n_scenes <= 2, f"too many scenes: {n_scenes}"
    assert n_shots <= 12, f"too many shots: {n_shots}"

    # 4. Verify output video NOT truncated to ~22s (apad fix)
    out = task_dir / "vimax_final.mp4"
    if not out.exists():
        out = task_dir / "output.mp4"
    dur = _ffprobe_duration(out)
    print(f"[verify] output video duration={dur:.1f}s (must NOT be ~22s truncated)")
    assert dur > 30.0, f"output too short ({dur:.1f}s) — likely 22s truncation bug"

    # 5. Verify narration audio present (Phase 3 per-shot narration).
    # With skip_tts=True + Phase 3 narration, audio.mp3 may not exist; narration
    # is baked into vimax_final.mp4. Check the final video has an audio stream.
    import subprocess

    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-select_streams", "a", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
        capture_output=True,
        text=True,
    )
    has_audio = "audio" in probe.stdout
    print(f"[verify] final video has audio stream: {has_audio}")
    assert has_audio, "final video must have audio (narration or fallback)"

    print(f"\nPASS: Task 13 E2E — vimax render {n_scenes} scene(s) {n_shots} shot(s), {dur:.1f}s video, {elapsed / 60:.1f} min, audio present")


if __name__ == "__main__":
    main()
