"""remotion_course docx 内嵌图提取 + staging 认领（2026-09-10 独立管线计划 T6）。

口径（计划 §3.6）：
- docx = zip：stdlib（zipfile + ElementTree）按 ``word/document.xml`` **body 顺序**
  解析——``w:p`` 段落抽文本；``w:tbl`` 表格逐行把单元格文本拼成独立文本行（课程
  脚本表格是数据密集区，丢了会被 faithful 覆盖率/数字校验判死）；段落内 drawing/blip
  的 ``r:embed`` 经 rels 解析到 ``word/media/``，文本原位插入 ``【图片N】`` 标记
  （插入发生在 ``_strip_visual_markers`` 清洗**之后**——用私用区占位符保护标记不被
  【】白名单剥掉）。
- 与 markitdown 口径差异：with_images 路径不含脚注/文本框/页眉页脚——v1 接受并在
  文档写明；``with_images=1`` 仅 remotion_course 创建页使用。
- 过滤：尺寸过小（<15KB 或边长 <120px，logo/装饰）丢弃不配标记（ignored_images
  计数，不静默）。
- staging：``<storage>/script-uploads/{extract_id}/img_N.<ext>``（extract_id = ULID）；
  TTL 24h，每次 extract 顺带清理（best-effort，无新 cron）。
- 认领：create/reparse 时图片搬进 ``task_dir/visuals/embedded/``，建 ``ref → 任务
  相对路径``映射；认领后删除 staging 目录；过期/丢失 → degradation 留痕
  （``embedded_images_expired``）+ 调用方写 payload warning 标记，任务继续。
- 安全：extract_id 白名单（ULID 字符集）、ref 白名单（``img_\\d+``），防路径穿越。
"""

from __future__ import annotations

import io
import logging
import os
import re
import shutil
import time
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree as ET

from ..degradations import record_degradation

logger = logging.getLogger(__name__)

STAGING_DIR_NAME = "script-uploads"
STAGING_TTL_SECONDS = 24 * 3600
MIN_IMAGE_BYTES = 15 * 1024
MIN_IMAGE_EDGE = 120
# 条目 18（2026-09-12 缺陷批）：staging 按用户子目录化
# （``script-uploads/{user_id}/{extract_id}/``，认领天然按属主隔离）+ 每用户配额。
# 配额基准 = 生产 64C/128G/1T、<20 用户（10×200MB 与 2GB 两口径自然合一；
# 极端 20 用户全满 ≈ 40GB ≈ 1T 的 4%）。
MAX_STAGING_PER_USER = 10
MAX_STAGING_BYTES_PER_USER = 2 * 1024**3

KIND_EMBEDDED_IMAGES_EXPIRED = "embedded_images_expired"
# 条目 13（2026-09-12 缺陷批）：认领异常（磁盘满/权限）与「过期」语义不同，单列
# kind（degradations.py KIND 表仅文档用途无需登记；文案映射在
# routers/video_gen._DEGRADATION_LABELS）。
KIND_EMBEDDED_IMAGES_CLAIM_FAILED = "embedded_images_claim_failed"

# ULID 字符集（Crockford base32）；长度 8-40 容错（测试可造短 id）
_EXTRACT_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Za-hjkmnp-tv-z]{8,40}$")
_STAGED_FILE_RE = re.compile(r"^img_(\d+)(\.[A-Za-z0-9]+)?$")

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_R_EMBED = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed"

# 私用区占位符（_strip_visual_markers 的 【】 白名单剥不到）
_BLIP_PLACEHOLDER = "BLIP{}"
_BLIP_PLACEHOLDER_RE = re.compile("BLIP(\\d+)")

_ULID_ENC = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_extract_id() -> str:
    """ULID 风格 id：10 字符毫秒时间戳 + 16 字符随机。"""
    t = int(time.time() * 1000)
    ts = ""
    for _ in range(10):
        ts = _ULID_ENC[t % 32] + ts
        t //= 32
    rnd = "".join(_ULID_ENC[b % 32] for b in os.urandom(16))
    return ts + rnd


def _image_edges_ok(data: bytes) -> bool:
    """边长 ≥120px。PIL 识别不出的格式（EMF/WMF 等，浏览器也渲染不了）判不过
    （计入 ignored_images，不静默进任务）；其他读取异常（截断图等）保守放行——
    仅剩字节数门槛兜底（2026-09-11 审查 Minor：修前宽 except 把格式不可渲染也放行）。"""
    try:
        from PIL import Image, UnidentifiedImageError

        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
        return w >= MIN_IMAGE_EDGE and h >= MIN_IMAGE_EDGE
    except UnidentifiedImageError:
        return False
    except Exception:
        return True


def extract_docx_with_images(docx_bytes: bytes) -> dict:
    """docx → {text, images, ignored_images}（纯函数，不写盘——staging 由端点负责）。

    ``images`` 元素 ``{ref, name, bytes}``（bytes = 原始字节；端点序列化时转 base64）。
    非法 zip/结构 → 抛异常（调用方回退 markitdown 口径）。
    """
    from .script import _strip_visual_markers

    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        names = set(z.namelist())
        if "word/document.xml" not in names:
            raise ValueError("not a docx (missing word/document.xml)")
        doc_xml = z.read("word/document.xml")
        rels: dict[str, str] = {}
        if "word/_rels/document.xml.rels" in names:
            for rel in ET.fromstring(z.read("word/_rels/document.xml.rels")):
                rels[rel.get("Id", "")] = rel.get("Target", "")

        # 候选图按首次出现顺序登记；占位符先落位，过滤后统一编号
        media_order: list[str] = []  # rId 顺序

        def _collect_blips(elem) -> list[str]:
            found = []
            for node in elem.iter():
                if node.tag.endswith("}blip"):
                    rid = node.get(_R_EMBED) or ""
                    if rid:
                        found.append(rid)
            return found

        def _para_text(p_elem) -> str:
            parts: list[str] = []
            for node in p_elem.iter():
                if node.tag == f"{_W}t":
                    parts.append(node.text or "")
                elif node.tag.endswith("}blip"):
                    rid = node.get(_R_EMBED) or ""
                    if rid:
                        if rid not in media_order:
                            media_order.append(rid)
                        parts.append(_BLIP_PLACEHOLDER.format(media_order.index(rid)))
            return "".join(parts)

        body = ET.fromstring(doc_xml).find(f"{_W}body")
        lines: list[str] = []
        for child in body if body is not None else []:
            if child.tag == f"{_W}p":
                raw = _para_text(child)
                if not raw.strip():
                    # 纯图段落也要占位（无文本只有图）
                    if _BLIP_PLACEHOLDER_RE.search(raw):
                        lines.append(_strip_visual_markers(raw))
                    continue
                lines.append(_strip_visual_markers(raw))
            elif child.tag == f"{_W}tbl":
                for tr in child.findall(f"{_W}tr"):
                    cells = []
                    for tc in tr.findall(f"{_W}tc"):
                        cells.append(_para_text(tc).strip())
                    row = " ".join(c for c in cells if c)
                    if row:
                        lines.append(_strip_visual_markers(row))

    # 媒体字节读取 + 过滤（keep 顺序编号）
    images: list[dict] = []
    kept_index: dict[int, int] = {}  # media_order idx → img_N
    ignored = 0
    with zipfile.ZipFile(io.BytesIO(docx_bytes)) as z:
        for idx, rid in enumerate(media_order):
            target = rels.get(rid, "")
            media_path = "word/" + target.lstrip("/") if not target.startswith("word/") else target
            if media_path not in z.namelist():
                ignored += 1
                continue
            data = z.read(media_path)
            if len(data) < MIN_IMAGE_BYTES or not _image_edges_ok(data):
                ignored += 1
                continue
            n = len(images) + 1
            kept_index[idx] = n
            images.append({"ref": f"img_{n}", "name": Path(media_path).name, "bytes": data})

    # 占位符 → 标记（被过滤的图不留标记）
    def _sub_placeholder(m: re.Match) -> str:
        n = kept_index.get(int(m.group(1)))
        return f"【图片{n}】" if n else ""

    text = _BLIP_PLACEHOLDER_RE.sub(_sub_placeholder, "\n".join(lines))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"text": text, "images": images, "ignored_images": ignored}


def stage_extracted_images(images: list[dict], staging_root: Path, *, user_id: str = "default") -> str:
    """写 staging 目录（<storage>/script-uploads/{user_id}/{extract_id}/img_N.<ext>），返回 extract_id。

    条目 18（2026-09-12 缺陷批）：按 user_id 子目录化——认领侧同参数解析路径，
    天然按属主隔离（他人 extract_id 在自己命名空间下不存在 → 按过期/不存在处理）。
    """
    extract_id = new_extract_id()
    staging_dir = Path(staging_root) / str(user_id) / extract_id
    staging_dir.mkdir(parents=True, exist_ok=True)
    for img in images:
        ext = Path(img.get("name") or "img.png").suffix or ".png"
        (staging_dir / f"{img['ref']}{ext}").write_bytes(img["bytes"])
    return extract_id


def staging_usage(staging_root: Path | str, user_id: str) -> tuple[int, int]:
    """该用户在存 staging（目录数, 总字节数）。旧布局（无用户层）不计数。"""
    user_root = Path(staging_root) / str(user_id)
    if not user_root.is_dir():
        return 0, 0
    count = 0
    total = 0
    try:
        for d in user_root.iterdir():
            try:
                if not d.is_dir():
                    continue
                count += 1
                for f in d.rglob("*"):
                    if f.is_file():
                        total += f.stat().st_size
            except OSError:
                continue
    except OSError:
        return 0, 0
    return count, total


def staging_quota_exceeded(staging_root: Path | str, user_id: str, *, incoming_bytes: int = 0) -> str | None:
    """条目 18：每用户配额预检——在存 ≥MAX_STAGING_PER_USER 个，或在存+本次上传
    总量 >2GB 时返回可读文案（调用方 429），未超限返回 None。

    incoming_bytes：即将落盘的本次上传字节数（extract 端点预检时提取结果已就绪，
    可给精确值）——不计入会把峰值放到 2GB+单件。语义=「落盘后不越顶」：正好到
    顶（==）放行（复审修订 2026-09-13）。"""
    count, total = staging_usage(staging_root, user_id)
    if count >= MAX_STAGING_PER_USER or total + max(incoming_bytes, 0) > MAX_STAGING_BYTES_PER_USER:
        return "暂存空间已满，请等 24h 自动过期或联系管理员"
    return None


def cleanup_stale_staging(storage_root: Path | str, *, ttl_seconds: int = STAGING_TTL_SECONDS) -> int:
    """清理 mtime 超过 TTL 的 staging 目录（best-effort；返回清理数）。

    条目 18 后为两级布局（{user_id}/{extract_id}/）；旧布局（无用户层的平铺
    extract_id 目录，2026-09-12 前在存、TTL 24h）仍按目录自身 mtime 清理。
    """
    root = Path(storage_root) / STAGING_DIR_NAME
    if not root.is_dir():
        return 0
    removed = 0
    cutoff = time.time() - ttl_seconds
    for entry in root.iterdir():
        try:
            if not entry.is_dir():
                continue
            subdirs = [d for d in entry.iterdir() if d.is_dir()]
            if subdirs:
                # 新布局：entry = 用户目录，清其下过期 extract 目录
                for d in subdirs:
                    if d.stat().st_mtime < cutoff:
                        shutil.rmtree(d, ignore_errors=True)
                        removed += 1
            elif entry.stat().st_mtime < cutoff:
                # 旧布局（平铺 extract 目录）或空用户目录：按自身 mtime 过期清理
                shutil.rmtree(entry, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


def expires_at_iso(*, ttl_seconds: int = STAGING_TTL_SECONDS) -> str:
    """staging 过期时间（ISO 8601 带时区——前端 new Date() 可直接比对）。"""
    return (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).astimezone().isoformat(timespec="seconds")


def claim_script_images(
    extract_id: str | None,
    task_dir: Path | str,
    *,
    storage_dir: str | Path | None = None,
    user_id: str = "default",
) -> tuple[dict[str, str], bool]:
    """认领 staging 图片 → 搬进 ``task_dir/visuals/embedded/``。

    返回 ``(ref → 任务相对路径 映射, expired)``；expired=True = staging 不存在/已被
    清理（degradation 留痕 ``embedded_images_expired``，调用方写 payload warning）。
    非法 extract_id（路径穿越尝试）→ 空映射且**不**留痕（区别于合法但过期）。

    条目 18（2026-09-12 缺陷批）：staging 按用户命名空间（script-uploads/{user_id}/）
    解析——他人 extract_id 在自己命名空间下不存在 → 按过期/不存在处理（属主校验，
    不暴露「存在但无权」的语义差异）。
    """
    task_dir = Path(task_dir)
    if not extract_id or not _EXTRACT_ID_RE.fullmatch(str(extract_id)):
        return {}, False
    if storage_dir is None:
        return {}, False
    staging_dir = Path(storage_dir) / STAGING_DIR_NAME / str(user_id) / str(extract_id)
    if not staging_dir.is_dir():
        record_degradation(
            task_dir,
            kind=KIND_EMBEDDED_IMAGES_EXPIRED,
            detail=f"暂存配图已过期或不存在（extract_id={extract_id}），未自动分配，可在分镜编辑器手动上传",
        )
        return {}, True
    dest_dir = task_dir / "visuals" / "embedded"
    dest_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    try:
        for f in sorted(staging_dir.iterdir()):
            m = _STAGED_FILE_RE.fullmatch(f.name)
            if not m or not f.is_file():
                continue
            shutil.copy2(f, dest_dir / f.name)
            paths[f"img_{m.group(1)}"] = f"visuals/embedded/{f.name}"
    except OSError:
        # 半途失败清掉残留（认领非原子——copy 中断留下半套 dest 会让重试误以为
        # 已认领过；staging 保留供下次重试，2026-09-11 审查 Minor）。
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    shutil.rmtree(staging_dir, ignore_errors=True)
    return paths, False
