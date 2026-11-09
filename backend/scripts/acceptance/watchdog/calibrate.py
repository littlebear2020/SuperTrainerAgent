#!/usr/bin/env python3
"""W8（2026-08-24 看门狗活性重构）：预算校准脚本。

跑 ≥3 个 ≥50 帧真实任务（含续写帧，走 360 通道），采集：
① 阶段时长分布（单帧墙钟含续写、单次 LLM 耗时、TTS 整批）对照预算公式；
② 租约公式 vs 实测合成时长（动态租约是否覆盖合成 p99，worker 侧约束）；
③ 沉默 p99（相邻两次任务行写库间隔的最大值）--覆盖阶段心跳健康度。
p99 > 预算时按公式上调并记录原因，**禁止直接拍数**。

存档 ``backend/test-output/watchdog-calibration-YYYYMMDD/``（WD7 门禁产物）。

用法（测试服部署后执行；会真实调付费 API）：
    cd backend && uv run python scripts/acceptance/watchdog/calibrate.py
    uv run python scripts/acceptance/watchdog/calibrate.py --tasks 3 --min-frames 50 --script-file myscript.txt
    uv run python scripts/acceptance/watchdog/calibrate.py --poll 10   # 轮询间隔 10s

环境变量：
    VG_ACCEPT_BASE   网关基址（默认 http://localhost:8001）
    VG_ACCEPT_TOKEN  可选 Bearer token
    VG_ACCEPT_USER   任务归属用户 id（默认 default）

口径：阶段边界从任务行的 status/progress 采样序列推导（5/20/35=LLM 相、
60/75=TTS+BGM、75→90=帧渲染、95=composing、100=ready），预算值从
VideoGenConfig 的 W4 公式按实际帧数推导（标量来自 config.yaml）。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import secrets
import statistics
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

BACKEND = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(BACKEND))

BASE = os.environ.get("VG_ACCEPT_BASE", "http://localhost:8001").rstrip("/")
TOKEN = os.environ.get("VG_ACCEPT_TOKEN", "")
USER = os.environ.get("VG_ACCEPT_USER", "default")
# 2026-08-25：gateway 仅认 access_token cookie + CSRF 双提交（POST 强制）。
# ACCESS_TOKEN 传 JWT（容器内用 app.gateway.auth.create_access_token 铸造）；
# CSRF 同值进 cookie 与 X-CSRF-Token 头。
ACCESS_TOKEN = os.environ.get("ACCESS_TOKEN", "")
_CSRF = secrets.token_urlsafe(16)

DEFAULT_SCRIPT = (
    "本视频面向新任团队管理者，介绍跨部门协作的核心方法。"
    "第一，建立透明的信息同步机制，让每个成员都能看到项目全貌。"
    "第二，用固定节奏推进决策，避免临时会议打断深度工作。"
    "第三，把冲突当作信号而非事故，及时复盘并沉淀协作规范。"
    "第四，设定清晰的里程碑与验收标准，让协作成果可度量。"
    "第五，培养跨部门同理心，定期轮岗体验彼此的工作流。"
    "第六，建立知识库沉淀机制，避免组织记忆随人员流动流失。"
    "第七，用数据驱动复盘，区分相关性与因果性。"
    "第八，保持向上沟通的简洁与结构化，结论先行。"
    "第九，授权但不弃权，边界清晰才能释放主动性。"
    "第十，持续庆祝阶段性胜利，让协作获得正反馈。"
) * 8  # 长脚本 -> ≥50 帧


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    if ACCESS_TOKEN:
        h["Cookie"] = f"access_token={ACCESS_TOKEN}; csrf_token={_CSRF}"
        h["X-CSRF-Token"] = _CSRF
    return h


async def submit_task(client: httpx.AsyncClient, script: str, video_type: str = "ai_image") -> str:
    """提交任务。端点 = POST /api/video/create（routers/video_gen.py:474），响应是
    list[TaskBrief]（video_count 移除后恒单元素），任务 id 在 ``id`` 字段。"""
    resp = await client.post(
        f"{BASE}/api/video/create",
        headers=_headers(),
        json={"video_type": video_type, "script": script, "mode": "fixed", "aspect": "16:9"},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    item = data[0] if isinstance(data, list) else data
    task_id = item.get("id") or item.get("task_id")
    if not task_id:
        raise RuntimeError(f"create 响应里没有任务 id：{data!r}")
    return task_id


async def fetch_n_frames(client: httpx.AsyncClient, task_id: str) -> int | None:
    """帧数：优先 storyboard 端点（draft 落盘后即可读），回落任务详情 result。"""
    try:
        resp = await client.get(f"{BASE}/api/video/tasks/{task_id}/storyboard", headers=_headers(), timeout=30)
        if resp.status_code == 200:
            sb = resp.json()
            frames = sb.get("frames") if isinstance(sb, dict) else None
            if isinstance(frames, list) and frames:
                return len(frames)
    except Exception:
        pass
    try:
        resp = await client.get(f"{BASE}/api/video/tasks/{task_id}", headers=_headers(), timeout=30)
        if resp.status_code == 200:
            d = resp.json()
            sb = (d.get("result") or {}).get("storyboard", {})
            frames = sb.get("frames") if isinstance(sb, dict) else None
            if isinstance(frames, list) and frames:
                return len(frames)
    except Exception:
        pass
    return None


async def poll_task(client: httpx.AsyncClient, task_id: str, interval: float, deadline_s: float):
    """采样任务行（status/progress/updated_at）直到终态，返回时间线。"""
    timeline: list[dict] = []
    t0 = time.monotonic()
    last_status = None
    while time.monotonic() - t0 < deadline_s:
        resp = await client.get(f"{BASE}/api/video/tasks/{task_id}", headers=_headers(), timeout=30)
        if resp.status_code != 200:
            await asyncio.sleep(interval)
            continue
        d = resp.json()
        point = {
            "t": round(time.monotonic() - t0, 1),
            "status": d.get("status"),
            "progress": d.get("progress"),
            "updated_at": d.get("updated_at"),
            "retry_count": d.get("retry_count", 0),
            "error_message": d.get("error_message"),
        }
        timeline.append(point)
        last_status = d.get("status")
        if last_status in ("ready", "failed", "cancelled"):
            break
        await asyncio.sleep(interval)
    return timeline, last_status


def stage_durations(timeline: list[dict]) -> dict[str, float]:
    """从采样序列推导阶段墙钟（progress 检查点为界，对齐 _prepare_storyboard_
    and_audio 的实际写点：5->20 叙事解析逐块、20->35 分镜逐帧 prompt、
    35->60 TTS 整批+concat+SRT、60->75 BGM+prep 尾段、75->90 帧渲染、
    95->100 worker 合成）。"""
    phases = {
        "llm_phase": (5, 35),  # 叙事解析 + 分镜逐帧 prompt（LLM 循环）
        "tts_phase": (35, 60),  # TTS 整批 + concat + SRT/字幕预切
        "render_phase": (75, 90),  # 帧渲染
        "compose_phase": (95, 100),  # worker 合成（progress 95->100）
    }
    out: dict[str, float] = {}
    for name, (lo, hi) in phases.items():
        t_lo = next((p["t"] for p in timeline if (p["progress"] or 0) >= lo), None)
        t_hi = next((p["t"] for p in timeline if (p["progress"] or 0) >= hi), None)
        if t_lo is not None and t_hi is not None:
            out[name] = round(t_hi - t_lo, 1)
    # 沉默上界：相邻采样间的 updated_at 最大间隔（轮询分辨率的近似）
    max_silence = 0.0
    for a, b in zip(timeline, timeline[1:]):
        try:
            ua, ub = a.get("updated_at"), b.get("updated_at")
            ta = datetime.fromisoformat(ua.replace("Z", "+00:00")) if ua else None
            tb = datetime.fromisoformat(ub.replace("Z", "+00:00")) if ub else None
            if ta and tb:
                max_silence = max(max_silence, (tb - ta).total_seconds())
        except (ValueError, TypeError, AttributeError):
            pass
    out["max_observed_silence_s"] = round(max_silence, 1)
    return out


def budget_formulas(n_frames: int) -> dict[str, float]:
    """W4 预算公式（与本仓 config.yaml 的标量同源推导）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig()  # 测试服标量若有 config.yaml 覆盖，用 --config-scalars 检查
    svc = VideoGenService.__new__(VideoGenService)
    svc._config = cfg
    conc = cfg.effective_frame_concurrency(n_frames)
    return {
        "llm_budget": round(svc._stage_budget_llm(n_frames, concurrency=cfg.prompt_frame_concurrency)),
        "tts_budget": round(svc._stage_budget_tts(n_frames)),
        "render_budget": round(svc._stage_budget_frame_render(n_frames, concurrency=conc)),
        "frame_concurrency": conc,
        "stall_progress_freeze_seconds": cfg.stall_progress_freeze_seconds,
        "liveness_threshold_seconds": cfg.liveness_threshold_seconds,
        "n_frames": n_frames,
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=int, default=3, help="任务数（默认 3，门禁下限）")
    parser.add_argument("--min-frames", type=int, default=50, help="每任务最少帧数（默认 50，门禁下限）")
    parser.add_argument("--video-type", default="ai_image", help="video_type（ai_image 走 360 生图主力）")
    parser.add_argument("--script-file", default="", help="可选：脚本 txt 文件路径")
    parser.add_argument("--poll", type=float, default=15.0, help="采样间隔秒（默认 15）")
    parser.add_argument("--deadline", type=float, default=7200.0, help="单任务最长等待秒（默认 2h）")
    args = parser.parse_args()

    script = Path(args.script_file).read_text(encoding="utf-8") if args.script_file else DEFAULT_SCRIPT

    print(f"== W8 校准：{args.tasks} 个任务（≥{args.min_frames} 帧，{args.video_type}） ==")
    results = []
    async with httpx.AsyncClient() as client:
        for i in range(args.tasks):
            tid = await submit_task(client, script, args.video_type)
            print(f"[{i + 1}/{args.tasks}] submitted {tid}; polling every {args.poll}s ...")
            timeline, final = await poll_task(client, tid, args.poll, args.deadline)
            print(f"    final={final}, samples={len(timeline)}")
            # 帧数：storyboard 端点优先（draft 落盘后即可读），详情 result 兜底
            n_frames = await fetch_n_frames(client, tid)
            results.append({"task_id": tid, "final": final, "n_frames": n_frames, "timeline": timeline})

    # 汇总阶段时长 + 预算对照
    report: dict = {"generated_at": datetime.now(UTC).isoformat(), "base": BASE, "tasks": []}
    stage_samples: dict[str, list[float]] = {}
    all_ok = True
    for r in results:
        durs = stage_durations(r["timeline"])
        n = r["n_frames"] or args.min_frames
        budgets = budget_formulas(n)
        entry = {"task_id": r["task_id"], "final": r["final"], "n_frames": n, "durations": durs, "budgets": budgets}
        # WD7 断言：阶段时长 < 预算（p99 用样本最大值近似，样本≥3 时再算 p99）
        checks = {
            "llm": durs.get("llm_phase", 0) < budgets["llm_budget"],
            "tts": durs.get("tts_phase", 0) < budgets["tts_budget"],
            "render": durs.get("render_phase", 0) < budgets["render_budget"],
        }
        entry["checks"] = checks
        all_ok = all_ok and all(checks.values()) and r["final"] == "ready"
        for k, v in durs.items():
            if isinstance(v, (int, float)):
                stage_samples.setdefault(k, []).append(v)
        report["tasks"].append(entry)
        print(f"  {r['task_id']}: final={r['final']} n={n} durs={durs} budgets={budgets} checks={checks}")

    summary: dict = {}
    for k, vals in stage_samples.items():
        summary[k] = {
            "n": len(vals),
            "max": max(vals) if vals else None,
            "p99": round(statistics.quantiles(vals, n=100)[98], 1) if len(vals) >= 2 else (max(vals) if vals else None),
        }
    report["stage_summary"] = summary
    report["all_within_budget"] = all_ok
    print(f"\n阶段汇总（p99 口径，样本不足时=max）：{json.dumps(summary, ensure_ascii=False)}")
    print(f"WD7 判定：{'PASS' if all_ok else 'FAIL'}（p99 < 预算值 且 任务全部 ready）")

    out_dir = BACKEND / "test-output" / f"watchdog-calibration-{datetime.now(UTC):%Y%m%d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "calibration.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"存档：{out}")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
