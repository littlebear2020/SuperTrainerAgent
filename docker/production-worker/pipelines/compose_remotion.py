"""Worker pipeline: compose remotion_course video (course-standard template).

T3/T12（2026-09-08 remotion-course 计划）。与 ffmpeg 池的 ai_visual/mixed 管线并列，
在独立 remotion-worker 池运行（WORKER_POOL=remotion）：

  1. 本地化素材（签名 URL 24h 过期，403 回报可重试错误；UA 头必带——CDN 拦裸 urllib）
  2. per-render staging public 目录（assets/ 前缀寻址；--public-dir 传入——
     T-A7 双槽并发安全，替代共享 public/assets 符号链接）
  3. npx remotion render DataDrivenCourse（Chrome headless shell，租约感知超时）
  4. QC 扫描：blackdetect 全片 / freezedetect 仅视频素材场景 / silencedetect 仅
     场景正文区间（intro/outro 本就无声、静态图文帧"冻结"是设计行为——不做这两类
     误报兜底的话每条任务都会进 qc_repair 死循环）
  5. loudnorm 两遍法 -14 LUFS（D10；不混 BGM——BGM 归属模板内 <Audio>）
  6. 缩略图 + 返回 output 路径（_validate_deliverable 由框架调用）

Pipeline function signature (matches framework):
    fn(task_dir, payload, options, progress_cb, lease_remaining=None) -> Path
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from pipelines import net_guard

opener = net_guard.build_guarded_opener()  # 30x 每跳守卫（评审修复轮）

logger = logging.getLogger(__name__)

# 模板/浏览器路径 env 可覆盖（原生本机验证与镜像内 /opt 布局共用一份代码；
# CHROME_PATH 留空 = 不传 --browser-executable，remotion 用自带管理的浏览器）
TEMPLATE_DIR = Path(os.environ.get("REMOTION_TEMPLATE_DIR", "/opt/course-standard"))
_CHROME_ENV = os.environ.get("REMOTION_CHROME_PATH", "/opt/chrome-headless-shell/chrome-headless-shell")
CHROME_PATH = Path(_CHROME_ENV) if _CHROME_ENV else None
_DOWNLOAD_TIMEOUT = 120
# QC 阈值：黑场 0.5s（转场淡入淡出最深点不足 0.5s 纯黑，不会误报）
_QC_BLACK_MIN_S = 0.5
# 静音 4s（句间停顿远短于此；intro/outro 区间已剔除）
_QC_SILENCE_MIN_S = 4.0
# 冻结 1.0s 且 ≥60% 落在视频素材场景内
_QC_FREEZE_MIN_S = 1.0
_QC_FREEZE_OVERLAP_RATIO = 0.6
# 目标响度（D10）：-14 LUFS 流媒体标准
_LOUDNESS_I = -14.0
_LOUDNESS_TP = -1.5
_LOUDNESS_LRA = 11.0


class RemotionAssetError(RuntimeError):
    """素材本地化失败（403/超时/损坏）。签名 URL 24h 过期 → 可重试类错误：
    重提交/重渲染任务即可恢复，不删任务目录。"""


def _download_asset(url: str, dest: Path) -> None:
    """下载远端素材到 dest。UA 头必带（Worker 403 修复结论：裸 urllib 被 CDN 拦）。

    C2（2026-10-05 审计 §4-8）：出站前经 net_guard 私网/域名白名单校验（连
    接前拒绝）；下载体积受 REMOTION_ASSET_MAX_BYTES 上限约束。
    403/超时抛 RemotionAssetError（可重试），不静默降级——素材缺失的降级由
    模板侧 VisualPlaceholder 承担，下载失败必须让任务可见地失败。
    """
    net_guard.assert_public_http_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    limit = net_guard.max_asset_bytes()
    try:
        with opener.open(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
            buf = bytearray()
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > limit:
                    raise RemotionAssetError(f"素材下载超限（>{limit}B，可重试）: {url[:200]}")
            dest.write_bytes(bytes(buf))
    except urllib.error.HTTPError as e:
        raise RemotionAssetError(
            f"素材下载失败 HTTP {e.code}（签名 URL 可能已过期，可重试）: {url[:200]}"
        ) from e
    except RemotionAssetError:
        raise
    except Exception as e:
        raise RemotionAssetError(f"素材下载失败（可重试）: {url[:200]}: {e}") from e
    if dest.stat().st_size == 0:
        raise RemotionAssetError(f"素材下载为 0 字节（可重试）: {url[:200]}")


def _localize_props(props: dict, task_dir: Path) -> tuple[dict, list[str]]:
    """素材本地化 + props 路径重写。返回 (新 props, degradations)。"""
    degradations: list[str] = []
    assets_dir = task_dir / "remotion_assets"
    counter = [0]

    def localize(value: str | None, prefix: str, is_video: bool) -> str | None:
        if not value:
            return None
        if value.startswith(("http://", "https://")):
            counter[0] += 1
            ext = ".mp4" if is_video else (Path(value.split("?")[0]).suffix or ".png")
            dest = assets_dir / f"{prefix}_{counter[0]:03d}{ext}"
            logger.info("localizing %s -> %s", value[:120], dest.name)
            _download_asset(value, dest)
            return f"assets/remotion_assets/{dest.name}"
        if (task_dir / value).exists():
            return f"assets/{value}"
        degradations.append(f"{prefix}: local path missing {value!r}")
        return None

    for scene in props.get("scenes") or []:
        audio = scene.get("audioFile")
        if audio:
            new = localize(audio, "audio", False)
            if new is None:
                degradations.append(f"scene {scene.get('index')}: audio missing -> silent scene")
            scene["audioFile"] = new
        visual = scene.get("visual") or {}
        if visual.get("path"):
            kind = visual.get("kind")
            new = localize(visual["path"], f"visual_s{scene.get('index', 0)}", kind == "video")
            if new is None:
                degradations.append(
                    f"scene {scene.get('index')}: visual missing -> VisualPlaceholder"
                )
                visual["path"] = None
            else:
                visual["path"] = new

    bgm = props.get("bgm")
    if isinstance(bgm, dict) and bgm.get("enabled") and bgm.get("path"):
        new = localize(bgm["path"], "bgm", False)
        if new is None:
            degradations.append("bgm: missing -> muted")
            bgm["enabled"] = False
        else:
            bgm["path"] = new

    brand = props.get("brand") or {}
    if brand.get("logoPath"):
        new = localize(brand["logoPath"], "brand_logo", False)
        if new is None:
            degradations.append("brand.logoPath: missing -> gradient badge")
            brand["logoPath"] = None
        else:
            brand["logoPath"] = new

    return props, degradations


def _scene_time_ranges(props: dict) -> tuple[float, list[tuple[float, float]], list[tuple[float, float]]]:
    """返回 (总时长秒, 场景正文区间列表, 视频素材场景区间列表)。

    时间轴与模板 calculateTimeline 同构：intro + ΣdurationInFrames + outro，
    帧转秒用 props.fps。QC 的 silence/freeze 检测只在对应区间内计异常。
    """
    fps = float(props.get("fps") or 30)
    brand = props.get("brand") or {}
    intro = float((brand.get("intro") or {}).get("durationInFrames") or 0)
    outro = float((brand.get("outro") or {}).get("durationInFrames") or 0)
    t = intro / fps
    ranges: list[tuple[float, float]] = []
    video_ranges: list[tuple[float, float]] = []
    for scene in props.get("scenes") or []:
        try:
            dur = max(1, round(float(scene.get("durationInFrames", 0)))) / fps
        except (TypeError, ValueError):
            continue
        ranges.append((t, t + dur))
        if (scene.get("visual") or {}).get("kind") == "video":
            video_ranges.append((t, t + dur))
        t += dur
    return t + outro / fps, ranges, video_ranges


def _overlap(a: tuple[float, float], b: tuple[float, float]) -> float:
    return max(0.0, min(a[1], b[1]) - max(a[0], b[0]))


def _run_qc_scan(video: Path, props: dict) -> list[dict]:
    """QC 三扫描（T12）：blackdetect 全片 / silencedetect 场景正文 / freezedetect
    视频素材场景。异常归到场景序号，供 gateway dirty_frames 重生回路消费。

    worker 只上报不生成（生成能力与凭证在 gateway）。
    """
    total_s, scene_ranges, video_ranges = _scene_time_ranges(props)
    issues: list[dict] = []

    def scene_of(start: float, duration: float) -> int:
        mid = start + duration / 2
        for i, (s, e) in enumerate(scene_ranges):
            if s <= mid < e:
                return i
        return -1

    # ── blackdetect：全片（转场最深点 <0.5s 纯黑不触发） ──
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(video),
             "-vf", f"blackdetect=d={_QC_BLACK_MIN_S}:pix_th=0.10", "-an", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        for line in (proc.stderr or "").splitlines():
            m = re.search(r"black_start:([\d.]+).*black_duration:([\d.]+)", line)
            if m:
                start, dur = float(m.group(1)), float(m.group(2))
                if start < total_s:
                    issues.append({"scene": scene_of(start, dur), "issue": "black",
                                   "start": round(start, 2), "duration": round(dur, 2)})
    except Exception:
        logger.warning("QC blackdetect failed (skip)", exc_info=True)

    # ── silencedetect：仅场景正文区间（intro/outro 无声是设计行为） ──
    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(video),
             "-af", f"silencedetect=noise=-35dB:d={_QC_SILENCE_MIN_S}", "-vn", "-f", "null", "-"],
            capture_output=True, text=True, timeout=180,
        )
        for line in (proc.stderr or "").splitlines():
            m = re.search(r"silence_start:([\d.]+)", line)
            m2 = re.search(r"silence_duration:([\d.]+)", line)
            if m:
                start = float(m.group(1))
                dur = float(m2.group(1)) if m2 else _QC_SILENCE_MIN_S
                for i, (s, e) in enumerate(scene_ranges):
                    ov = _overlap((start, start + dur), (s, e))
                    if ov >= _QC_SILENCE_MIN_S:
                        issues.append({"scene": i, "issue": "silence",
                                       "start": round(start, 2), "duration": round(dur, 2)})
                        break
    except Exception:
        logger.warning("QC silencedetect failed (skip)", exc_info=True)

    # ── freezedetect：仅视频素材场景（静态图文帧"冻结"是设计行为，全片扫必误报） ──
    if video_ranges:
        try:
            proc = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", str(video),
                 "-vf", f"freezedetect=n=-60dB:d={_QC_FREEZE_MIN_S}", "-an", "-f", "null", "-"],
                capture_output=True, text=True, timeout=180,
            )
            for line in (proc.stderr or "").splitlines():
                m = re.search(r"freeze_start:([\d.]+).*freeze_duration:([\d.]+)", line)
                if m:
                    start, dur = float(m.group(1)), float(m.group(2))
                    span = (start, start + dur)
                    for i, vr in enumerate(video_ranges):
                        # 只有当冻结段 ≥60% 落在视频素材场景内才计异常
                        if _overlap(span, vr) / max(dur, 0.01) >= _QC_FREEZE_OVERLAP_RATIO:
                            # 视频区间序号 → 场景序号
                            scene_idx = next(
                                (j for j, (s, e) in enumerate(scene_ranges)
                                 if abs(s - vr[0]) < 1e-6 and abs(e - vr[1]) < 1e-6),
                                scene_of(start, dur),
                            )
                            issues.append({"scene": scene_idx, "issue": "freeze",
                                           "start": round(start, 2), "duration": round(dur, 2)})
                            break
        except Exception:
            logger.warning("QC freezedetect failed (skip)", exc_info=True)

    return issues


def _render_timeout(lease_remaining: float | None) -> int:
    """渲染 subprocess 超时：必须 ≤ 租约−300（租约不变量），上限 1h，缺省 1h。"""
    if lease_remaining is None:
        return 3600
    return max(min(int(lease_remaining) - 300, 3600), 60)


def _normalize_loudness_two_pass(video: Path, output: Path) -> Path:
    """loudnorm 两遍法 -14 LUFS（D10）。无音轨/失败返回原文件——响度是增强项，
    不该阻断出片（与 ffmpeg 池 _normalize_loudness 同哲学，但按计划用两遍法）。"""
    # 第一遍：测量
    try:
        probe = subprocess.run(
            ["ffmpeg", "-hide_banner", "-i", str(video),
             "-af", f"loudnorm=I={_LOUDNESS_I}:TP={_LOUDNESS_TP}:LRA={_LOUDNESS_LRA}:print_format=json",
             "-f", "null", "-"],
            capture_output=True, text=True, timeout=300,
        )
        m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr or "", re.S)
        if not m:
            logger.warning("loudnorm pass1 no JSON (no audio track?); skip normalization")
            return video
        measured = json.loads(m.group(0))
        if float(measured.get("input_i", "-inf")) in (float("-inf"),) or measured.get("input_i") in (None, "-inf"):
            return video
    except Exception:
        logger.warning("loudnorm pass1 failed; skip normalization", exc_info=True)
        return video
    # 第二遍：按测量值线性拟合应用
    af = (
        f"loudnorm=I={_LOUDNESS_I}:TP={_LOUDNESS_TP}:LRA={_LOUDNESS_LRA}"
        f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}:linear=true"
    )
    try:
        proc = subprocess.run(
            ["ffmpeg", "-y", "-i", str(video), "-af", af,
             "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", str(output)],
            capture_output=True, timeout=600,
        )
        if proc.returncode == 0 and output.exists() and output.stat().st_size > 0:
            return output
        logger.warning("loudnorm pass2 rc=%s; keep original", proc.returncode)
        return video
    except Exception:
        logger.warning("loudnorm pass2 failed; keep original", exc_info=True)
        return video


def _extract_thumbnail(video: Path, task_dir: Path) -> None:
    try:
        probe = subprocess.run(
            ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
            capture_output=True, text=True, timeout=30,
        )
        duration = float(probe.stdout.strip())
        ss = max(1.0, duration * 0.25)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(ss), "-i", str(video), "-frames:v", "1", "-q:v", "2",
             str(task_dir / "thumbnail.jpg")],
            capture_output=True, timeout=60,
        )
    except Exception:
        logger.warning("thumbnail extraction failed", exc_info=True)


@contextmanager
def _staged_public_dir(task_dir: Path):
    """T-A7（M11，2026-09-09 feedback-round1）：per-render staging public 目录。

    修前重建**共享** public/assets 符号链接（_reset_public_assets，无锁）——渲染与
    still 两槽并发（still 设计上就是渲染期可并发）时互相切走对方素材链接 →
    占位帧 + QC 循环。现在每次渲染/still 建独立 staging 目录（内含
    assets → task_dir 符号链接），经 ``--public-dir`` 传给 remotion，彻底消除
    共享链接；退出时清理（staging 目录与符号链接都是本次渲染私有）。
    模板 public/ 下无其他静态文件（素材全部经 assets/ 寻址），staging 目录
    只含 assets 链接即可等价。
    """
    stage = Path(tempfile.mkdtemp(prefix=".public-stage-", dir=str(TEMPLATE_DIR)))
    (stage / "assets").symlink_to(task_dir, target_is_directory=True)
    try:
        yield stage
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def compose_remotion(task_dir: Path, payload: dict, options: dict, progress_cb=None, lease_remaining: float | None = None) -> Path:
    """主入口：本地化 → render → QC → loudnorm → 缩略图。返回 output.mp4 路径。

    T7：payload.task_kind == "still" 走静帧预览分支（remotion_still 任务，秒级、
    短租约，无 QC/响度后处理；产物 PNG，交付闸走 still 模式）。
    """
    from pipelines._qc_exception import QCRepairNeeded  # noqa: PLC0415 — 共享模块（worker.py 双实例陷阱）

    if payload.get("task_kind") == "still":
        return _render_still(task_dir, payload, progress_cb)

    props = payload.get("remotion_props")
    if not isinstance(props, dict) or not props.get("scenes"):
        raise RuntimeError("payload 缺少 remotion_props.scenes（gateway 未构建 props？）")

    def _progress(pct: int) -> None:
        if progress_cb:
            try:
                progress_cb(pct)
            except Exception:
                logger.warning("progress_cb failed", exc_info=True)

    # 1) 素材本地化 + 路径重写
    _progress(3)
    props, degradations = _localize_props(dict(props), task_dir)
    if degradations:
        logger.warning("remotion localization degradations: %s", degradations)

    # 2) props 落盘（调试 + 重渲复用）
    props_file = task_dir / "remotion_props.json"
    props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    # 3) 渲染（T-A7：staging public 目录经 --public-dir 传入，双槽并发安全）
    _progress(8)
    raw_output = task_dir / "remotion_raw.mp4"
    timeout = _render_timeout(lease_remaining)
    logger.info("remotion render start: task_dir=%s timeout=%ds scenes=%d",
                task_dir, timeout, len(props.get("scenes") or []))
    try:
        with _staged_public_dir(task_dir) as public_dir:
            cmd = [
                "npx", "remotion", "render", "src/index.ts", "DataDrivenCourse", str(raw_output),
                f"--props={props_file}",
                f"--public-dir={public_dir}",
                *( [f"--browser-executable={CHROME_PATH}"] if CHROME_PATH else [] ),
                "--log=error",
                "--concurrency=2",
            ]
            proc = subprocess.run(cmd, cwd=TEMPLATE_DIR, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(
            f"Remotion 渲染超时（{timeout}s，租约内未完成）；任务可重试"
        ) from e
    if proc.returncode != 0 or not raw_output.exists():
        tail = (proc.stderr or "")[-2000:]
        raise RuntimeError(f"Remotion 渲染失败 (rc={proc.returncode}): {tail}")

    # 4) QC 扫描（T12/D19：异常 → qc_repair，不标 ready）。
    # payload.qc_skip = gateway 降级占位交付的终态重渲（已记 degradations，
    # 跳过 QC 防无限循环——降级占位画面本就无素材可言）。
    _progress(82)
    if payload.get("qc_skip"):
        logger.info("qc_skip set — skipping QC scan (degraded delivery)")
        qc_issues = []
    else:
        qc_issues = _run_qc_scan(raw_output, props)
    qc_sidecar = task_dir / "qc_scan.json"
    qc_sidecar.write_text(json.dumps({"issues": qc_issues, "skipped": bool(payload.get("qc_skip"))}, ensure_ascii=False), encoding="utf-8")
    if qc_issues:
        raise QCRepairNeeded(
            qc_issues,
            f"QC 发现 {len(qc_issues)} 处异常（黑帧/冻结/静音），进入质检修复流程",
        )

    # 5) loudnorm 两遍法 -14 LUFS（不混 BGM——BGM 由模板 <Audio> 混）
    _progress(88)
    output = task_dir / "output.mp4"
    final = _normalize_loudness_two_pass(raw_output, output)
    if final != output:
        shutil.copyfile(final, output)
    if raw_output.exists() and raw_output != output:
        raw_output.unlink(missing_ok=True)

    # 6) 缩略图
    _progress(94)
    _extract_thumbnail(output, task_dir)
    logger.info("remotion compose done: %s (%d bytes)", output, output.stat().st_size)
    return output


def _render_still(task_dir: Path, payload: dict, progress_cb=None) -> Path:  # noqa: ANN001
    """T7 静帧预览：单场景 props 渲一帧 PNG（frame 默认 30——转场模式无入场动画，
    帧内容即时静止）。输出 = task_dir / payload["still_output"]（相对路径）。

    素材未生成/缺失由模板 VisualPlaceholder 占位渲染（不炸）；结果不经 QC/
    loudnorm（预览非交付物）。秒级任务——progress 只报两端。
    """
    props = payload.get("remotion_props")
    if not isinstance(props, dict) or not props.get("scenes"):
        raise RuntimeError("still payload 缺少 remotion_props.scenes")
    # 产物与素材都归属**源任务**目录（缓存命中在源任务 GET 端点读取；帧素材
    # visual/audio 相对路径也是源任务相对——修前落 still 任务自身目录，GET 永远
    # 找不到文件 + 素材全部降级占位）。回退 task_dir 兼容无 source 的调用。
    # C2（2026-10-05 审计 §4-9）：resolve 后包含断言——source_task_dir 必须位于
    # VIDEO_GEN_STORAGE_DIR 根内，still_output 不得逃逸 assets_root。
    storage_root = Path(os.environ.get("VIDEO_GEN_STORAGE_DIR", "/app/backend/.deer-flow/video-gen")).resolve()
    assets_root = Path(payload.get("source_task_dir") or task_dir).resolve()
    if not assets_root.is_relative_to(storage_root):
        raise RuntimeError(f"still source_task_dir 不在 storage 根内: {assets_root} (root={storage_root})")
    out_rel = payload.get("still_output") or "still_preview/preview.png"
    out = (assets_root / out_rel).resolve()
    if not out.is_relative_to(assets_root):
        raise RuntimeError(f"still_output 逃逸任务目录: {out_rel!r}")
    out.parent.mkdir(parents=True, exist_ok=True)
    frame_no = int(payload.get("still_frame", 30))

    def _progress(pct: int) -> None:
        if progress_cb:
            try:
                progress_cb(pct)
            except Exception:
                pass

    # 单场景 props 已是 gateway 构建好的最小集；本地化（上传素材/远端 URL）
    props, degradations = _localize_props(dict(props), assets_root)
    if degradations:
        logger.info("still localization degradations: %s", degradations)
    props_file = assets_root / "still_props.json"
    props_file.write_text(json.dumps(props, ensure_ascii=False), encoding="utf-8")

    _progress(10)
    try:
        with _staged_public_dir(assets_root) as public_dir:
            cmd = [
                "npx", "remotion", "still", "src/index.ts", "DataDrivenCourse", str(out),
                f"--props={props_file}",
                f"--public-dir={public_dir}",
                *( [f"--browser-executable={CHROME_PATH}"] if CHROME_PATH else [] ),
                f"--frame={frame_no}",
                "--log=error",
            ]
            proc = subprocess.run(cmd, cwd=TEMPLATE_DIR, capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("静帧预览渲染超时（300s）") from e
    if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        tail = (proc.stderr or "")[-1500:]
        raise RuntimeError(f"静帧预览渲染失败 (rc={proc.returncode}): {tail}")
    _progress(95)
    logger.info("still preview done: %s (%d bytes)", out, out.stat().st_size)
    return out
