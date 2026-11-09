#!/usr/bin/env python3
"""video_gen 360 通道切换——部署验收套件（2026-08-21 计划 §五 AC1~AC10）。

用法（测试服部署后执行）：
    cd backend && uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 9
    uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 1,2,5   # 分镜类
    uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 3,10    # 出片类（真出片，慢）
    uv run python scripts/acceptance/video_gen_360/run_acceptance.py --snapshot   # AC8 部署前快照
    uv run python scripts/acceptance/video_gen_360/run_acceptance.py --diff pre.json post.json  # AC8 diff
    uv run python scripts/acceptance/video_gen_360/run_acceptance.py --ac 4 --expect-fallback  # AC4 演练（先注入坏 key）

环境变量：
    VG_ACCEPT_BASE   网关基址（默认 http://localhost:8001）
    VG_ACCEPT_TOKEN  可选 Bearer token（鉴权模式部署时）
    VG_ACCEPT_USER   任务归属用户 id（默认 default）

AC4（降级演练）操作序（需服务器侧配合，README 有完整 runbook）：
    1) 测试服 config.yaml 把 qihu_api_key 换成无效值（保留 ark_api_key）→ 重启 gateway；
    2) 跑 --ac 4：并发提交短 ai_video 任务，断言全部成功（= 落 Ark）且无 4xx 冒泡；
    3) 恢复 key → 重启 → 恢复后新任务回 360（验证「恢复后切回」）。

AC8（跨模块隔离）：部署**前**在旧版本上 --snapshot 存 pre.json；部署后 --snapshot
存 post.json；--diff 两文件——models 名单与顺序、默认模型、providers 列表必须
零差异（本批只在尾部追加，不动 models[0]）。

注意：AC3/AC10 会真实调付费 API 并等待渲染（每任务数分钟）。
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
BASELINE = json.loads((HERE / "baseline.json").read_text(encoding="utf-8"))

BASE = os.environ.get("VG_ACCEPT_BASE", "http://localhost:8001").rstrip("/")
TOKEN = os.environ.get("VG_ACCEPT_TOKEN", "")
USER = os.environ.get("VG_ACCEPT_USER", "default")

# 固定素材（2 题材 × 8 帧，AC5 口径；AC1 用同一脚本池）
TOPIC_SCRIPTS = {
    "职场沟通": "有效的职场沟通包含三个基础。第一，倾听比表达更重要，听懂对方的真实诉求。"
    "第二，反馈要及时且具体，指出行为而非评价人格。第三，非暴力表达，用我开头描述感受，而不是你开头指责对方。"
    "练习这三点，可以让团队协作效率显著提升，减少误解和内耗。"
    "沟通不是天赋，而是可以刻意练习的技能，从今天的下一次对话开始。",
    "复利思维": "复利是世界上最强大的力量之一。本金一万，年化百分之十，七年后翻倍。"
    "知识复利同样成立，每天进步百分之一，一年后是原来的三十七倍。"
    "健康复利最容易被忽视，每天运动三十分钟，十年后的身体状态天差地别。"
    "人脉复利来自持续的价值输出，而非索取。理解复利的关键是时间维度，让时间成为朋友。",
}

_results: list[dict] = []


def _client() -> httpx.Client:
    """工厂：返回未发送过请求的 client（httpx ≥0.28 先发请求再进 with 会
    "Cannot open a client instance more than once"）——鉴权引导由
    :func:`_authed` 在 with 块内执行。"""

    class _AuthedClient(httpx.Client):
        def __enter__(self):
            super().__enter__()
            _bootstrap_auth(self)
            return self

    headers = {}
    if TOKEN:
        headers["Authorization"] = f"Bearer {TOKEN}"
    return _AuthedClient(base_url=BASE, headers=headers, timeout=60)


def _bootstrap_auth(c: httpx.Client) -> None:
    """鉴权部署下的引导：initialize 首个 admin（幂等：已初始化则跳过）→
    会话 cookie 自动落 jar → 取 csrf_token cookie 并以 X-CSRF-Token 头全局携带。"""
    import os

    email = os.environ.get("VG_ACCEPT_ADMIN_EMAIL", "vg360@trainerai-acceptance.cn")
    # 固定默认密码：initialize 只生效一次，后续重跑须以同账密 login。
    password = os.environ.get("VG_ACCEPT_ADMIN_PASSWORD", "vg360-acceptance-local")
    # CSRF 先取（auth POST 本身也要头），成功后头继续全局携带。
    c.get("/api/v1/auth/csrf-token")
    csrf = c.cookies.get("csrf_token")
    if not csrf:
        print("  [auth] 无 csrf cookie——部署可能未启用 CSRF 中间件，继续裸跑")
    else:
        c.headers["X-CSRF-Token"] = csrf
    r = c.post("/api/v1/auth/initialize", json={"email": email, "password": password})
    if r.status_code == 409:  # already initialized → login
        r2 = c.post("/api/v1/auth/login/local", data={"username": email, "password": password})
        if r2.status_code != 200:
            print(f"  [auth] 已初始化且默认账密登录失败（{r2.status_code}）——请用 VG_ACCEPT_ADMIN_EMAIL/PASSWORD 传入既有账号")
            return
    elif r.status_code not in (200, 201):
        print(f"  [auth] initialize {r.status_code}: {r.text[:120]}")
        return
    # auth POST 响应会**轮换** csrf cookie——旧头失效（403 根因）。auth 完成后
    # 重取一次（GET 返回当前 cookie token），头以最新为准。
    r3 = c.get("/api/v1/auth/csrf-token")
    token = (r3.json() or {}).get("token") or c.cookies.get("csrf_token")
    if token:
        c.headers["X-CSRF-Token"] = token
    print("  [auth] 会话+CSRF 就绪")


def _record(ac: str, name: str, ok: bool, detail: str = "") -> bool:
    _results.append({"ac": ac, "check": name, "ok": ok, "detail": detail})
    mark = "PASS" if ok else "FAIL"
    print(f"[{mark}] {ac} {name} {detail}")
    return ok


def _create_task(c: httpx.Client, payload: dict) -> str:
    r = c.post("/api/video/create", json=payload)
    r.raise_for_status()
    data = r.json()
    # 端点返回 list[TaskBrief]（video_count 决策移除后恒单元素）
    item = data[0] if isinstance(data, list) else data
    task_id = item.get("id") or item.get("task_id")
    if not task_id:
        raise RuntimeError(f"no task id in create response: {data}")
    return str(task_id)


def _get(c: httpx.Client, path: str) -> dict:
    r = c.get(path)
    r.raise_for_status()
    return r.json()


def _wait_storyboard(c: httpx.Client, task_id: str, budget_s: float = 600.0) -> dict:
    """轮询任务详情+storyboard，直到分镜就绪（frames 有 image_prompt）或失败。"""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        task = _get(c, f"/api/video/tasks/{task_id}")
        status = task.get("status")
        if status in ("failed", "error"):
            return {"failed": True, "task": task}
        try:
            sb = _get(c, f"/api/video/tasks/{task_id}/storyboard")
        except httpx.HTTPError:
            sb = {}
        frames = sb.get("frames") or []
        if frames and all(f.get("image_prompt") for f in frames):
            return {"failed": False, "storyboard": sb, "task": task}
        time.sleep(5)
    return {"failed": True, "timeout": True}


def _wait_done(c: httpx.Client, task_id: str, budget_s: float = 1800.0) -> dict:
    """等待任务终态，返回 task dict。"""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        task = _get(c, f"/api/video/tasks/{task_id}")
        if task.get("status") in ("ready", "completed", "failed", "error"):
            return task
        time.sleep(10)
    raise TimeoutError(f"task {task_id} not done in {budget_s}s")


def _download(c: httpx.Client, url: str, dest: Path) -> Path:
    if url.startswith("/"):
        r = c.get(url)
    else:
        r = httpx.get(url, follow_redirects=True, timeout=300)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _ffprobe_json(path: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _video_dims(path: Path) -> tuple[int, int]:
    info = _ffprobe_json(path)
    for s in info.get("streams", []):
        if s.get("codec_type") == "video":
            return int(s["width"]), int(s["height"])
    raise RuntimeError(f"no video stream in {path}")


def _video_duration(path: Path) -> float:
    out = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-print_format", "json", str(path)], capture_output=True, text=True, check=True)
    return float(json.loads(out.stdout)["format"]["duration"])


# ── AC1/AC2：分镜失败率 + 总耗时 ─────────────────────────────────────────────


def ac1_ac2(n_tasks: int = 20) -> None:
    acc = BASELINE["acceptance"]
    scripts = list(TOPIC_SCRIPTS.values())
    fails, times = [], []
    with _client() as c:
        for i in range(n_tasks):
            script = scripts[i % len(scripts)]
            t0 = time.monotonic()
            payload = {"video_type": "ai_image", "mode": "fixed", "script": script, "aspect": "16:9", "auto_render": False, "ai_normalize_script": False}
            try:
                task_id = _create_task(c, payload)
                res = _wait_storyboard(c, task_id, budget_s=420)
                elapsed = time.monotonic() - t0
                times.append(elapsed)
                if res.get("failed"):
                    fails.append({"task": task_id, "why": "timeout" if res.get("timeout") else res["task"].get("error")})
                    print(f"  task {i} {task_id} FAILED ({elapsed:.0f}s)")
                else:
                    print(f"  task {i} {task_id} storyboard ok ({elapsed:.0f}s)")
            except Exception as e:  # noqa: BLE001
                fails.append({"task": "?", "why": str(e)})
        rate = len(fails) / n_tasks
        _record("AC1", f"分镜失败率 {rate:.0%} (<{acc['ac1_failure_rate_max']:.0%})", rate < acc["ac1_failure_rate_max"], f"失败 {len(fails)}/{n_tasks}: {fails[:3]}")
        if times:
            baseline_t = BASELINE["storyboard"]["bulk_total_time_s_per_task"]
            mean_t = sum(times) / len(times)
            _record("AC2", f"分镜平均耗时 {mean_t:.0f}s ≤ 基线 {baseline_t:.0f}s", mean_t <= baseline_t * max(1.0, acc["ac2_time_budget_ratio"]), f"n={len(times)}")


# ── AC3：三档比例 100% 正确 ──────────────────────────────────────────────────


def ac3() -> None:
    tol = BASELINE["acceptance"]["ac3_ratio_tolerance"]
    short_script = "复利是时间的朋友，每天进步一点点，长期看是巨大的差距。理解复利，让时间为你工作。"
    ok_all = True
    with _client() as c:
        for aspect, rw, rh in (("16:9", 16, 9), ("1:1", 1, 1), ("9:16", 9, 16)):
            payload = {"video_type": "ai_video", "mode": "fixed", "script": short_script, "aspect": aspect, "auto_render": True, "ai_normalize_script": False, "n_scenes": 1}
            task_id = _create_task(c, payload)
            task = _wait_done(c, task_id)
            if task.get("status") != "ready":
                ok_all = False
                _record("AC3", f"{aspect} 任务未就绪", False, str(task.get("error"))[:200])
                continue
            url = (task.get("result") or {}).get("output_url") or f"/api/video/tasks/{task_id}/download"
            if task.get("status") != "ready":
                ok_all = False
                _record("AC3", f"{aspect} 未 ready", False, str(task.get("error_message"))[:200])
                continue
            dest = Path(f"/tmp/vg360_ac3_{aspect.replace(':', 'x')}.mp4")
            _download(c, url, dest)
            w, h = _video_dims(dest)
            target = rw / rh
            actual = w / h
            ok = abs(actual - target) / target <= tol
            ok_all &= ok
            _record("AC3", f"{aspect} → {w}x{h}", ok, f"目标比例 {target:.3f} 实际 {actual:.3f}")


# ── AC4：降级演练（2026-10-12 豆包聊天删册后 = 通道级全故障口径）──────────────


def ac4(n_tasks: int = 8) -> None:
    """坏 qihu key 演练（语义退化后的诚实口径，终审 F3）。

    原设计（2026-08-21）：script_model 覆盖走 Ark LLM 隔离分镜与生视频通道，
    断言 100% 落 Ark。2026-10-12 豆包聊天删册后非 360 注册聊天模型不存在，
    坏 key 下分镜 LLM 与生视频通道同步故障——隔离前提消失，**「落 Ark」断言
    永不可能满足**。本用例现为「通道级全故障」演练：断言全部任务在分镜阶段
    即失败（fail-fast + error 可见，而非静默挂死），覆盖 E6 显式报错路径。
    原隔离语义须另议注册非 360 通道聊天模型后重设计（超出花名册批范围）。
    """
    acc = BASELINE["acceptance"]
    short_script = "短句测试降级链路，验证兜底可用。"
    failed_visible, unexpected = 0, []
    with _client() as c:
        ids = []
        for _ in range(n_tasks):
            payload = {"video_type": "ai_video", "mode": "fixed", "script": short_script, "aspect": "16:9", "auto_render": True, "ai_normalize_script": False, "n_scenes": 1, "script_model": "qwen3.8-max"}
            ids.append(_create_task(c, payload))
        for tid in ids:
            try:
                task = _wait_done(c, tid, budget_s=2400)
                status = task.get("status")
                error = str(task.get("error") or "")[:150]
                if status == "ready":
                    unexpected.append({tid: "坏 key 下意外成功（分镜 LLM 未被注入故障？检查 key 与 script_model 覆盖）"})
                elif status in ("failed", "error") and error:
                    failed_visible += 1
                else:
                    unexpected.append({tid: f"非 ready 但无可见错误（status={status}, error={error!r}）——期望 fail-fast + error 可见"})
            except TimeoutError as e:
                unexpected.append({tid: f"超时而非快速失败（静默挂死即回归）：{e}"})
    _record(
        "AC4",
        f"通道级全故障（退化口径）：{failed_visible}/{n_tasks} 任务快速失败且错误可见",
        failed_visible == n_tasks and n_tasks >= acc["ac4_min_tasks"],
        f"unexpected: {unexpected[:3]}",
    )
    print("  （原「100% 落 Ark」隔离断言已随 2026-10-12 豆包聊天删册失效——seedance→Ark 兜底")
    print("    行为的覆盖由单测 test_seedance_360_fallback.py 继续承担；如需真机演练须先重引")
    print("    非 360 通道注册聊天模型。恢复 key 后重跑同类任务确认恢复 = runbook 步骤 3）")


# ── AC5：逐帧提示词质量 ──────────────────────────────────────────────────────


def _adjacent_similarity(prompts: list[str]) -> float:
    ratios = [difflib.SequenceMatcher(None, a, b).ratio() for a, b in zip(prompts, prompts[1:])]
    return sum(ratios) / len(ratios) if ratios else 0.0


def ac5() -> None:
    st = BASELINE["storyboard"]
    acc = BASELINE["acceptance"]
    topic_sig = re.compile(r"以「.+」为主题的具体写实教学场景")
    all_prompts: list[str] = []
    anchor_missing = 0
    with _client() as c:
        for topic, script in TOPIC_SCRIPTS.items():
            payload = {"video_type": "ai_image", "mode": "generate", "topic": topic, "n_scenes": 8, "aspect": "16:9", "auto_render": False}
            task_id = _create_task(c, payload)
            res = _wait_storyboard(c, task_id, budget_s=600)
            if res.get("failed"):
                _record("AC5", f"题材「{topic}」分镜失败", False)
                return
            sb = res["storyboard"]
            prompts = [f["image_prompt"] for f in sb["frames"]]
            all_prompts.extend(prompts)
            if not (sb.get("global_anchor") or sb.get("anchor")):
                anchor_missing += 1
            sim = _adjacent_similarity(prompts)
            _record("AC5", f"「{topic}」相邻相似度 {sim:.2f} ≤ 基线 {st['adjacent_similarity']}", sim <= acc["ac5_similarity_max"])
    if not all_prompts:
        _record("AC5", "无提示词产出", False)
        return
    fallback_hits = sum(1 for p in all_prompts if topic_sig.search(p or ""))
    ratio = fallback_hits / len(all_prompts)
    _record("AC5", f"锚定覆盖（无锚任务 {anchor_missing}/2）", anchor_missing == 0)
    _record("AC5", f"主题模板兜底帧占比 {ratio:.0%} = 0", ratio <= acc["ac5_topic_fallback_ratio_max"])
    lens = sorted(len(p) for p in all_prompts)
    median = lens[len(lens) // 2]
    lo, hi = st["prompt_len_range"]
    _record("AC5", f"长度中位数 {median} ∈ [{lo},{hi}]", lo <= median <= hi)


# ── AC6/AC7：预览同模型 + 参考图不丢 ────────────────────────────────────────


def ac6_ac7() -> None:
    with _client() as c:
        payload = {"video_type": "ai_image", "mode": "generate", "topic": "复利思维", "n_scenes": 4, "aspect": "16:9", "auto_render": False, "image_provider": "seedream-360"}
        task_id = _create_task(c, payload)
        res = _wait_storyboard(c, task_id, budget_s=600)
        if res.get("failed"):
            _record("AC6", "分镜失败", False)
            return
        sb = res["storyboard"]
        # 预览图生成（AC6：经 make_ai_image('seedream-360')——服务端日志/来源标记核验）
        prev_ok = 0
        for f in sb["frames"][:2]:
            r = c.post(f"/api/video/tasks/{task_id}/frames/{f['index']}/preview?user_id={USER}")
            # 端点是异步设计（routers/video_gen.py:1549 声明 status_code=202，
            # already_running 分支同样按 202 返回）——202 即受理成功，此前断言
            # == 200 导致 prev_ok 恒 0、AC6 恒误报。
            if r.status_code == 202:
                prev_ok += 1
        # 预览是 202 异步——轮询至 240s（seedream 单帧 30-60s×2 并发）
        with_url = 0
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            sb2 = _get(c, f"/api/video/tasks/{task_id}/storyboard")
            with_url = sum(1 for f in sb2.get("frames", []) if f.get("visual_url"))
            if with_url >= 2:
                break
            time.sleep(10)
        _record("AC6", f"预览生成 {prev_ok}/2、帧 visual_url {with_url}", prev_ok == 2 and with_url >= 2, "产物来源由服务端日志 providers.qihu_image（=seedream-360 类）核验")
        # AC7：seedream-360 白名单后无 dropped sidecar 告警（详情 warnings 字段）
        task = _get(c, f"/api/video/tasks/{task_id}")
        warnings_blob = json.dumps(task.get("warnings") or task.get("degradations") or {}, ensure_ascii=False)
        _record("AC7", "无参考图丢弃告警", "不支持参考图" not in warnings_blob and "i2i" not in warnings_blob, warnings_blob[:120])


# ── AC10：续写效果（>15s 旁白单帧无静止凑时长）──────────────────────────────


def ac10() -> None:
    long_script = (
        "复利的力量在于时间的积累。想象一颗种子长成参天大树，需要数十年的阳光雨水。"
        "财富的复利如此，每天一点点的积累，在十年后形成惊人的差距。"
        "知识的复利如此，今天读的一页书，会成为明天思考的基石。"
        "健康的复利如此，每一次锻炼都在为未来的身体储蓄。"
        "关系的复利如此，持续的价值输出会吸引同频的人。"
        "理解了复利，就会明白为什么坚持比聪明更重要。"
    )
    with _client() as c:
        # ② >15s 旁白帧（计划 AC10 判据②）：帧内续写（产物动态覆盖 ≥ 旁白，无静止凑时长）
        payload = {"video_type": "ai_video", "mode": "fixed", "script": long_script, "aspect": "16:9", "auto_render": True, "ai_normalize_script": False, "n_scenes": 1}
        task_id = _create_task(c, payload)
        task = _wait_done(c, task_id, budget_s=2400)
        if task.get("status") != "ready":
            _record("AC10", "任务未就绪", False, str(task.get("error"))[:200])
            return
        url = (task.get("result") or {}).get("output_url") or f"/api/video/tasks/{task_id}/download"
        dest = Path("/tmp/vg360_ac10.mp4")
        _download(c, url, dest)
        dur = _video_duration(dest)
        _record("AC10", f"产物时长 {dur:.1f}s ≥ 旁白预算 20s", dur >= 19.0)
        freezes = _detect_freezes(dest, dur)
        tail_freeze = [(s, d) for s, d in freezes if s + d >= dur - 1.0]
        _record("AC10", f"尾部冻结段 {tail_freeze or '无'}（无冻结尾帧凑时长）", not tail_freeze)
        if freezes:
            print(f"  全部冻结段: {freezes}")

        # ① ≤15s 旁白帧（计划 AC10 判据①，E9 超发裁剪 2026-08-25 补齐后覆盖）：
        # 产物按 TTS 时长精确裁剪（|产物−TTS| ≤1s）、全片无 ≥2s 冻结段。
        short_script = "复利是时间的朋友。每天进步一点点，十年后回头，差距惊人。"
        p2 = {"video_type": "ai_video", "mode": "fixed", "script": short_script, "aspect": "16:9", "auto_render": True, "ai_normalize_script": False, "n_scenes": 1}
        tid2 = _create_task(c, p2)
        task2 = _wait_done(c, tid2, budget_s=2400)
        if task2.get("status") != "ready":
            _record("AC10", "≤15s 任务未就绪", False, str(task2.get("error"))[:200])
            return
        url2 = (task2.get("result") or {}).get("output_url") or f"/api/video/tasks/{tid2}/download"
        dest2 = Path("/tmp/vg360_ac10_short.mp4")
        _download(c, url2, dest2)
        dur2 = _video_duration(dest2)
        sb2 = _get(c, f"/api/video/tasks/{tid2}/storyboard")
        tts_dur = float(((sb2.get("frames") or [{}])[0]).get("duration") or 0)
        _record("AC10", f"≤15s 产物 {dur2:.1f}s ≈ TTS {tts_dur:.1f}s（按 TTS 裁剪）", 0 < tts_dur <= 15.0 and abs(dur2 - tts_dur) <= 1.0)
        freezes2 = _detect_freezes(dest2, dur2)
        _record("AC10", f"≤15s 全片冻结段 {freezes2 or '无'}", not freezes2)


def _detect_freezes(dest: Path, dur: float) -> list[tuple[float, float]]:
    """freezedetect（≥2s 冻结段）→ [(start, duration)]。

    freezedetect 走 lavfi 元数据逐行输出（冒号后有空格、字段分行）：
      lavfi.freezedetect.freeze_start: 2
      lavfi.freezedetect.freeze_end: 5
      lavfi.freezedetect.freeze_duration: 3
    旧的单行联合正则 freeze_start:X\\s+freeze_duration:Y 恒不匹配（本机实测），
    须逐行收集再配对；冻结持续到 EOF 时无 freeze_end 行（tpad 凑时长的典型
    形态），按视频总长补端点。
    """
    out = subprocess.run(["ffmpeg", "-v", "info", "-i", str(dest), "-vf", "freezedetect=n=0.003:d=2", "-f", "null", "-"], capture_output=True, text=True)
    starts = [float(x) for x in re.findall(r"freeze_start:\s*([\d.]+)", out.stderr)]
    ends = [float(x) for x in re.findall(r"freeze_end:\s*([\d.]+)", out.stderr)]
    if len(ends) < len(starts):
        ends.append(dur)
    return [(s, e - s) for s, e in zip(starts, ends)]


# ── AC9：单测（本地跑，不碰服务器）─────────────────────────────────────────


def ac9() -> None:
    acc = BASELINE["acceptance"]
    _ = acc
    here = Path(__file__).resolve().parents[3]  # backend/
    out = subprocess.run(["uv", "run", "pytest", "tests/", "-q"], cwd=str(here), capture_output=True, text=True, shell=False)
    tail = out.stdout.strip().splitlines()[-1] if out.stdout.strip() else out.stderr[-200:]
    _record("AC9", f"pytest: {tail}", out.returncode == 0)


# ── AC8：跨模块隔离（snapshot / diff）──────────────────────────────────────


def snapshot(path: str) -> None:
    with _client() as c:
        models = _get(c, "/api/models")
        try:
            providers = _get(c, "/api/video/providers")
        except httpx.HTTPError:
            providers = {}
        snap = {
            "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "models": [m.get("name") for m in models.get("models", [])],
            "default_model": (models.get("models") or [{}])[0].get("name"),
            "video_providers": sorted(p.get("name", "") for p in providers.get("video", [])),
            "image_providers": sorted(p.get("name", "") for p in providers.get("image", [])),
        }
    Path(path).write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"snapshot → {path} (models[0]={snap['default_model']})")


def diff_snapshots(pre_path: str, post_path: str) -> None:
    pre = json.loads(Path(pre_path).read_text(encoding="utf-8"))
    post = json.loads(Path(post_path).read_text(encoding="utf-8"))
    diffs: list[str] = []
    if pre["models"] != post["models"]:
        # 允许尾部追加，不允许删除/重排/改默认
        if post["models"][: len(pre["models"])] != pre["models"]:
            diffs.append(f"models 名单重排/删除: {pre['models']} → {post['models']}")
        else:
            print(f"  （models 尾部追加: {post['models'][len(pre['models']) :]}）")
    if pre.get("default_model") != post.get("default_model"):
        diffs.append(f"默认模型变更: {pre.get('default_model')} → {post.get('default_model')}")
    if pre["video_providers"] != post["video_providers"]:
        added = set(post["video_providers"]) - set(pre["video_providers"])
        removed = set(pre["video_providers"]) - set(post["video_providers"])
        # 本批允许 +seedance-fast/seedance-mini（E2 型号选项）；不允许摘除
        if removed:
            diffs.append(f"video providers 被摘除: {sorted(removed)}")
        else:
            print(f"  （video providers 追加: {sorted(added)}）")
    if pre["image_providers"] != post["image_providers"]:
        diffs.append(f"image providers 变更: {pre['image_providers']} → {post['image_providers']}")
    _record("AC8", "跨模块路由 diff 为空", not diffs, "; ".join(diffs) or "仅允许的追加项")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ac", default="", help="逗号分隔的 AC 编号，如 1,2 / 3 / 9")
    ap.add_argument("--snapshot", metavar="OUT.json", help="AC8：记录路由快照")
    ap.add_argument("--diff", nargs=2, metavar=("PRE.json", "POST.json"), help="AC8：对比两份快照")
    ap.add_argument("--ac1-n", type=int, default=20, help="AC1 任务数")
    args = ap.parse_args()

    if args.snapshot:
        snapshot(args.snapshot)
        return 0
    if args.diff:
        diff_snapshots(args.diff[0], args.diff[1])
        return _summarize()

    wanted = {a.strip() for a in args.ac.split(",") if a.strip()}
    runners = {
        "1": lambda: ac1_ac2(args.ac1_n),
        "2": lambda: None,  # 与 AC1 同批计时
        "3": ac3,
        "4": ac4,
        "5": ac5,
        "6": ac6_ac7,
        "7": lambda: None,  # 与 AC6 同批
        "9": ac9,
        "10": ac10,
    }
    if not wanted:
        ap.print_help()
        return 2
    if "1" in wanted or "2" in wanted:
        ac1_ac2(args.ac1_n)
    for ac in sorted(wanted):
        if ac in ("1", "2"):
            continue
        runners.get(ac, lambda: None)()
    return _summarize()


def _summarize() -> int:
    failed = [r for r in _results if not r["ok"]]
    print("\n==== 验收汇总 ====")
    for r in _results:
        print(f"  [{'PASS' if r['ok'] else 'FAIL'}] {r['ac']} {r['check']}")
    print(f"共 {len(_results)} 项，失败 {len(failed)} 项")
    out = HERE / f"report-{time.strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps(_results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告: {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
