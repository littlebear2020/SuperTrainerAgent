"""Worker pipeline: compose AI visual video from generated images/video clips.

Pipeline function signature (matches framework §5.2):
    fn(task_dir: Path, payload: dict, options: dict, progress_cb: Callable[[int], None]) -> Path

Supports two payload shapes:
  - NEW (storyboard): frames[{visual_url, visual_type, duration}], subtitle_path, audio_concat_path
  - LEGACY: shots[{url, duration, type: "image"|"video"}], audio{audio_path}
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import time
import urllib.request

from pipelines import net_guard

opener = net_guard.build_guarded_opener()  # 30x 每跳守卫（评审修复轮）
from pathlib import Path

from pipelines._degradation_sidecar import KIND_BGM_FAILED, record_degradation
from pipelines._placeholder_sidecar import write_placeholder_sidecar
from pipelines._ffmpeg_helpers import (
    _ZOOM_OVERSAMPLE as _helpers_zoom_oversample,
)
from pipelines._ffmpeg_helpers import (
    _apply_colorgrade_and_subtitles,
    _apply_intro_outro_fade,
    _build_zoompan_vf,
    _dynamic_concat_timeout,
    _ffmpeg_concat,
    _ffmpeg_concat_with_transitions,
    _ffmpeg_convert_format,
    _ffmpeg_err,
    _ffmpeg_extract_thumbnail,
    _ffmpeg_extend_clip_to_duration,
    _ffmpeg_mix_audio,
    _ffmpeg_mix_bgm,
    _ffmpeg_mix_bgm_with_ducking,
    normalize_clip_resolution,
    _finalize_output,
    _normalize_loudness,
    _probe_black_scenes,
    _probe_duration,
    _write_av_drift_sidecar,
)

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 120
_DOWNLOAD_RETRY_DELAYS = [0, 1, 2]  # E1: 秒；下载无效内容重试间隔

# 2026-08-03 Task 11：_ZOOM_OVERSAMPLE 与 _build_zoompan_vf 已搬到 _ffmpeg_helpers
# （mixed/segmented 也要用，复制会让「crop 路线已被 ffmpeg 8.1.2 证伪」这类实测结论
# 分叉）。此处保留同名 re-export，既有 import 与测试零改动。
_ZOOM_OVERSAMPLE = _helpers_zoom_oversample


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download_asset(url: str, dest: Path) -> None:
    """下载素材到 dest。E1: 加 User-Agent + 重试 + 类型感知内容校验。

    原裸 urllib 无 UA 无重试无校验，CDN 返回 200+无效内容时静默写入损坏文件喂给
    ffmpeg。现校验后无效则删文件重试，耗尽 raise（让调用方 E2 走占位，不静默）。
    校验类型感知（评审修正）：仅 .mp4 目标做 ffprobe 时长校验（>0 判有效）；
    图片（.png 等）ffprobe 无时长概念（_probe_duration 恒返 0），只做最小字节校验，
    否则会误杀全部 ai_image 素材。
    """
    logger.info("Downloading asset: %s -> %s", url[:80], dest.name)
    net_guard.assert_public_http_url(url)  # C2: 私网/白名单拒绝（连接前）
    headers = {"User-Agent": "TrainerAI-Worker/1.0"}
    last_err = None
    for delay in _DOWNLOAD_RETRY_DELAYS:
        try:
            req = urllib.request.Request(url, headers=headers)
            limit = net_guard.max_asset_bytes()
            with opener.open(req, timeout=_DOWNLOAD_TIMEOUT) as resp:
                with open(dest, "wb") as f:
                    copied = 0
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > limit:
                            raise ValueError(f"download exceeds REMOTION_ASSET_MAX_BYTES ({limit}B)")
                        f.write(chunk)
            if dest.suffix == ".mp4":
                if _probe_duration(dest) > 0:
                    return
                last_err = "downloaded file not a valid video (ffprobe duration=0)"
            else:
                # 图片：最小字节校验（错误页/空响应通常 <512B）
                if dest.stat().st_size >= 512:
                    return
                last_err = f"downloaded image too small ({dest.stat().st_size} bytes)"
            dest.unlink(missing_ok=True)
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
            dest.unlink(missing_ok=True)
        if delay:
            time.sleep(delay)
    raise RuntimeError(f"download failed after {len(_DOWNLOAD_RETRY_DELAYS)} retries: {last_err}")


# ---------------------------------------------------------------------------
# Image-to-video conversion
# ---------------------------------------------------------------------------


def _pick_transition(frame_index: int, scene_group_starts: set[int], base: str = "none") -> str:
    """Task 3d: 段落级转场——scene_group 边界用 fade（强转场），段内用 base（柔切）。

    scene_groups 已在 payload（build_worker_payload），worker 消费它区分转场。
    """
    return "fade" if frame_index in scene_group_starts else base


def _image_to_video(image_path: Path, duration: float, output: Path, width: int = 1280, height: int = 720, quality: str = "standard", frame_index: int = 0) -> None:
    """Convert a still image to a video clip with gentle zoom (Ken Burns).

    Oversamples the input to 4x target (_ZOOM_OVERSAMPLE) BEFORE zoompan. zoompan's
    x/y are integer-pixel sampled, so as `zoom` changes each frame the computed
    `iw/2-(iw/zoom/2)` floats round to different integers → frame-to-frame jitter.
    At 4x the ±1px canvas jitter downsamples to ≤0.25 output pixels (was 2x →
    0.5px, still visible). Segments render natively at 30fps (was 25 → resample
    judder against CONCAT_TARGET_FPS=30).
    Also respects the caller's aspect ratio (was hardcoded 1280x720, which
    squashed 9:16 portraits).

    quality drives the per-frame encode (CRF/preset from QUALITY_PRESETS) so
    draft renders use ultrafast at the segment stage too (not just at concat).
    """
    from pipelines._ffmpeg_helpers import _get_quality_args

    vf = _build_zoompan_vf(width=width, height=height, duration=duration, frame_index=frame_index)

    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", str(image_path),
        "-vf", vf,
        *_get_quality_args(quality),
        "-pix_fmt", "yuv420p",
        "-t", str(duration),
        str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "image-to-video conversion failed"))


# ---------------------------------------------------------------------------
# Payload helpers
# ---------------------------------------------------------------------------


def _build_black_clip_vf(text: str, *, width: int, height: int) -> str:
    """构造黑场占位片的 drawtext 滤镜串。

    2026-07-29：补 fontfile= —— 缺它时 ffmpeg 用内建 Latin-only 默认字体，
    中文渲染成方块（线上实测：过期 URL 降级黑场后「画面中间各种方块乱码」）。
    无字体可用时退回不带 fontfile（英文仍可读），不抛异常。
    """
    from ._ffmpeg_helpers import _resolve_cjk_fontfile

    safe = (text or "画面生成中")[:40].replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\;")
    fontsize = max(height // 20, 16)
    font = _resolve_cjk_fontfile()
    font_arg = f"fontfile='{font}':" if font else ""
    return (
        f"drawtext=text='{safe}':{font_arg}"
        f"fontcolor=white:fontsize={fontsize}:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:box=1:boxcolor=black@0.5"
    )


def _make_black_subtitle_clip(
    output: Path, duration: float, text: str, width: int, height: int
) -> None:
    """P0-4 layer 3: 生成黑场 + 旁白字幕占位片段（失败帧兜底，非纯黑）。

    复用帧/失败帧补齐音画等长——画面黑底 + 居中旁白文案（半透明底衬），
    避免纯黑无声色块。2026-07-29：vf 构造抽到 _build_black_clip_vf 并补 CJK fontfile。
    """
    vf = _build_black_clip_vf(text, width=width, height=height)
    cmd = [
        "ffmpeg", "-y", "-f", "lavfi", "-i",
        f"color=c=black:s={width}x{height}:d={max(duration, 0.1)}:r=30",
        "-vf", vf, "-pix_fmt", "yuv420p", str(output),
    ]
    proc = subprocess.run(cmd, capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(_ffmpeg_err(proc.stderr, "black subtitle clip failed"))


def _collect_visual_assets(payload: dict) -> list[dict]:
    """Extract visual assets from storyboard frames (new payload shape).

    返回 list of {frame_index, url, local_path, type, duration, placeholder, narration}。
    P0-4 layer 3：既无 url 也无 local_path 的失败帧不再 continue 跳过——返回
    placeholder=True 占位 asset，保证 concat 后视频总时长 = 音频总时长（修后半无画面）。
    """
    assets: list[dict] = []
    for idx, frame in enumerate(payload.get("frames", [])):
        url = frame.get("visual_url")
        local_path = frame.get("local_visual_path")
        duration = frame.get("duration", 5.0)
        narration = frame.get("narration", "")
        if url or local_path:
            raw_type = frame.get("visual_type", "image")
            # image_reuse 已废弃（BUG 1-B 移除了 layer-2 冻结兜底，不再产生新 image_reuse 帧）。
            # 保留归一化作为防御：历史 storyboard.json 可能残留 image_reuse 标记，
            # 不归一会 fall-through 各分支的 image/video 判断（local 分支返回原始 png 被
            # move 成 seg_NNN.mp4 -> concat 崩 + 源文件被移走销毁）。
            atype = "image" if raw_type == "image_reuse" else raw_type
            assets.append({
                "frame_index": idx,
                "url": url,
                "local_path": local_path,
                "type": atype,
                "duration": duration,
                "placeholder": False,
                "narration": narration,
            })
        else:
            # P0-4 layer 3: 失败帧占位（黑场+旁白字幕由 concat 段生成），不丢帧
            assets.append({
                "frame_index": idx,
                "url": None,
                "local_path": None,
                "type": "placeholder",
                "duration": duration,
                "placeholder": True,
                "narration": narration,
            })
    return assets


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------


def _prepare_local_segment(
    asset: dict,
    task_dir: Path,
    tmp_dir: Path,
    width: int,
    height: int,
    quality: str,
    config: dict | None = None,
    lease_remaining: float | None = None,
) -> Path | None:
    """本地素材（用户上传/D1 本地化/占位）→ segment-ready 文件；不存在返 None。

    P1-1（F3 修复）：图片必须走 _image_to_video（Ken Burns）——旧版对 image 直接
    copy2 成 seg_NNN.mp4，是「上传图片替换帧 → 合成崩/坏片」的根因。
    视频维持既有 D2 规则：非用户上传且短于 duration → extend 补长 + 分辨率归一；
    用户上传 video（frame_ 前缀）不 extend，仅归一。
    """
    local_path = asset.get("local_path")
    if not local_path:
        return None
    full_path = task_dir / local_path
    if not full_path.exists():
        return None
    seg_idx = asset["frame_index"]
    duration = asset["duration"]

    if asset["type"] == "image":
        seg = tmp_dir / f"local_{seg_idx:03d}.mp4"
        _image_to_video(full_path, duration, seg, width, height, quality, frame_index=seg_idx)
        return seg

    if asset["type"] != "video":
        # R1: 显式拒绝未知类型（image_reuse 已由 _collect_visual_assets 归一为 image）——
        # 不得 fall-through 返回原始文件被 move 成 seg_NNN.mp4。
        raise ValueError(f"unsupported local asset type: {asset['type']}")

    src = full_path
    clip_dur = _probe_duration(src)
    # Task 4/B3: 删除 _is_user_uploaded_visual 的补长排除。原设计怕「循环补齐改变
    # 观感」——那个顾虑针对 -stream_loop；改成 tpad 定格末帧后不再成立，而短于帧
    # 时长的上传片不补会让其后所有帧相对旁白整体错位且误差累积。
    if clip_dur > 0 and clip_dur + 0.2 < duration:
        extended = tmp_dir / f"local_{seg_idx:03d}_ext.mp4"
        src = _ffmpeg_extend_clip_to_duration(src, duration, extended, config=config, frame_index=seg_idx, lease_remaining=lease_remaining)
    normalized = tmp_dir / f"local_{seg_idx:03d}_norm.mp4"
    # H5: max_duration=duration——上传/本地化长 clip 截到帧时长（短 clip 不补长是 D2 设计）。
    src = normalize_clip_resolution(Path(str(src)), normalized, width, height, max_duration=duration)
    return Path(str(src))


def _persist_local_visual(task_dir: Path, frame_index: int, rel_path: str) -> None:
    """把 worker 下载得到的本地相对路径写回 storyboard.json 的 local_visual_path。

    2026-07-29：修前 worker 下载到 task_dir/source/ 然后在 finally 里
    rmtree(source_dir)，且全仓零 storyboard.json 写入点——下载结果无处留存，
    每次重渲都得重新依赖远程 URL；TOS 签名 24h 后过期即全片黑场。
    服务器实测 ai_image 任务 sidecar 指向文件存在数全为 0（出生即悬空）。

    只改 local_visual_path 单字段（read-merge-write + 原子替换），
    与 gateway 的 _update_storyboard 同模式。worker 持 lease 期间 gateway 只读，
    不存在并发写；任何失败都静默跳过——写回是优化，不得成为渲染的新失败源。
    """
    sb_path = task_dir / "storyboard.json"
    if not sb_path.exists():
        return
    try:
        data = json.loads(sb_path.read_text(encoding="utf-8"))
        frames = data.get("frames") or []
        target = next((f for f in frames if isinstance(f, dict) and f.get("index") == frame_index), None)
        if target is None:
            return
        if target.get("local_visual_path") == rel_path:
            return
        target["local_visual_path"] = rel_path
        tmp = sb_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(sb_path)
    except Exception:  # noqa: BLE001
        logger.warning("persist local_visual_path failed (frame %d)", frame_index, exc_info=True)


# Task 10: writer 提到共享模块 _placeholder_sidecar，v2/v3 复用同一实现。
# 保留本名字作为薄委托——既有调用点与测试（test_placeholder_sidecar.py）不变。
def _write_placeholder_sidecar(task_dir: Path, indices: list[int], total: int, *, touched: list[int] | None = None) -> None:
    """委托到共享 writer（语义不变，见 _placeholder_sidecar.write_placeholder_sidecar）。"""
    write_placeholder_sidecar(task_dir, indices, total, touched=touched)


def _download_and_prepare(
    asset: dict,
    source_dir: Path,
    tmp_dir: Path,
    width: int,
    height: int,
    quality: str,
    config: dict | None = None,
    lease_remaining: float | None = None,
) -> Path:
    """Download an asset and convert it to segment-ready media.

    image → ``_image_to_video`` (tmp mp4); video → extend clips shorter than
    the declared duration (HR-T2). Extracted so the full-render and incremental
    branches share one code path — the incremental branch previously skipped
    the extend, desyncing audio on single-frame recompose (P1-9).

    Returns the local path the caller should move into ``segments_dir``.
    """
    # B2(轮3): source_dir 自建仓——全量分支在 compose 开头建过，但 finally 会删
    # （非增量时），后续增量 recompose 再进这里时目录不存在 → 下载失败 → 误走占位。
    source_dir.mkdir(parents=True, exist_ok=True)
    shot_type = asset["type"]
    duration = asset["duration"]
    seg_idx = asset["frame_index"]
    ext = ".mp4" if shot_type == "video" else ".png"
    local = source_dir / f"shot_{seg_idx:03d}{ext}"
    _download_asset(asset["url"], local)
    if shot_type == "image":
        seg = tmp_dir / f"shot_{seg_idx:03d}.mp4"
        _image_to_video(local, duration, seg, width, height, quality, frame_index=seg_idx)
        return seg
    # HR-T2: ai_video clips are capped at 8s; loop-align to the frame duration.
    clip_dur = _probe_duration(local)
    if clip_dur > 0 and clip_dur + 0.2 < duration:
        extended = tmp_dir / f"shot_{seg_idx:03d}_ext.mp4"
        return _ffmpeg_extend_clip_to_duration(local, duration, extended, config=config, frame_index=seg_idx, lease_remaining=lease_remaining)
    return local


def compose_ai_visual(
    task_dir: Path,
    payload: dict,
    options: dict,
    progress_cb,
    *,
    lease_remaining: float | None = None,
) -> Path:
    # lease_remaining: 渲染期剩余租约秒数（worker 传入）——minterpolate 等阻塞
    # subprocess 的 timeout 核算用（不变量 ≤ 租约−300）。pipelines 禁读 env，
    # 只经调用链透传。
    """Compose AI visual video.

    Supports both new (storyboard) and legacy payload shapes.
    """
    frames = payload.get("frames")
    subtitle_path = payload.get("subtitle_path")
    audio_concat_path = payload.get("audio_concat_path")
    config = payload.get("config") or {}
    subtitle_style = config.get("subtitle_style", "default")
    transition = config.get("transition", "none")
    transition_duration = config.get("transition_duration", 0.5)
    # Width/height from storyboard config (build_worker_payload asdict's StoryboardConfig).
    # Drives the image-to-video output size + aspect ratio (was hardcoded 1280x720).
    width = int(config.get("width", 1280))
    height = int(config.get("height", 720))
    quality = payload.get("quality", "standard")

    # Detect new storyboard payload by presence of the "frames" key.
    # Do NOT inspect frames[0].visual_url — if the first frame's image
    # generation failed (visual_url=None) but later frames succeeded, that
    # check would wrongly route a valid new payload to the legacy branch.
    is_new_payload = isinstance(frames, list) and len(frames) > 0

    tmp_dir = task_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    source_dir = task_dir / "source"
    local_sources: dict[int, str] = {}
    # 2026-07-29：登记降级为黑场占位的帧。增量与全渲分支共用，故在两分支之前初始化。
    placeholder_indices: list[int] = []

    # incremental 必须在 try 之前绑定：finally 里要用它决定是否 rmtree(source_dir)，
    # 而 try 的第一句 progress_cb(5) 现在会读写 DB（高水位钳制），它一旦抛异常，
    # finally 会因 NameError 掩盖掉真正的原始错误。
    incremental = payload.get("incremental", False)
    changed_frames = payload.get("changed_frames", [])
    # 下面两个只在 new-payload 分支里被赋值，但成品前的 sidecar 写入要用它们
    # （legacy shots 分支走不到增量），先给安全默认值防 NameError。
    use_incremental = False
    valid_changed: list[int] = []

    try:
        progress_cb(5)

        if is_new_payload:
            # --- NEW storyboard payload ---
            assets = _collect_visual_assets(payload)
            if not assets:
                raise ValueError("No visual assets found in storyboard frames")

            segments_dir = task_dir / "segments"

            # P1-3 防护：缓存 segment 数 ≠ 帧数（增删帧/上次合成中断）或 changed_frames
            # 为空/索引越界 → 回退全量，防 stale segment 复用出鬼片。
            # M5 补强：①文件名集合必须恰为 {seg_000..seg_{N-1}}（数量巧合一致但错位也拦）；
            # ②每个文件 >1024 字节（0 字节/截断写入=损坏，喂 concat 必崩且无回退）。
            cached_segments = sorted(segments_dir.glob("seg_*.mp4")) if segments_dir.exists() else []
            valid_changed = [i for i in changed_frames if 0 <= i < len(assets)]
            expected_names = {f"seg_{i:03d}.mp4" for i in range(len(assets))}
            cached_ok = (
                bool(cached_segments)
                and {p.name for p in cached_segments} == expected_names
                and all(p.stat().st_size > 1024 for p in cached_segments)
            )
            use_incremental = incremental and bool(valid_changed) and cached_ok

            if use_incremental:
                # Incremental: only re-download and convert changed frames
                # 按 frame_index 索引 assets（修 assets[idx] 错位：idx 是原帧序，
                # assets 是过滤后列表，原版直接 assets[idx] 会取到错位帧）
                assets_by_idx = {a["frame_index"]: a for a in assets}
                for _n, idx in enumerate(valid_changed):
                    # 每帧上报进度：本循环每帧要下载 + 转码，帧多时耗时可观。
                    # 修前增量分支从 progress_cb(5) 到 progress_cb(70) 之间零上报 →
                    # updated_at 全程冻结 → reaper 谓词 A（updated_at 超时没动）
                    # 把正在正常渲染的任务判成 stalled。全渲分支本就逐帧上报，
                    # 增量漏了这层是不对称——与 worker 侧高水位钳制修复同一目标。
                    # 增量只重渲部分帧，故进度按 5→65 线性铺（与全渲 5→60 同量级）。
                    progress_cb(5 + int(_n / max(len(valid_changed), 1) * 60))
                    asset = assets_by_idx.get(idx)
                    if asset is None:
                        continue  # 该帧无视觉素材（跳过）
                    # P0-4 layer 3: placeholder 帧 → 黑场 + 旁白字幕
                    if asset.get("placeholder"):
                        seg = segments_dir / f"seg_{idx:03d}.mp4"
                        _make_black_subtitle_clip(
                            seg, asset["duration"], asset.get("narration", ""), width, height
                        )
                        placeholder_indices.append(idx)
                        continue
                    local_path = asset.get("local_path")
                    # local 上传/占位优先（不再读 frames[idx]，消除索引错位）
                    if local_path:
                        prepared = _prepare_local_segment(asset, task_dir, tmp_dir, width, height, quality, config=config, lease_remaining=lease_remaining)
                        if prepared is not None:
                            seg = segments_dir / f"seg_{idx:03d}.mp4"
                            shutil.move(str(prepared), str(seg))
                            continue
                    # P1-9: 与全渲分支共用 _download_and_prepare（含 HR-T2 短 clip 补齐），
                    # 消除增量路径漏 extend 的不对称（旧增量路径短 ai_video 会音画不同步）。
                    try:
                        prepared = _download_and_prepare(asset, source_dir, tmp_dir, width, height, quality, lease_remaining=lease_remaining)
                    except Exception as dl_err:
                        # E2: 增量路径下载失败同样走黑场+旁白占位
                        logger.warning("incremental download failed for frame %d, using placeholder: %s", idx, dl_err)
                        seg = segments_dir / f"seg_{idx:03d}.mp4"
                        _make_black_subtitle_clip(seg, asset["duration"], asset.get("narration", ""), width, height)
                        placeholder_indices.append(idx)
                        continue
                    # B2(轮3): video 增量段也要归一分辨率——_download_and_prepare 本身不含
                    # normalize，增量漏了与全量分支（Task 3h）不对称 → 拼接拉伸/流不兼容。
                    # H5: + max_duration 截断长 clip
                    if asset["type"] == "video":
                        normalized = tmp_dir / f"shot_{idx:03d}_norm.mp4"
                        prepared = normalize_clip_resolution(Path(str(prepared)), normalized, width, height, max_duration=asset["duration"])
                    final_seg = segments_dir / f"seg_{idx:03d}.mp4"
                    shutil.move(str(prepared), str(final_seg))
                    # 增量分支不写 local_sources（直接 move 后就走下一帧），直接写回 storyboard：
                    # 增量是 recompose 主路径，漏了这行 recompose 之后下次渲染仍找不到本地副本。
                    # 只对 video 帧写回：seg_NNN.mp4 是 mp4，而 image 帧的 visual_type 仍是
                    # "image"，下次渲染 _prepare_local_segment 会拿它走 _image_to_video
                    # （ffmpeg -loop 1 -i x.mp4 → "Option loop not found" 直接抛），
                    # 且该调用在占位兜底 try 之外 → 整任务 failed + segments/ 被清空。
                    if asset["type"] == "video":
                        _persist_local_visual(task_dir, idx, f"segments/seg_{idx:03d}.mp4")
                    elif asset["type"] == "image":
                        # image 帧的下载产物是 source/shot_NNN.png（segments/ 里那份是转码后的
                        # mp4，不能记给 image 帧——记了下次渲染会拿 mp4 走 _image_to_video 而崩）。
                        # 但**不能直接记 source/**：增量虽不 rmtree source_dir，下一次**全量**
                        # 渲染的 finally 会删它 → local_visual_path 变悬空，正是 Task 3 要根治的
                        # 那个 bug。故复制到 visuals/dl_NNNN.png（与全渲分支同落点、同存活性）再记录。
                        # 不补这段的话，增量路径下次渲染仍只能依赖远程 URL（24h 后过期即黑场）。
                        _shot_png = source_dir / f"shot_{idx:03d}.png"
                        if _shot_png.exists():
                            try:
                                _visuals_dir = task_dir / "visuals"
                                _visuals_dir.mkdir(parents=True, exist_ok=True)
                                _rel_dl = f"visuals/dl_{idx:04d}.png"
                                shutil.copy2(str(_shot_png), str(task_dir / _rel_dl))
                                _persist_local_visual(task_dir, idx, _rel_dl)
                            except OSError:
                                # 留存是优化，不得成为渲染的新失败源
                                logger.warning("persist incremental image failed (frame %d)", idx, exc_info=True)
                segments = sorted(segments_dir.glob("seg_*.mp4"))
            else:
                # Full render
                source_dir.mkdir(parents=True, exist_ok=True)
                segments_dir.mkdir(parents=True, exist_ok=True)
                segments = []
                for i, asset in enumerate(assets):
                    url = asset["url"]
                    local_path = asset.get("local_path")
                    shot_type = asset["type"]
                    duration = asset["duration"]
                    seg_idx = asset["frame_index"]  # 原帧序，保 concat 顺序 + 增量复渲对齐
                    # P0-4 layer 3: placeholder 帧（无 url 无 local_path）→ 黑场 + 旁白字幕
                    if asset.get("placeholder"):
                        seg = tmp_dir / f"seg_{seg_idx:03d}.mp4"
                        _make_black_subtitle_clip(
                            seg, duration, asset.get("narration", ""), width, height
                        )
                        final_seg = segments_dir / f"seg_{seg_idx:03d}.mp4"
                        shutil.move(str(seg), str(final_seg))
                        segments.append(final_seg)
                        placeholder_indices.append(seg_idx)
                        pct = 5 + int((i + 1) / max(len(assets), 1) * 55)
                        progress_cb(pct)
                        continue
                    # local 上传/占位优先（不再读 frames[i]，消除索引错位）
                    if local_path:
                        prepared = _prepare_local_segment(asset, task_dir, tmp_dir, width, height, quality, config=config, lease_remaining=lease_remaining)
                        if prepared is not None:
                            seg = segments_dir / f"seg_{seg_idx:03d}.mp4"
                            shutil.move(str(prepared), str(seg))
                            segments.append(seg)
                            local_sources[seg_idx] = local_path
                            continue

                    # E2（粒度统一）: 下载 + 后续处理（image_to_video/extend/normalize）任一失败
                    # 都走黑场+旁白占位，不挂整体 compose（与 incremental 分支 _download_and_prepare
                    # 的保护粒度对齐，修前 full render 仅包下载、处理阶段失败仍整体崩溃）。
                    try:
                        if shot_type == "image":
                            # 2026-07-29：落 visuals/ 而非 source/——finally 会 rmtree(source_dir)，
                            # 落 source/ 等于下载完就删，每次重渲都得重下远程 URL（24h 后必过期黑场）。
                            # dl_ 前缀区别于 img_（gateway 本地化）与 frame_（用户上传）。
                            visuals_dir = task_dir / "visuals"
                            visuals_dir.mkdir(parents=True, exist_ok=True)
                            rel_local = f"visuals/dl_{seg_idx:04d}.png"
                            local = task_dir / rel_local
                            _download_asset(url, local)
                            seg = tmp_dir / f"seg_{seg_idx:03d}.mp4"
                            _image_to_video(local, duration, seg, width, height, quality, frame_index=seg_idx)
                            final_seg = segments_dir / f"seg_{seg_idx:03d}.mp4"
                            shutil.move(str(seg), str(final_seg))
                            segments.append(final_seg)
                            local_sources[seg_idx] = rel_local
                            # 写回 storyboard——下次渲染/预览直接命中本地，不再依赖远程 URL
                            _persist_local_visual(task_dir, seg_idx, rel_local)
                        else:
                            # 复用 _download_and_prepare（含下载 + extend 补齐），与 incremental 同路径
                            prepared = _download_and_prepare(asset, source_dir, tmp_dir, width, height, quality, config=config, lease_remaining=lease_remaining)
                            # Task 3h: ai_video clip 归一到目标分辨率（防拼接拉伸）
                            # H5: + max_duration 截断长 clip
                            normalized = tmp_dir / f"shot_{seg_idx:03d}_norm.mp4"
                            prepared = normalize_clip_resolution(Path(str(prepared)), normalized, width, height, max_duration=duration)
                            final_seg = segments_dir / f"seg_{seg_idx:03d}.mp4"
                            shutil.move(str(prepared), str(final_seg))
                            segments.append(final_seg)
                            local_sources[seg_idx] = f"segments/seg_{seg_idx:03d}.mp4"
                            # segments/ 本就不被 rmtree，但没写进 storyboard 时 gateway 只能靠
                            # sidecar 兜底；写回后 local_visual_path 第一顺位命中，预览更稳。
                            # 仅 video 帧写回：这里落的是 mp4，若帧的 visual_type 是 image，
                            # 下次渲染会拿 mp4 去走 _image_to_video（-loop 1 -i x.mp4 必崩，
                            # 且在占位兜底 try 之外 → 整任务 failed）。本 else 已排除 image，
                            # 显式再判一次是防 shot_type 出现第三种取值时静默踩坑。
                            if shot_type == "video":
                                _persist_local_visual(task_dir, seg_idx, f"segments/seg_{seg_idx:03d}.mp4")
                    except Exception as dl_err:
                        # E2: 下载/处理失败 → 黑场+旁白占位（不静默黑帧、不挂 compose）
                        logger.warning("download/prepare failed for frame %d, using placeholder: %s", seg_idx, dl_err)
                        seg = tmp_dir / f"seg_{seg_idx:03d}.mp4"
                        _make_black_subtitle_clip(seg, duration, asset.get("narration", ""), width, height)
                        final_seg = segments_dir / f"seg_{seg_idx:03d}.mp4"
                        shutil.move(str(seg), str(final_seg))
                        segments.append(final_seg)
                        placeholder_indices.append(seg_idx)

                    pct = 5 + int((i + 1) / max(len(assets), 1) * 55)
                    progress_cb(pct)
        else:
            # --- LEGACY payload ---
            shots = payload.get("shots", [])
            segments = []
            source_dir.mkdir(parents=True, exist_ok=True)
            for i, shot in enumerate(shots):
                url = shot.get("url", "")
                shot_type = shot.get("type", "image")
                duration = shot.get("duration", 5.0)
                if not url:
                    continue
                ext = ".mp4" if shot_type == "video" else ".png"
                local = source_dir / f"shot_{i:03d}{ext}"
                _download_asset(url, local)
                if shot_type == "image":
                    seg = tmp_dir / f"seg_{i:03d}.mp4"
                    _image_to_video(local, duration, seg, width, height, quality, frame_index=i)
                    segments.append(seg)
                else:
                    segments.append(local)
                pct = 5 + int((i + 1) / max(len(shots), 1) * 60)
                progress_cb(pct)

        if not segments:
            raise ValueError("No segments were processed successfully")

        # 2026-07-29：占位帧也 append 进 segments，上面这条守卫对「全帧黑场」完全放行。
        # 写 sidecar 让 gateway 能把降级如实告知用户，而不是 status=ready 一片黑。
        # 增量渲染只碰改动帧，故传 touched 做逐帧合并——否则重渲 1 帧就会把其余帧
        # 仍是黑场的事实抹掉，报成「全部正常」。
        _write_placeholder_sidecar(
            task_dir,
            placeholder_indices,
            len(assets) if is_new_payload else len(segments),
            touched=valid_changed if use_incremental else None,
        )
        if placeholder_indices:
            logger.warning(
                "compose finished with %d placeholder (black) frames: %s",
                len(placeholder_indices),
                placeholder_indices[:20],
            )
        progress_cb(70)

        if local_sources:
            try:
                (task_dir / "local_sources.json").write_text(
                    json.dumps({str(k): v for k, v in local_sources.items()}, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                logger.warning("Failed to write local_sources.json sidecar")

        # Concatenate
        concat_path = tmp_dir / "concat.mp4"
        n_segs = len(segments)
        # H6: 尊重 payload 的 transition——删除旧的 scene_group auto-fade 覆盖。
        # gateway W1（service.py 强制 ai_image/ai_video transition=none）的意图是防
        # xfade 重叠致视频<音频（音画漂移，vg_93e83a7ee259 实证）；旧覆盖在多 scene_group
        # 任务上重新强加 fade，recompose（恒走 v1）后成片多出原片没有的转场 → 字幕提前。
        effective_transition = transition
        has_transitions = effective_transition != "none" and n_segs > 1
        # Task 18：超时按总时长而非镜数——重编码回落的耗时随片长线性增长，
        # 旧公式给 54 镜 8 分钟片只有 354s（vg_66978a4b06ba 实测超时）。
        total_secs = sum(float(f.get("duration", 0) or 0) for f in frames) if isinstance(frames, list) else 0.0
        concat_timeout = _dynamic_concat_timeout(n_segs, has_transitions, total_seconds=total_secs)
        if has_transitions:
            _ffmpeg_concat_with_transitions(segments, concat_path, transition=effective_transition, transition_duration=transition_duration, quality=quality, timeout=concat_timeout)
        else:
            _ffmpeg_concat(segments, concat_path, quality=quality, timeout=concat_timeout)
        progress_cb(85)

        # G2: concat 后扫描黑场段（诊断"分镜之间黑屏"，不 trim）。统一帧率（G1）后应消失；
        # 若仍在，多为 provider 首尾黑帧，留 sidecar 供后续决策是否自动去黑。
        try:
            black_scenes = _probe_black_scenes(concat_path)
            if black_scenes:
                logger.warning(
                    "concat 输出含 %d 段黑场（衔接黑场/provider 首尾黑帧嫌疑）: %s",
                    len(black_scenes), black_scenes[:5],
                )
                (task_dir / "concat_black_scenes.json").write_text(
                    json.dumps(black_scenes), encoding="utf-8"
                )
        except Exception:
            logger.debug("black scene probe failed", exc_info=True)

        # Subtitles (new payload has pre-made SRT) + colorgrade — unified finalizer
        current = concat_path
        color_palette = payload.get("config", {}).get("color_palette") if payload.get("config") else None
        current = _apply_colorgrade_and_subtitles(
            current,
            task_dir=task_dir,
            tmp_dir=tmp_dir,
            color_palette=color_palette,
            subtitle_path=subtitle_path,
            subtitle_style=subtitle_style,
            subtitle_ass=payload.get("subtitle_ass"),
        )

        # H6: 与 mixed/segmented 对齐——显式转场时写 av_drift sidecar（xfade 重叠致
        # 视频<音频时 gateway 可回传诚实提示；修复前 v1 无 sidecar，漂移完全不可见）。
        if has_transitions and audio_concat_path and Path(audio_concat_path).exists():
            _write_av_drift_sidecar(current, Path(audio_concat_path), task_dir, transition_count=n_segs - 1)

        # Audio mix
        if audio_concat_path and Path(audio_concat_path).exists():
            mixed = tmp_dir / "with_audio.mp4"
            _ffmpeg_mix_audio(current, Path(audio_concat_path), mixed)
            current = mixed
        elif not audio_concat_path:
            # Legacy: audio.audio_path
            audio_info = payload.get("audio", {})
            audio_path_str = audio_info.get("audio_path", "")
            if audio_path_str:
                audio_path = Path(audio_path_str)
                if audio_path.exists():
                    mixed = tmp_dir / "with_audio.mp4"
                    _ffmpeg_mix_audio(current, audio_path, mixed)
                    current = mixed
        progress_cb(95)

        # Mix BGM
        bgm_path_str = payload.get("config", {}).get("bgm_path", "") if payload.get("config") else ""
        bgm_ducking = payload.get("config", {}).get("bgm_ducking", True) if payload.get("config") else True
        if bgm_path_str:
            bgm_path = Path(bgm_path_str)
            if bgm_path.exists():
                try:
                    bgm_out = tmp_dir / "with_bgm.mp4"
                    bgm_vol = payload.get("config", {}).get("bgm_volume", 0.15)
                    if bgm_ducking:
                        _ffmpeg_mix_bgm_with_ducking(current, bgm_path, bgm_out, bgm_volume=bgm_vol)
                    else:
                        _ffmpeg_mix_bgm(current, bgm_path, bgm_out, bgm_volume=bgm_vol)
                    current = bgm_out
                except Exception as exc:
                    # Task 23 / C11: 用户明确选了 BGM，成片里没有——修前只有这行
                    # warning（还漏了 exc_info），界面零说明。
                    logger.warning("BGM mix failed, continuing without BGM", exc_info=True)
                    record_degradation(
                        task_dir,
                        kind=KIND_BGM_FAILED,
                        detail=f"背景音乐混音失败，成片不含 BGM：{exc}",
                    )
            else:
                # 修前这个分支连 warning 都没有：选的 BGM 文件丢了/路径错了，
                # 表现与「压根没选 BGM」完全一致。
                logger.warning("BGM file not found, continuing without BGM: %s", bgm_path)
                record_degradation(
                    task_dir,
                    kind=KIND_BGM_FAILED,
                    detail="背景音乐文件不存在，成片不含 BGM",
                )

        # C3 (Task 8): 响度归一 —— 必须在全部 amix 之后（否则与 normalize=0 抵消）
        current = _normalize_loudness(current, tmp_dir / "loudnorm.mp4")

        # Task 3g: 片头淡入 + 片尾淡出 — shared finalizer
        current = _apply_intro_outro_fade(current, tmp_dir=tmp_dir, fade_sec=0.5, quality=quality)

        # Format conversion + final output
        output_format = payload.get("output_format", "mp4")
        quality = payload.get("quality", "standard")
        ext = f".{output_format}"
        final_raw = task_dir / f"output{ext}"
        result = _ffmpeg_convert_format(current, final_raw, output_format=output_format, quality=quality)
        _finalize_output(result, final_raw)

        # Extract thumbnail
        thumb_path = task_dir / "thumbnail.jpg"
        _ffmpeg_extract_thumbnail(final_raw, thumb_path)

        progress_cb(100)
        return final_raw

    finally:
        if tmp_dir.exists():
            shutil.rmtree(tmp_dir, ignore_errors=True)
        if not incremental and source_dir.exists():
            shutil.rmtree(source_dir, ignore_errors=True)
