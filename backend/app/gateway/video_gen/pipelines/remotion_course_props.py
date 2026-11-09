"""Storyboard → DataDrivenCourse props 构建（T4，2026-09-08 remotion-course 计划）。

gateway 构建完整 props（含品牌包解析）随 payload 下发，worker 只消费（T3 拍板）。

- 版式指派 D14 优先级链：用户手改（frame.layout，编辑器写回）> planner hint
  （frame.layout，planner 只在 None 时写——两方共享字段但写入纪律保证用户胜出）
  > 确定性规则表（D13，本模块 _deterministic_layout）。
- captions 从帧级 srt 转换为全局毫秒（T2 根部字幕层契约）；无 srt 的帧按语音
  时长均匀切句合成句级 captions（不静默丢字幕）。
- 音频/素材路径 = task_dir 相对路径（worker 侧 symlink public/assets → task_dir
  后以 assets/ 前缀寻址）；远端 URL 原样下发由 worker 本地化。
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path

from ..brand_pack import (
    INTRO_FRAMES_DEFAULT,
    OUTRO_FRAMES_DEFAULT,
    load_project_for_task,
    resolve_brand,
)
from ..degradations import record_degradation
from ..storyboard import Storyboard, StoryboardFrame

logger = logging.getLogger(__name__)

FPS = 30
# T-A3：片头/片尾默认帧数单一事实源在 brand_pack（本模块曾双处硬编码 90）
__all__ = [
    "FPS",
    "INTRO_FRAMES_DEFAULT",
    "OUTRO_FRAMES_DEFAULT",
    "build_remotion_props",
    "build_still_props",
]

from ..storyboard import REMOTION_LAYOUTS as VALID_LAYOUTS  # noqa: E402

# T-D2（D2 方案 c，2026-09-09 feedback-round1）：版式视觉槽几何（模板 VisualSlot
# 实测尺寸）——生图按槽位比例出图的单一事实源。360 渠道只有三档尺寸
# （qihu_image._map_to_seedream_size 就近贴档），残余失配由 contain 兜底。
# 不在表中的版式（文字卡无视觉槽/章节页/片尾页）生图用任务级宽高。
REMOTION_SLOT_SIZES: dict[str, str] = {
    "ImageTextLeft": "800x640",  # 5:4 图文页左/右槽
    "ImageTextRight": "800x640",
    "QuotePage": "540x675",  # 4:5 竖版配图
    "EndingPage": "520x640",  # 13:16 下集封面
    "FullVisualPage": "1920x1080",  # 16:9 全幅
    "VideoClipPage": "1280x720",  # 16:9 视频区
}


def slot_size_for_layout(layout: str | None) -> str | None:
    """帧版式 → 生图请求尺寸（"WxH"）；无视觉槽版式返回 None（调用方落任务级）。"""
    return REMOTION_SLOT_SIZES.get(layout or "")


def assign_deterministic_layouts(storyboard: Storyboard) -> int:
    """T-D2 时序前提：draft 编排完成时即做确定性版式指派并持久化 frame.layout。

    生图按槽位比例出图要求**生图时已知 layout**——修前 _deterministic_layout
    在 compose 时才跑，生图只能用任务级尺寸（16:9 出图进 5:4 槽被裁，F7）。
    D14 链不变：本函数只写 None 帧（planner hint 已在规划期写入，用户手改
    后续仍胜出——编辑端点写 frame.layout 不受影响）；compose 时 build_remotion_props
    读到合法持久值直接沿用。顺带 draft 期 LayoutSwitcher/静帧预览显示真实版式
    （隐-3）。与 build_remotion_props 的指派链严格同构（组首帧 ChapterDivider >
    规则表），保证 draft 指派 = 成片版式。
    """
    total = len(storyboard.frames)
    if not total:
        return 0
    group_start_frames: set[int] = set()
    for g in storyboard.scene_groups or []:
        if g.frame_indices:
            group_start_frames.add(min(int(i) for i in g.frame_indices))
    image_counter = [0]
    assigned = 0
    for i, frame in enumerate(storyboard.frames):
        if getattr(frame, "layout", None) in VALID_LAYOUTS:
            continue  # 用户手改 / planner hint 已写——不覆写（D14）
        if i in group_start_frames and 0 < i < total - 1:
            frame.layout = "ChapterDivider"
        else:
            frame.layout = _deterministic_layout(frame, i, total, image_counter)
        assigned += 1
    return assigned


_SENTENCE_SPLIT = re.compile(r"[。！？；\n]+")
_BULLET_SPLIT = re.compile(r"[，。！？；、\n]+")


def _first_clause(text: str, max_len: int = 18) -> str:
    """取旁白首个短句作版式标题（确定性；超长截断加省略号）。"""
    parts = [p.strip() for p in _SENTENCE_SPLIT.split(text or "") if p.strip()]
    head = parts[0] if parts else (text or "").strip()
    if len(head) > max_len:
        return head[: max_len - 1] + "…"
    return head


def _bullets(text: str, max_n: int = 4) -> list[str]:
    """旁白拆要点（句/顿号切分，去重去空，≤max_n 条）。"""
    seen: list[str] = []
    for p in _BULLET_SPLIT.split(text or ""):
        p = p.strip()
        if len(p) >= 2 and p not in seen:
            seen.append(p)
        if len(seen) >= max_n:
            break
    return seen


# ── T-B1 末帧收尾语义判定（F5 根修，2026-09-09 feedback-round1）──────────
_CLOSING_WORDS = (
    "感谢观看",
    "谢谢观看",
    "感谢聆听",
    "谢谢聆听",
    "感谢收看",
    "谢谢收看",
    "谢谢大家",
    "下期再见",
    "下次再见",
    "再见",
    "谢谢",
    "拜拜",
)
_CLOSING_PUNCT_RE = re.compile(r"[\s，。！？；、,.!?~～…·:：'\"“”‘’()（）\[\]【】《》-]+")


def _is_pure_closing(text: str) -> bool:
    """纯收尾语判定：剥掉收尾词与标点后无正文，或全文（去标点空白）≤12 字
    且含收尾词。修前末帧无条件 EndCard——n_scenes 压缩后末帧承载实际内容时
    旁白照播、画面空（F5 实锤：scene3=EndCard 承载"第三，信任…"）。"""
    stripped = _CLOSING_PUNCT_RE.sub("", text or "")
    if not stripped:
        return True  # 空旁白末帧（异常形态）按收尾处理，避免内容页空标题
    if len(stripped) <= 12 and any(w in stripped for w in _CLOSING_WORDS):
        return True
    rest = stripped
    for w in _CLOSING_WORDS:
        rest = rest.replace(w, "")
    return not rest


# ── D13 确定性版式规则表 ─────────────────────────────────────
def _deterministic_layout(frame: StoryboardFrame, index: int, total: int, image_counter: list[int]) -> str:
    if index == 0:
        return "CoverPage"
    # T-B1：末帧仅纯收尾语 → EndCard；混合内容（内容+收尾语同帧）按内容帧走
    # 规则表（追加式片尾由 build_remotion_props 补无旁白 EndCard 场景）
    if index == total - 1 and _is_pure_closing(frame.narration):
        return "EndCard"

    source = frame.visual_source
    spec = frame.graphic_spec or {}
    spec_type = spec.get("type")

    if source == "graphic" or spec_type:
        if spec_type == "big_number":
            return "ChartPage"
        if spec_type == "steps":
            return "ProcessPage"
        return "ConceptPage"  # text_card / 无 spec 的 graphic 兜底
    if source == "ai_video":
        return "VideoClipPage"
    # ai_image（默认源）：短旁白（冲击帧）→ 全幅视觉；其余左右图文交替
    if len((frame.narration or "").strip()) <= 20:
        return "FullVisualPage"
    image_counter[0] += 1
    return "ImageTextLeft" if image_counter[0] % 2 == 1 else "ImageTextRight"


# ── layoutProps 内容推导（确定性；planner hint 可携带更优内容，见 T6）──
def _layout_props(layout: str, frame: StoryboardFrame, sb: Storyboard, group_title: str | None, brand_name: str = "") -> dict:
    """确定性默认值 + ``frame.layout_content`` 白名单 overlay（独立管线计划 T5）。

    先按现状逻辑算默认 props（保证 title 等必有字段存在），再把 layout_content 中
    属于该版式字段表的合法值覆盖上去（类型矫正/非法项丢弃）。无 layout_content →
    行为与今天逐字节一致（存量兼容的关键）。
    """
    return _overlay_layout_content(_base_layout_props(layout, frame, sb, group_title, brand_name), layout, frame)


_INVALID = object()

# 模板 SceneContent 字段类型分组（video-templates/course-standard/src/content.tsx）
_INT_FIELDS = frozenset({"chapterNo", "chapterTotal", "currentChapter", "currentStep"})
_LIST_STR_FIELDS = frozenset({"bullets", "chapters", "left", "right"})
_STRUCT_LIST_FIELDS = frozenset({"steps", "chart", "cards", "options"})


def _coerce_layout_value(key: str, value):
    """layout_content 字段值 → 模板契约类型；非法返回 ``_INVALID``（调用方丢弃+日志）。"""
    if key in _INT_FIELDS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return _INVALID
    if key in _LIST_STR_FIELDS:
        if not isinstance(value, (list, tuple)):
            return _INVALID
        return [str(x).strip() for x in value if x is not None and str(x).strip()]
    if key == "steps":
        if not isinstance(value, (list, tuple)):
            return _INVALID
        out = []
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "") or "").strip()
            desc = str(item.get("desc", "") or "").strip()
            if name or desc:
                out.append({"name": name, "desc": desc})
        return out
    if key == "chart":
        if not isinstance(value, (list, tuple)):
            return _INVALID
        out = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "") or "").strip()
            try:
                num = float(item.get("value"))
                # NaN/inf 防御（M5，2026-09-11 审查）：json.loads 默认接受裸 NaN/Infinity，
                # LLM 产出可携带——round(nan) 抛 ValueError / round(inf) 抛 OverflowError，
                # 之前都发生在 try 块外直接炸渲染。专职防非法值的函数不能被非法值打炸。
                if not math.isfinite(num):
                    continue
                num = round(num)
            except (TypeError, ValueError, OverflowError):
                continue
            out.append({"label": label, "value": max(0, min(100, num))})
        return out
    if key == "cards":
        if not isinstance(value, (list, tuple)):
            return _INVALID
        out = []
        for item in value:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "") or "").strip()
            body = str(item.get("body", "") or "").strip()
            if not (label or body):
                continue
            card = {"label": label, "body": body}
            if item.get("metric") is not None:
                card["metric"] = str(item["metric"]).strip()
            out.append(card)
        return out
    if key == "options":
        if not isinstance(value, (list, tuple)):
            return _INVALID
        out = []
        for item in value:
            if not isinstance(item, dict):
                continue
            k = str(item.get("key", "") or "").strip()
            t = str(item.get("text", "") or "").strip()
            if k and t:
                out.append({"key": k, "text": t})
        return out
    if key == "instructor":
        if not isinstance(value, dict):
            return _INVALID
        return {
            "initial": str(value.get("initial", "") or "").strip(),
            "name": str(value.get("name", "") or "").strip(),
            "title": str(value.get("title", "") or "").strip(),
        }
    # 其余为字符串字段（title/kicker/quote/code/badge 等）
    if isinstance(value, (str, int, float)):
        s = str(value).strip()
        return s if s else _INVALID  # 空串视为无效——避免抹掉确定性默认值
    return _INVALID


def _overlay_layout_content(props: dict, layout: str, frame: StoryboardFrame) -> dict:
    from ..orchestration.course_script import LAYOUT_CONTENT_FIELDS

    lc = getattr(frame, "layout_content", None)
    if not isinstance(lc, dict) or not lc:
        return props
    allowed = LAYOUT_CONTENT_FIELDS.get(layout)
    if not allowed:
        return props  # 未知版式（防御）——不 overlay
    out = dict(props)
    for k, v in lc.items():
        if k not in allowed:
            continue
        coerced = _coerce_layout_value(k, v)
        if coerced is _INVALID:
            logger.warning("layout_content 字段 %s 值非法（layout=%s），已丢弃", k, layout)
            continue
        out[k] = coerced
    return out


def _base_layout_props(layout: str, frame: StoryboardFrame, sb: Storyboard, group_title: str | None, brand_name: str = "") -> dict:
    """brandName（T-D2/R7）：有项目→项目名；无项目→空（模板空则不渲染品牌行，
    不注入任务标题——与封面主标题同文重复）。"""
    narration = (frame.narration or "").strip()
    spec = frame.graphic_spec or {}

    if layout == "CoverPage":
        return {
            "title": (sb.title or "课程").strip()[:40],
            "subtitle": _first_clause(narration, 24),
            "brandName": brand_name,
        }
    if layout == "EndCard":
        return {"title": "感谢观看", "brandName": brand_name}
    if layout == "ChapterDivider":
        return {"title": (group_title or _first_clause(narration, 14))[:20], "lead": narration[:40]}
    if layout == "ChartPage":
        number = spec.get("number")
        if number is not None:
            try:
                value = int(float(str(number).replace(",", "")))
            except ValueError:
                value = 0
            return {
                "kicker": "DATA · 数据说话",
                "title": _first_clause(narration, 16),
                "chart": [{"label": str(spec.get("unit") or ""), "value": max(0, min(100, value))}],
                "headline": f"{number}{spec.get('unit') or ''}",
                "conclusion": str(spec.get("description") or narration)[:60],
            }
        return {"kicker": "DATA · 数据说话", "title": _first_clause(narration, 16), "conclusion": narration[:60]}
    if layout == "ProcessPage":
        steps = [str(s) for s in (spec.get("steps") or []) if str(s).strip()]
        if steps:
            return {
                "kicker": "PROCESS · 流程",
                "title": _first_clause(narration, 16),
                "steps": [{"name": s[:12], "desc": s[12:36]} for s in steps[:6]],
            }
        return {"kicker": "PROCESS · 流程", "title": _first_clause(narration, 16)}
    if layout in ("ImageTextLeft", "ImageTextRight"):
        return {
            "kicker": "图文讲解",
            "title": _first_clause(narration, 16),
            "bullets": _bullets(narration),
        }
    if layout == "FullVisualPage":
        return {"title": _first_clause(narration, 24), "subtitle": ""}
    if layout == "VideoClipPage":
        return {"title": _first_clause(narration, 20)}
    if layout == "ConceptPage":
        points = [str(p) for p in (spec.get("points") or []) if str(p).strip()]
        return {
            "kicker": "CONCEPT · 概念讲解",
            "title": str(spec.get("title") or _first_clause(narration, 16)),
            "lead": "",
            "bullets": points if points else _bullets(narration),
        }
    # 未匹配的版式（用户/planner 指定了 ObjectivesPage/QuotePage 等）→ 通用内容
    return {"title": _first_clause(narration, 16), "bullets": _bullets(narration)}


# ── captions ─────────────────────────────────────────────────
_SRT_TS = re.compile(r"(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})")


def _parse_srt_cues(srt: str) -> list[tuple[int, int, str]]:
    """标准 SRT → [(start_ms, end_ms, text)]；解析失败返 []。"""
    cues: list[tuple[int, int, str]] = []
    lines = (srt or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        m = _SRT_TS.search(lines[i])
        if m:
            g = [int(x) for x in m.groups()]
            start = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]
            end = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
            text_parts: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].strip() and not _SRT_TS.search(lines[j]):
                text_parts.append(lines[j].strip())
                j += 1
            if text_parts:
                cues.append((start, end, "".join(text_parts)))
            i = j
        else:
            i += 1
    return cues


def _synthesize_captions(narration: str, duration_s: float) -> list[dict]:
    """无字级 srt 的兜底：旁白按 ~10 字切块，语音时长均匀分布（句级）。"""
    text = (narration or "").strip()
    if not text or duration_s <= 0:
        return []
    chunks = [text[i : i + 10] for i in range(0, len(text), 10)]
    total_chars = sum(len(c) for c in chunks) or 1
    captions = []
    t = 0.0
    for c in chunks:
        dur = duration_s * len(c) / total_chars
        captions.append(
            {
                "word": c,
                "startMs": int(t * 1000),
                "endMs": int((t + dur) * 1000),
            }
        )
        t += dur
    return captions


def _frame_captions(frame: StoryboardFrame, offset_ms: int) -> list[dict]:
    """帧级 srt → 全局毫秒 captions；无 srt 均匀合成句级。"""
    cues = _parse_srt_cues(getattr(frame, "srt", "") or "")
    if cues:
        return [{"word": text, "startMs": offset_ms + start, "endMs": offset_ms + end} for start, end, text in cues]
    dur = frame.speech_duration or frame.duration or 0.0
    synthesized = _synthesize_captions(frame.narration, dur)
    for c in synthesized:
        c["startMs"] += offset_ms
        c["endMs"] += offset_ms
    return synthesized


# ── 主入口 ───────────────────────────────────────────────────
def build_remotion_props(
    storyboard: Storyboard,
    *,
    task_dir: Path,
    user_id: str = "default",
    course_project_id: str | None = None,
    task_payload: dict | None = None,
    storage_dir: str | Path | None = None,
) -> dict:
    """Storyboard → CourseProps（设计文档 §5 契约）。

    - 音频：frame.audio_path（task_dir 绝对路径）→ 相对路径；缺失该场景静音。
    - 素材：local_visual_path（相对）优先，其次 visual_url（远端，worker 本地化）。
    - 版式：D14 链（frame.layout > 确定性规则）；非法 layout 剔除（visual_routing
      护栏 + 此处双保险）。
    """
    task_payload = task_payload or {}
    project = load_project_for_task(storage_dir, course_project_id, user_id)
    # 无项目：brandName 留空（title 是旁白前缀，当品牌名会污染封面顶栏）
    brand = resolve_brand(project, title=None if project is None else storyboard.title)
    _materialize_brand_assets(brand, task_dir, storage_dir)

    # 任务级字幕覆盖（T-E9 三级链收口：任务显式 > 项目（brand 侧已解析）> 默认）：
    # "none" → 不产 captions（模板层无字幕）；bold/minimal/outline/default →
    # 覆写 brand.subtitleStyle.variant（修前任务级只有 none 生效，其余断链）。
    _task_style = str(task_payload.get("subtitle_style") or "").strip()
    disable_subtitles = _task_style == "none"
    if _task_style and _task_style != "none" and isinstance(brand.get("subtitleStyle"), dict):
        _spec = dict(brand["subtitleStyle"])
        _spec["variant"] = _task_style if _task_style in ("bold", "minimal", "outline") else "default"
        if _task_style == "bold":
            _spec.setdefault("fontSize", 40)
        elif _spec.get("variant") != "bold":
            _spec.pop("fontSize", None)
        brand["subtitleStyle"] = _spec

    group_of_frame: dict[int, str] = {}
    for g in storyboard.scene_groups or []:
        for fi in g.frame_indices or []:
            group_of_frame[int(fi)] = g.title

    group_start_frames: set[int] = set()
    for g in storyboard.scene_groups or []:
        if g.frame_indices:
            group_start_frames.add(min(int(i) for i in g.frame_indices))

    total = len(storyboard.frames)
    image_counter = [0]
    scenes: list[dict] = []
    # T-A3（R3）：captions 全局毫秒起算 = intro 帧数换算（模板场景从 introFrames
    # 起步）——修前 offset_ms=0 起算，挂 intro 项目全片字幕提前 3000ms。
    intro_frames = 0
    if isinstance(brand.get("intro"), dict):
        try:
            intro_frames = int(brand["intro"].get("durationInFrames") or 0)
        except (TypeError, ValueError):
            intro_frames = 0
    offset_ms = round(intro_frames * 1000 / FPS)

    for i, frame in enumerate(storyboard.frames):
        duration_s = frame.duration or 3.0
        duration_in_frames = max(1, round(duration_s * FPS))

        # 版式：D14 优先级链（frame.layout 已含用户/planner 指派，非法值剔除）
        layout = getattr(frame, "layout", None)
        if layout not in VALID_LAYOUTS:
            layout = None
        if layout is None and i in group_start_frames and 0 < i < total - 1:
            layout = "ChapterDivider"
        if layout is None:
            layout = _deterministic_layout(frame, i, total, image_counter)

        # 视觉素材
        visual: dict = {"kind": "none"}
        local_visual = getattr(frame, "local_visual_path", None)
        if local_visual:
            # T-D2（D2 方案 c）：上传图比例不可控（logo/截图任意比例）→ contain
            # 兜底不裁切；生成图走 visual_url（T-D2 起按槽位比例出图，cover 不裁）。
            visual = {
                "kind": "video" if frame.visual_type == "video" else "image",
                "path": str(local_visual),
                "fit": "contain",
            }
        elif frame.visual_url and frame.visual_source != "graphic":
            visual = {
                "kind": "video" if frame.visual_type == "video" else "image",
                "path": frame.visual_url,
                "fit": "cover",
            }
        elif frame.visual_source == "graphic":
            visual = {"kind": "graphic"}

        # T-B1（F6 止血）：EndCard/ChapterDivider 无视觉槽——模板不渲染 visual，
        # 留着 path 是"素材白生成"（末帧生图/生视频后全被丢弃）。不写 path 并
        # 留痕（若素材已生成，告诉用户为何没用上）。
        if layout in ("EndCard", "ChapterDivider") and visual["kind"] != "none":
            record_degradation(
                task_dir,
                kind="visual_unused",
                detail=f"帧 {i} 版式 {layout} 无视觉槽，已生成的素材未在成片中引用",
                frame_index=i,
            )
            visual = {"kind": "none"}

        # 音频（task_dir 绝对路径 → 相对）
        audio_rel = None
        if frame.audio_path:
            p = Path(frame.audio_path)
            try:
                audio_rel = str(p.relative_to(task_dir))
            except ValueError:
                audio_rel = p.name if (task_dir / "audio" / p.name).exists() else None

        captions = [] if disable_subtitles else _frame_captions(frame, offset_ms)

        scenes.append(
            {
                "index": i,
                "narration": frame.narration,
                "audioFile": audio_rel,
                "durationInFrames": duration_in_frames,
                "layout": layout,
                "layoutProps": _layout_props(layout, frame, storyboard, group_of_frame.get(i), str(brand.get("brandName") or "")),
                "visual": visual,
                "captions": captions,
            }
        )
        # T-A3：累计偏移与模板帧时间轴同口径（durationInFrames×1000/FPS 取整）——
        # 修前用 int(duration_s*1000) 秒级口径，帧取整后每场景漂移最多 16ms。
        offset_ms += round(duration_in_frames * 1000 / FPS)

    # T-B1（隐-1 追加式片尾 + R7 双尾合并，2026-09-09 feedback-round1）：
    # ① 末帧为混合内容（内容+收尾语同帧，按内容版式渲染）且品牌无 outro →
    #    追加无旁白 EndCard 场景（固定 OUTRO_FRAMES_DEFAULT=90 帧品牌页安静展示，
    #    不拆音频——混合帧音频整段属内容场景）；时长由模板 calculateMetadata
    #    自动跟随；QC silencedetect 含该场景但 3s<4s 阈值不触发。
    # ② 末帧本就是 EndCard 且品牌有 outro → EndCard 场景消费 outro 文案
    #    （headline/follow），brand.outro 摘除防模板 BrandOutro 再渲一遍
    #    （修前"感谢观看"双尾）。
    # ③ 混合末帧 + 品牌 outro → 不追加（BrandOutro 即片尾，混合帧音频属内容）。
    brand_outro = brand.get("outro") if isinstance(brand.get("outro"), dict) else None
    if scenes and scenes[-1]["layout"] == "EndCard" and brand_outro:
        end_props: dict = {"title": str(brand_outro.get("headline") or "感谢观看")}
        if brand_outro.get("follow"):
            end_props["follow"] = str(brand_outro.get("follow"))
        scenes[-1]["layoutProps"] = end_props
        brand.pop("outro", None)
    elif scenes and scenes[-1]["layout"] != "EndCard" and not brand_outro:
        scenes.append(
            {
                "index": len(scenes),
                "narration": "",
                "audioFile": None,
                "durationInFrames": OUTRO_FRAMES_DEFAULT,
                "layout": "EndCard",
                "layoutProps": {"title": "感谢观看"},
                "visual": {"kind": "none"},
                "captions": [],
            }
        )

    # BGM（用户上传 bgm.mp3 落 task_dir；模板 <Audio> 唯一归属，ffmpeg 不混）
    bgm: dict = {"enabled": False, "volume": storyboard.config.bgm_volume, "path": None}
    bgm_file = task_dir / "bgm.mp3"
    if bgm_file.exists():
        bgm = {"enabled": True, "volume": storyboard.config.bgm_volume, "path": "bgm.mp3"}

    # 模板原生 1920×1080（M0 版式安全区/字号按 1080p 设计；storyboard.config
    # 的同比例尺寸直接下发会把布局等比放大，超出设计基准）——统一钳到 1080p
    return {
        "title": storyboard.title or "",
        "fps": FPS,
        "width": 1920,
        "height": 1080,
        "brand": brand,
        "bgm": bgm,
        "scenes": scenes,
    }


def build_remotion_worker_payload(storyboard: Storyboard, props: dict) -> dict:
    """组装交棒 payload（pipeline_version=remotion_course + remotion_props）。"""
    from dataclasses import asdict

    config = asdict(storyboard.config)
    return {
        "video_type": "remotion_course",
        "pipeline_version": "remotion_course",
        "remotion_props": props,
        # 既有调试/兼容字段（worker 不消费，但失败上下文与运维排查有用）
        "config": config,
        "frames": [asdict(f) for f in storyboard.frames],
        "scene_groups": [asdict(g) for g in storyboard.scene_groups],
    }


def build_still_props(
    storyboard: Storyboard,
    frame_index: int,
    *,
    task_dir: Path,
    user_id: str = "default",
    course_project_id: str | None = None,
    storage_dir: str | Path | None = None,
) -> tuple[dict, str]:
    """T7：单帧静帧预览 props（3s 单场景，无音频/BGM/字幕）+ 缓存键。

    返回 (props, cache_key)。cache_key = layout + brand 关键字段 + 帧内容 hash
    （D9：换版式/改品牌才重渲，重复打开秒回）。
    """
    import hashlib

    if frame_index < 0 or frame_index >= len(storyboard.frames):
        raise ValueError(f"frame_index {frame_index} out of range")
    frame = storyboard.frames[frame_index]
    project = load_project_for_task(storage_dir, course_project_id, user_id)
    # 无项目：brandName 留空（与成片 build_remotion_props 同口径——title 是旁白
    # 前缀，当品牌名显示会污染预览图顶栏，且与成片不一致）
    brand = resolve_brand(project, title=None if project is None else storyboard.title)
    _materialize_brand_assets(brand, task_dir, storage_dir)

    image_counter = [0]
    layout = getattr(frame, "layout", None)
    if layout not in VALID_LAYOUTS:
        layout = None
    if layout is None:
        # T-D3（R10）：组首帧补 ChapterDivider 分支（与成片 build_remotion_props 的
        # D14 规则一致——修前静帧预览从不渲章节页）
        if frame_index in {min(int(i) for i in g.frame_indices) for g in (storyboard.scene_groups or []) if g.frame_indices} and 0 < frame_index < len(storyboard.frames) - 1:
            layout = "ChapterDivider"
        else:
            layout = _deterministic_layout(frame, frame_index, len(storyboard.frames), image_counter)

    visual: dict = {"kind": "none"}
    local_visual = getattr(frame, "local_visual_path", None)
    if local_visual:
        visual = {"kind": "video" if frame.visual_type == "video" else "image", "path": str(local_visual), "fit": "contain"}  # T-D2：上传图 contain 兜底
    elif frame.visual_url and frame.visual_source != "graphic":
        visual = {"kind": "video" if frame.visual_type == "video" else "image", "path": frame.visual_url, "fit": "cover"}
    elif frame.visual_source == "graphic":
        visual = {"kind": "graphic"}

    group_title = None
    for g in storyboard.scene_groups or []:
        if frame_index in [int(i) for i in (g.frame_indices or [])]:
            group_title = g.title
            break

    # T-D3（R9）：静帧品牌剔除 intro/outro——修前嵌入完整 brand（含 intro 90 帧），
    # still_frame=30 落在 intro 窗内 → 预览渲出的是片头帧而非所选版式。
    still_brand = {k: v for k, v in brand.items() if k not in ("intro", "outro")}

    props = {
        "title": storyboard.title or "",
        "fps": FPS,
        "width": 1920,
        "height": 1080,
        "brand": still_brand,
        "scenes": [
            {
                "index": 0,
                "narration": frame.narration,
                "durationInFrames": 90,  # 3s 单场景（静帧渲染取 frame=30）
                "layout": layout,
                "layoutProps": _layout_props(layout, frame, storyboard, group_title, str(still_brand.get("brandName") or "")),
                "visual": visual,
                "captions": [],
            }
        ],
    }
    key_material = json.dumps(
        {
            "layout": layout,
            "brand": {k: brand.get(k) for k in ("themeName", "colors", "fontFamily", "logoPath")},
            "narration": frame.narration,
            "visual": visual.get("path") or visual.get("kind"),
            "spec": (frame.graphic_spec or {}).get("type"),
            # T5（独立管线计划）：layout_content 进 cache key——改内容必换 key，
            # 否则编辑器改结构化内容静帧不刷新。
            "layoutContent": getattr(frame, "layout_content", None),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    cache_key = hashlib.md5(key_material.encode()).hexdigest()[:16]
    return props, cache_key


def _materialize_brand_assets(brand: dict, task_dir: Path, storage_dir: str | Path | None) -> None:
    """T8/T9：LOGO 项目资产（storage 相对路径或绝对路径）物化到 task_dir/brand/。

    worker 本地化只认 task_dir 相对路径或 http(s) URL——项目资产须先拷入任务目录。
    缺失（已被删/跨用户路径）→ 静默摘除 + 日志（渐变占位徽标兜底，不炸渲染）。
    """
    import shutil as _shutil

    logo = brand.get("logoPath")
    if not logo or logo.startswith(("http://", "https://")):
        return
    src = Path(logo) if Path(logo).is_absolute() else Path(storage_dir or "") / logo
    if not src.is_file():
        logger.warning("brand logo missing at %s — falling back to gradient badge", src)
        brand.pop("logoPath", None)
        return
    dest_dir = task_dir / "brand"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"logo{src.suffix or '.png'}"
    _shutil.copyfile(src, dest)
    brand["logoPath"] = f"brand/{dest.name}"
