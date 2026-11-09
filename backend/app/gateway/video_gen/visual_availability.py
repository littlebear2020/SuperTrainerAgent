"""视觉素材可达性审计——渲染前判断素材还能不能取到。

线上实测（2026-07-29）：两天前的任务改台词后重渲，provider 临时 URL
（TOS 签名 X-Tos-Expires=86400）已过期且无本地副本 → worker 每帧降级黑场占位
（video_gen_ai_visual.py:500-504）→ 产出「没有画面只有字幕」的视频，
状态仍 ready（worker.py:265）。用户白等一次完整渲染。

recompose_task 已预检 audio.mp3 与旁白重生成状态，独缺视觉这一层。

设计要点：纯磁盘判断不足——过期 URL 的帧磁盘上「有 URL」，必须实际探测。
探测只在渲染入口这一次性动作里跑（有界并发 + 硬超时），绝不进轮询路径。
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .storyboard import Storyboard, StoryboardFrame

logger = logging.getLogger(__name__)

_PROBE_TIMEOUT = 8.0
_PROBE_CONCURRENCY = 6
# 整体预算：必须明显小于 nginx 对普通 API 的 proxy_read_timeout（120s，
# docker/nginx/nginx.conf），否则超时的是 nginx，用户拿到 504 而非可操作提示。
_PROBE_DEADLINE = 25.0
_UA = "TrainerAI-VideoGen/1.0 (liveness probe)"


@dataclass
class VisualAudit:
    """一次渲染前的素材盘点（磁盘层）。"""

    total: int = 0
    local: int = 0
    remote: int = 0
    missing: list[int] = field(default_factory=list)

    @property
    def all_missing(self) -> bool:
        """全部帧都取不到素材（空 storyboard 不算，避免误报）。"""
        return self.total > 0 and len(self.missing) == self.total

    @property
    def missing_ratio(self) -> float:
        return len(self.missing) / self.total if self.total else 0.0


def classify_frame_visual(frame: StoryboardFrame | dict, task_dir: Path) -> str:
    """判定单帧素材状态：local（本地文件在）/ remote（仅远程 URL）/ missing。

    local 优先——worker 也是 local 优先复用（不重新下载）。
    「记录了 local_visual_path 但文件已不在」归 missing：正是用户说的
    「分镜图片直接没有了」。越界路径一并归 missing（不信任 storyboard 内容）。
    """
    if isinstance(frame, dict):
        local, url = frame.get("local_visual_path"), frame.get("visual_url")
    else:
        local = getattr(frame, "local_visual_path", None)
        url = getattr(frame, "visual_url", None)

    if local:
        try:
            resolved = (task_dir / local).resolve()
            resolved.relative_to(task_dir.resolve())
            if resolved.exists():
                return "local"
        except (ValueError, OSError):
            pass
    return "remote" if url else "missing"


def _iter_frames(storyboard: Storyboard | dict | list) -> list:
    """兼容 Storyboard / storyboard dict / frames list 三种入参。

    刻意不走 ``Storyboard.from_dict``：它要求 config 完整
    （video_type/width/height 皆必填），而磁盘上的 storyboard.json 可能是
    历史遗留或半成品——预检不该因为解析 config 就崩掉整个渲染入口。
    """
    if isinstance(storyboard, list):
        return storyboard
    if isinstance(storyboard, dict):
        return storyboard.get("frames") or []
    return storyboard.frames


def _frame_index(frame, fallback: int) -> int:
    if isinstance(frame, dict):
        idx = frame.get("index")
    else:
        idx = getattr(frame, "index", None)
    return fallback if idx is None else idx


def audit_visual_availability(storyboard: Storyboard | dict | list, task_dir: Path) -> VisualAudit:
    """盘点整份 storyboard 的素材可达性（纯磁盘，不发网络请求）。

    remote 帧只表示「有 URL」，不代表 URL 还活着——死活由
    probe_remote_liveness 判定。本函数供盘点脚本与快速预检共用。
    """
    frames = _iter_frames(storyboard)
    audit = VisualAudit(total=len(frames))
    for pos, f in enumerate(frames):
        kind = classify_frame_visual(f, task_dir)
        if kind == "local":
            audit.local += 1
        elif kind == "remote":
            audit.remote += 1
        else:
            audit.missing.append(_frame_index(f, pos))
    return audit


async def _head_ok(url: str, timeout: float) -> bool:
    """HEAD 探测单个 URL 是否仍可取。测试会 monkeypatch 本函数。

    非 http(s)（如 data: URI）直接算活——它不依赖网络。
    """
    if not url.startswith(("http://", "https://")):
        return True
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.head(url, headers={"User-Agent": _UA})
            if resp.status_code == 405:  # 少数 CDN 不支持 HEAD
                resp = await client.get(url, headers={"User-Agent": _UA, "Range": "bytes=0-0"})
            return resp.status_code < 400
    except Exception as exc:  # noqa: BLE001
        logger.info("liveness probe failed for %s: %s", url[:80], type(exc).__name__)
        return False


async def probe_remote_liveness(
    storyboard: Storyboard | dict | list,
    task_dir: Path,
    *,
    timeout: float = _PROBE_TIMEOUT,
    concurrency: int = _PROBE_CONCURRENCY,
    deadline: float = _PROBE_DEADLINE,
) -> list[int]:
    """探测「仅有远程 URL」的帧其 URL 是否还活着，返回死链帧 index（升序）。

    有本地副本的帧跳过（worker 不会去下载）。有界并发 + 单请求超时 + **整体 deadline**，
    只在渲染入口跑一次——不可用于轮询路径。

    整体 deadline 不可省：并发 6 + 单请求 8s，96 帧全黑洞时 = 16 批 x 8s = 128s，
    超过 nginx 对普通 API 的 proxy_read_timeout 120s → 用户拿到 504 而不是
    「哪些帧失效、该怎么办」的可操作提示，等于白等还没说清。
    超时即放弃探测并**放行**（返回空列表）：探不出来不等于素材坏了，
    宁可让渲染继续（worker 侧仍有黑场占位 + placeholder_frames 上报兜底），
    也不要凭一次网络抖动把用户的重新合成拦死。
    """
    targets = [(pos, f) for pos, f in enumerate(_iter_frames(storyboard)) if classify_frame_visual(f, task_dir) == "remote"]
    if not targets:
        return []

    sem = asyncio.Semaphore(concurrency)

    async def _one(pos: int, frame) -> int | None:
        url = frame.get("visual_url") if isinstance(frame, dict) else getattr(frame, "visual_url", None)
        async with sem:
            alive = await _head_ok(url, timeout)
        return None if alive else _frame_index(frame, pos)

    gather = asyncio.gather(*(_one(pos, f) for pos, f in targets))
    try:
        results = await asyncio.wait_for(gather, timeout=deadline)
    except TimeoutError:
        logger.warning(
            "liveness probe exceeded %.0fs deadline for %d remote frames — skipping precheck (fail-open)",
            deadline,
            len(targets),
        )
        return []
    return sorted(i for i in results if i is not None)


def frames_in_regen_error(storyboard: Storyboard | dict | list) -> list[int]:
    """上次单帧重生失败的帧 index（与 audit/probe 同口径：f["index"] 或位置兜底）。

    ``regen_state`` 是 ``_set_regen_state`` 写入 storyboard.json 帧级 dict 的额外键
    （非 StoryboardFrame dataclass 字段）——因此**必须对原始 dict 调用**，不能对
    StoryboardFrame 对象调用（``StoryboardFrame(**{...})`` 会丢弃非字段键）。
    """
    frames = _iter_frames(storyboard)
    result: list[int] = []
    for pos, f in enumerate(frames):
        state = f.get("regen_state") if isinstance(f, dict) else getattr(f, "regen_state", None)
        if state == "error":
            result.append(_frame_index(f, pos))
    return result


def format_unavailable_error(
    indices: list[int],
    total: int,
    *,
    regen_failed: list[int] | None = None,
) -> str:
    """统一的中文错误文案（1-based 帧号；recompose / re-render 共用）。

    帧号用中文顿号连接而非 Python list repr——``帧 [1, 2, 5]`` 的方括号是
    实现细节泄漏到用户界面（仓库别处也有这个老毛病，此处不再新增）。

    ``regen_failed`` 非空时追加死因分治说明：这些帧上次单帧重生失败，
    直接合成会沿用失败前的旧画面（说「远程链接已过期」是误导）。
    """
    shown = "、".join(str(i + 1) for i in indices[:10])
    more = " 等" if len(indices) > 10 else ""
    if regen_failed:
        # 全部是重生失败帧 -> 主因写「上次重新生成失败」
        if set(regen_failed) >= set(indices):
            return f"帧 {shown}{more} 的画面素材不可用（共 {len(indices)}/{total} 帧）——上次重新生成失败，直接合成会沿用失败前的旧画面。请对这些帧点「重新生成」补齐画面后再合成。"
        # 混合场景：部分死链 + 部分重生失败
        regen_shown = "、".join(str(i + 1) for i in regen_failed[:5])
        regen_more = " 等" if len(regen_failed) > 5 else ""
        return f"帧 {shown}{more} 的画面素材不可用（共 {len(indices)}/{total} 帧）——远程链接已过期且无本地副本，直接合成只会得到黑屏画面。其中帧 {regen_shown}{regen_more} 是上次重新生成失败——请对这些帧点「重新生成」补齐画面后再合成。"
    return f"帧 {shown}{more} 的画面素材不可用（共 {len(indices)}/{total} 帧）——远程链接已过期且无本地副本，直接合成只会得到黑屏画面。请对这些帧点「重新生成」补齐画面后再合成。"
