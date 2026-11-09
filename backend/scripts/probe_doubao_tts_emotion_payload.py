#!/usr/bin/env python3
"""T5 执行前置 live 探针：doubao seed-tts-2.0 ``audio_params.emotion`` 支持面实测。

背景（2026-10-14 配音修复批 T5）：官方文档记载 ``req_params.audio_params.emotion``
（+ 可选 ``emotion_scale`` 1-5 默认 4）**仅部分音色支持**（多情感音色），且枚举随
音色不同。本探针在真实 openspeech 端点上，对本仓实际使用的 doubao 音色
（``voice_catalog`` 4 条 + ``DOUBAO_TTS_DEFAULT_SPEAKER``，去重）实测：

  ① stage A：默认音色上跑候选 emotion 值全集（含英文/中文/auto 形态），发现可接受形态；
  ② stage B：stage A 命中的值 × 其余音色，测每音色支持面；
  ③ 每音色各带一次 baseline（不带 emotion）作对照。

判定：HTTP 2xx + SSE 全 0 code + 有音频字节 = accepted；否则记录错误码/消息。
不打印密钥；文本 ~15 字/次，付费成本可忽略。

2026-10-14 实测结论（T5 执行前置）：
- 4 个在册 doubao 音色（vv/yingyujiaoxue/m191/liufei）对 happy/sad/angry/neutral/
  auto/开心/焦急/平静 **全部无错接受**（HTTP 200 + code 0 + 有音频）——下发无
  4xx/153 风险，emotion 参数可接线。PR AI 评审四轮后补跑**全值覆盖**（Stage B
  不再截断前 4 值）：中文枚举 开心/焦急/平静（恰为生产 ``_ANNOTATION_EMOTION_MAP``
  实际下发的值）在全部 4 音色逐一口径实测通过（4 音色 × 8 值 = 32 组合全 ✅）。
- 同参数两次调用音频 md5 不同（TTS 输出非确定性）→ 声学效果**离线不可判定**，
  归部署侧人工试听验收（T8 行为级冒烟）。
- 映射表（orchestration/tts.py ``_ANNOTATION_EMOTION_MAP``）只使用上述探针
  验证过的值；新增映射/新音色须先本探针复测。

用法（须显式执行）：
    cd backend && uv run python scripts/probe_doubao_tts_emotion_payload.py --env-file /path/to/.env
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid

import httpx

PROBE_TEXT = "今天的风儿好喧嚣啊，我们回家吧。"
CANDIDATE_EMOTIONS = ["happy", "sad", "angry", "neutral", "auto", "开心", "焦急", "平静"]


def _catalog_speakers() -> list[str]:
    """CURATED_VOICES 里 provider=doubao 的 speaker（去重保序）。"""
    from app.gateway._shared.voice_catalog import CURATED_VOICES

    out: list[str] = []
    for v in CURATED_VOICES:
        if v.provider == "doubao" and v.id.startswith("doubao:"):
            speaker = v.id.split(":", 1)[1]
            if speaker not in out:
                out.append(speaker)
    return out


def _payload(speaker: str, emotion: str | None) -> dict:
    audio_params: dict = {"format": "mp3", "sample_rate": 24000}
    if emotion is not None:
        audio_params["emotion"] = emotion
    return {
        "user": {"uid": "deerflow"},
        "req_params": {
            "text": PROBE_TEXT,
            "speaker": speaker,
            "speed_ratio": 1.0,
            "audio_params": audio_params,
            "additions": json.dumps({"explicit_language": "zh"}, ensure_ascii=False),
        },
    }


async def _one_call(client: httpx.AsyncClient, domain: str, key: str, speaker: str, emotion: str | None) -> dict:
    """单次合成调用，返回 {emotion, status, audio_bytes, error}。"""
    headers = {
        "X-Api-Key": key,
        "X-Api-Resource-Id": "seed-tts-2.0",
        "X-Api-Request-Id": str(uuid.uuid4()),
        "Content-Type": "application/json",
    }
    try:
        audio = 0
        err: str | None = None
        async with client.stream("POST", f"{domain}/api/v3/tts/unidirectional/sse", headers=headers, json=_payload(speaker, emotion)) as resp:
            if resp.is_error:
                body = await resp.aread()
                return {"emotion": emotion, "status": f"HTTP {resp.status_code}", "audio_bytes": 0, "error": body.decode(errors="replace")[:200]}
            current_event: str | None = None
            data_lines: list[str] = []
            async for raw in resp.aiter_lines():
                line = raw.strip()
                if line.startswith("event:"):
                    current_event = line[len("event:") :].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[len("data:") :].strip())
                elif line == "" and data_lines:
                    full = "\n".join(data_lines)
                    data_lines.clear()
                    try:
                        ev = json.loads(full)
                    except json.JSONDecodeError:
                        continue
                    code = ev.get("code")
                    code_norm = None if code is None else str(code).strip()
                    if current_event == "153" or (current_event == "352" and code_norm not in (None, "", "0")) or (current_event == "152" and code_norm not in (None, "", "20000000")):
                        err = f"event {current_event} code={code}: {ev.get('message', '')}"
                        break
                    if current_event == "352":
                        audio += len(ev.get("data") or "")  # base64 字符长度（≈真实字节 4/3），仅作 >0 判据
                    current_event = None
                elif line == "":
                    # 空行且无 data（事件不带 data / 边界额外空行）：重置事件态，
                    # 防残留 current_event 错误归属到下一 data 事件（评审六轮）
                    current_event = None
        return {"emotion": emotion, "status": "ok" if (err is None and audio > 0) else "no_audio", "audio_bytes": audio, "error": err}
    except Exception as exc:  # noqa: BLE001 — probe wants the real error
        return {"emotion": emotion, "status": "exception", "audio_bytes": 0, "error": f"{type(exc).__name__}: {exc}"[:200]}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env-file", default=None, help="显式 .env 路径（worktree 无 .env 时用）")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(args.env_file) if args.env_file else load_dotenv()

    key = os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY", "")
    if not key:
        print("FAIL: VOLCENGINE_VOICE_ACCESS_KEY not set")
        raise SystemExit(1)

    default_speaker = os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER", "")
    speakers = _catalog_speakers()
    if default_speaker and default_speaker not in speakers:
        speakers.insert(0, default_speaker)
    elif default_speaker:
        speakers.remove(default_speaker)
        speakers.insert(0, default_speaker)
    if not speakers:
        print("FAIL: no doubao speakers found (voice_catalog empty and DOUBAO_TTS_DEFAULT_SPEAKER unset)")
        raise SystemExit(1)
    primary = speakers[0]

    domain = "https://openspeech.bytedance.com"
    report: dict = {"primary_speaker": primary, "speakers": speakers, "stage_a": [], "stage_b": []}

    async with httpx.AsyncClient(timeout=60.0) as client:
        # Stage A: candidate emotion values on the primary speaker
        print(f"=== Stage A: speaker={primary} × {len(CANDIDATE_EMOTIONS)} candidate emotions ===")
        report["stage_a"].append(await _one_call(client, domain, key, primary, None))  # baseline
        accepted: list[str] = []
        for emo in CANDIDATE_EMOTIONS:
            r = await _one_call(client, domain, key, primary, emo)
            report["stage_a"].append(r)
            flag = "✅" if r["status"] == "ok" else "❌"
            print(f"  {flag} emotion={emo!r}: status={r['status']} audio={r['audio_bytes']}B err={r['error']}")
            if r["status"] == "ok":
                accepted.append(emo)

        # Stage B: accepted values × remaining speakers
        rest = [s for s in speakers if s != primary]
        if accepted and rest:
            print(f"=== Stage B: accepted={accepted} × speakers={rest} ===")
            for sp in rest:
                r0 = await _one_call(client, domain, key, sp, None)
                report["stage_b"].append({"speaker": sp, **r0})
                print(f"  baseline speaker={sp}: status={r0['status']}")
                for emo in accepted:  # 全值覆盖：生产映射表会向所有 doubao 音色下发中文枚举，须逐音色实测
                    r = await _one_call(client, domain, key, sp, emo)
                    report["stage_b"].append({"speaker": sp, **r})
                    flag = "✅" if r["status"] == "ok" else "❌"
                    print(f"  {flag} speaker={sp} emotion={emo!r}: status={r['status']} err={r['error']}")

    print("\n=== JSON ===")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
