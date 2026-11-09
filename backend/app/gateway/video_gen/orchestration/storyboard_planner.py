"""Plan a Storyboard from narrations.

- ai_image: 为每条旁白生成画面描述（语言随旁白，拍板④；解释性视觉分层模板）。
- ai_video: 为每条旁白生成视频描述（语言随旁白，拍板④；强制主体动作层 + 运镜分离 + 环境动态层）。
"""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from ..degradations import record_degradation
from ..storyboard import REMOTION_LAYOUTS, SceneGroup, ShotDirection, Storyboard, StoryboardConfig, StoryboardFrame
from . import _invoke_llm_bounded
from .graphic_spec_extractor import extract_graphic_specs
from .visual_routing import content_routing_enabled, normalize_visual_routing

logger = logging.getLogger(__name__)

# 风格具体质量词（按 style 选配）。2026-08-12（prompt-optimization-v1 T1）：
# 从泛化「高质量」改为每风格具体技术词，直接引导画面上限而非靠荣誉词堆砌。
# ⚠️ realistic 不含「电影级调色」——与 worker eq 滤镜调色（color_palette）职责重叠，双重调色有风险。
_QUALITY_SUFFIX = {
    "realistic": "35mm 浅景深，自然光线，细腻材质质感",
    "anime": "干净线稿，鲜明色块，赛璐珞质感",
    "illustration": "扁平矢量质感，明快配色，清晰轮廓",
    "watercolor": "水彩笔触，通透晕染，纸纹肌理",
    "default": "柔和光线，细腻材质质感",
}

# 2026-08-12（prompt-optimization-v1 T1/R1）：_NEGATIVE_SUFFIX 已删除。
# 白熊效应（「避免畸变」反而提示模型生成畸变）+ Ark seedream 无 negative 字段，
# 负向引导不是有效的去畸变手段。水印/字幕抑制迁入系统 prompt 纯净条款（正向化表述）。


def _enhance_prompt_quality(prompt: str, style: str = "default") -> str:
    """image_prompt 后处理追加风格具体质量词（按 style 选配）。

    2026-08-12（prompt-optimization-v1 T1/T2）：负向引导后缀已删除（白熊效应 +
    Ark 无 negative 字段）。水印/字幕抑制迁入系统 prompt 纯净条款（正向化表述）。
    """
    suffix = _QUALITY_SUFFIX.get(style, _QUALITY_SUFFIX["default"])
    result = f"{prompt}，{suffix}"
    # R2: 总长度守护——拼接后 >280 字打 warning（不截断，截断可能切断语义）
    if len(result) > 280:
        logger.warning("enhanced prompt exceeds 280 chars (%d); style=%s", len(result), style)
    return result


# Task 8：镜头语言词表。key = LLM 产出的枚举值，value = 下发给视频模型的中文术语。
# 只认词表内的值——LLM 偶发生词直接忽略，不污染提示词。
_SHOT_SIZE_TERMS = {
    "wide": "远景",
    "medium": "中景",
    "close": "特写",
}
_CAMERA_MOVE_TERMS = {
    "static": "固定机位",
    "push_in": "缓慢推近",
    "pull_out": "缓慢拉远",
    "pan": "横向摇移",
    "tilt": "纵向摇移",
}


# Task 9：语义相反的运镜对，紧邻出现观感跳脱。
_OPPOSING_MOVES = {("push_in", "pull_out"), ("pull_out", "push_in"), ("pan", "tilt"), ("tilt", "pan")}
_MOVE_CYCLE = ["push_in", "static", "pull_out", "pan"]


def smooth_camera_continuity(frames: list) -> None:  # noqa: ANN001
    """原地消除相邻镜的运镜突变（Task 9）。

    为什么是后处理而不是改提示词：``_fill_image_prompts_robust`` 按 CHUNK=8 分块
    **并发**调 LLM（asyncio.gather），块间互不可见——原理上无法让第 9 镜看到
    第 8 镜的运镜。故连贯性只能在拿到全部结果后统一收敛。

    只调整**已有**镜头语言的帧；字段为 None 的旧 storyboard 原样不动（不凭空填值）。
    """
    if not frames:
        return
    # 开场建立环境：首镜若已指定景别且不是远景，改远景。
    if frames[0].shot_size and frames[0].shot_size != "wide":
        frames[0].shot_size = "wide"
    for i in range(1, len(frames)):
        prev, cur = frames[i - 1], frames[i]
        if not cur.camera_move or not prev.camera_move:
            continue
        if (prev.camera_move, cur.camera_move) in _OPPOSING_MOVES:
            # 相反运镜之间插入静止，作视觉缓冲
            cur.camera_move = "static"
            continue
        if cur.camera_move != prev.camera_move:
            continue
        # BUG-C（2026-08-03）：static 相邻**不是**突变——稳定机位连续多镜完全合法
        # （访谈、图表讲解、标题卡）。修前 [static]×5 会被改成
        # [static, push_in, static, push_in, static]，给静止画面强加运镜，
        # 既违反本函数「不凭空造值」的契约，也把 LLM 的正确选择改坏。
        if prev.camera_move == "static":
            continue
        # 重复 → 换到 cycle 里第一个既不重复、也不与前一镜构成相反对的值。
        # 判据必须同时含这两条：只避重复会让 push_in,push_in 变成 push_in,pull_out
        # （相反对），等于把一个突变换成另一个突变。
        for cand in _MOVE_CYCLE:
            if cand != prev.camera_move and (prev.camera_move, cand) not in _OPPOSING_MOVES:
                cur.camera_move = cand
                break


def _apply_ir_fields(frames: list, parsed: list) -> None:  # noqa: ANN001
    """Phase 1 T2（2026-08-17 方言层）：把 LLM 产出的 ir 对象写回帧 visual_ir。

    只接受 dict 且至少一个非空字段（schema 收窄到五个白名单键）；LLM 未返/返坏
    -> 保持 None（该帧走 legacy 透传，现有兜底链不变，QP1-6 不变量不破）。
    """
    if len(parsed) != len(frames):
        logger.warning(
            "ir fields count mismatch: LLM returned %d entries for %d frames (unmatched frames keep visual_ir=None)",
            len(parsed),
            len(frames),
        )
    ir_fields = ("subject", "action", "environment", "style", "audio")
    for f, entry in zip(frames, parsed):
        if not isinstance(entry, dict):
            continue
        ir = entry.get("ir")
        if not isinstance(ir, dict):
            continue
        clean = {k: str(ir[k]).strip() for k in ir_fields if isinstance(ir.get(k), str) and ir[k].strip()}
        if clean:
            f.visual_ir = clean


def _normalize_entity_name(name) -> str:
    """M2 实体名归一化键：strip + 大小写不敏感（同 routers._normalize_sheet_name 口径）。"""
    return str(name or "").strip().casefold()


def _normalize_visible_list(value, name_map: dict, kind: str, frame_index: int, task_dir) -> list | None:  # noqa: ANN001
    """M2：单帧出场声明归一化。

    - value 缺失/None/非列表 → None（语义铁律：回退旧行为，不是 []）；
    - [] → []（明确空镜）；
    - 名册外名（幻觉）→ 丢弃该条目 + sidecar ``declared_invalid``（B13：不阻断生成）；
    - view 非法 → 回退缺省（键删除：角色缺省 front / 道具缺省 0=main）。
    """
    if not isinstance(value, list):
        return None
    kept: list[dict] = []
    invalid: list[str] = []
    for entry in value:
        if not isinstance(entry, dict):
            # 条目非对象 = LLM 不合作 → 整条声明置 None（语义铁律，非 []）
            return None
        key = _normalize_entity_name(entry.get("name"))
        canonical = name_map.get(key)
        if canonical is None:
            invalid.append(str(entry.get("name") or ""))
            continue
        item: dict = {"name": canonical}
        view = entry.get("view")
        if kind == "character":
            if view in ("front", "side", "back"):
                item["view"] = view
        else:
            if isinstance(view, int) and not isinstance(view, bool) and view in (0, 1, 2):
                item["view"] = view
        kept.append(item)
    if invalid and task_dir is not None:
        from ..service import record_ref_injection_sidecar  # 惰性导入避免环

        record_ref_injection_sidecar(
            task_dir,
            frame_index=frame_index,
            declared_entities=[],
            injected_refs=[],
            truncated=[],
            skipped=[{"entity": f"{kind}:{n}", "reason": "declared_invalid"} for n in invalid],
            reason="",
        )
    if invalid:
        logger.warning("M2 visible_%ss frame %d: %d 个名册外名字被丢弃（declared_invalid）", kind, frame_index, len(invalid))
    return kept


def _apply_visible_fields(frames: list, parsed: list, entity_roster: dict | None, task_dir=None, *, index_offset: int = 0) -> None:  # noqa: ANN001
    """M2：把 LLM 产出的 visible_characters/visible_props 写回帧（对齐 _apply_camera_fields 副作用式）。

    名册为空（无项目/无资产/显式脱离）时**不写**——帧保持 None（旧行为）。
    条数不齐时 zip(strict=False)：多余丢弃、缺失帧保持 None（同 camera fields 降级口径）。
    ``index_offset``：mini storyboard 局部帧号 → 真实帧号（逐帧路径 mini index=0 →
    真实 i；bulk → 块 offset；default 0 = 直接整表调用）。仅影响 sidecar 帧号归因。
    """
    roster = entity_roster or {}
    chars = roster.get("characters") or []
    props = roster.get("props") or []
    if not chars and not props:
        return
    char_map = {_normalize_entity_name(c.get("name")): c.get("name") for c in chars if c.get("name")}
    prop_map = {_normalize_entity_name(p.get("name")): p.get("name") for p in props if p.get("name")}
    if len(parsed) != len(frames):
        logger.warning(
            "visible fields count mismatch: LLM returned %d entries for %d frames (unmatched frames keep visible_*=None)",
            len(parsed),
            len(frames),
        )
    for f, entry in zip(frames, parsed, strict=False):
        if not isinstance(entry, dict):
            continue
        real_index = index_offset + getattr(f, "index", 0)
        f.visible_characters = _normalize_visible_list(entry.get("visible_characters"), char_map, "character", real_index, task_dir)
        f.visible_props = _normalize_visible_list(entry.get("visible_props"), prop_map, "prop", real_index, task_dir)


async def rederive_visible_declarations(llm, *, title: str, narration: str, entity_roster: dict | None) -> tuple[list | None, list | None]:
    """M2：旁白被编辑后的单帧出场声明重推（prompt 重生端点专用；保留原声明之外的唯一重推通道）。

    返回 (visible_characters, visible_props)（已归一化；无名册 → (None, None) 不重推）。
    """
    roster = entity_roster or {}
    chars = roster.get("characters") or []
    props = roster.get("props") or []
    if not chars and not props:
        return None, None
    lines = []
    if chars:
        lines.append("角色名册：" + "、".join(f"{c.get('name')}" for c in chars))
    if props:
        lines.append("道具名册：" + "、".join(f"{p.get('name')}" for p in props))
    system = (
        "你是视频分镜的出场检查员。根据单条旁白判断本镜实际出场的角色与道具。\n" + "\n".join(lines) + "\n只允许使用名册中的名字，不得发明新名字；未出场输出空数组 []。\n"
        '严格输出 JSON：{"visible_characters": [{"name": "角色名", "view": "front|side|back"}], '
        '"visible_props": [{"name": "道具名", "view": 0|1|2}]}（view 可省略）。不要输出其他任何文字。'
    )
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": json.dumps({"title": title, "narration": narration}, ensure_ascii=False)},
    ]
    try:
        resp = await _invoke_llm_bounded(llm, messages)
        content = resp.content if hasattr(resp, "content") else str(resp)
        # 整体 JSON 对象（两键同取）——栅栏剥离复用 _strip_markdown_fence
        obj = json.loads(_strip_markdown_fence(content))
        char_map = {_normalize_entity_name(c.get("name")): c.get("name") for c in chars}
        prop_map = {_normalize_entity_name(p.get("name")): p.get("name") for p in props}
        vc = _normalize_visible_list(obj.get("visible_characters"), char_map, "character", 0, None)
        vp = _normalize_visible_list(obj.get("visible_props"), prop_map, "prop", 0, None)
        return vc, vp
    except Exception as e:
        logger.warning("rederive_visible_declarations failed: %s", e)
        return None, None


def _apply_camera_fields(frames: list, parsed: list) -> None:  # noqa: ANN001
    """把 LLM 产出的 shot_size/camera_move 写回帧（Task 8）。

    只接受词表内的枚举值——LLM 偶发生词（"bird_eye"）直接丢弃，保持 None，
    下游 compose_video_prompt 因此不会把生词塞进提示词。

    2026-08-03 评审修：条数不符时补 warning。``strict=False`` 是**刻意**的
    （多余条目丢弃、缺失帧保持 None，都是可接受降级——image_prompt 另有兜底链，
    镜头语言缺失只是少一层提示词增强），但静默会让「LLM 少返一条 → 尾帧无镜头语言」
    无从排查。日志不改变行为，只让它可观测。
    """
    if len(parsed) != len(frames):
        logger.warning(
            "camera fields count mismatch: LLM returned %d entries for %d frames (unmatched frames keep shot_size/camera_move=None)",
            len(parsed),
            len(frames),
        )
    for f, entry in zip(frames, parsed, strict=False):
        if not isinstance(entry, dict):
            continue
        shot = entry.get("shot_size")
        move = entry.get("camera_move")
        if shot in _SHOT_SIZE_TERMS:
            f.shot_size = shot
        if move in _CAMERA_MOVE_TERMS:
            f.camera_move = move


def compose_video_prompt(frame) -> str:  # noqa: ANN001
    """把结构化镜头语言拼进画面提示词（Task 8）。

    未指定或值不在词表时原样返回 image_prompt——不得凭空造词，也不得因缺字段而报错
    （旧 storyboard 全是 None）。

    BUG-D（2026-08-03）：拼接前剥掉 base 尾部的中英文逗号、空 base 时不加前导逗号。
    修前用户在分镜编辑器把 image_prompt 手改成「场景描述，」会得到「场景描述，，中景」，
    空 prompt 会得到「，中景，缓慢推近」。
    """
    base = (frame.image_prompt or "").strip().rstrip("，,").strip()
    parts: list[str] = []
    shot = _SHOT_SIZE_TERMS.get(frame.shot_size or "")
    move = _CAMERA_MOVE_TERMS.get(frame.camera_move or "")
    if shot:
        parts.append(shot)
    if move:
        parts.append(move)
    if not parts:
        # 无镜头语言时原样返回（含用户自己写的尾逗号——不做与本功能无关的清洗）
        return frame.image_prompt or ""
    return f"{base}，{'，'.join(parts)}" if base else "，".join(parts)


def _detect_style(style_prefix: str) -> str:
    """从 style_prefix 粗判风格类别，选质量词。"""
    p = (style_prefix or "").lower()
    if "anime" in p or "动漫" in p:
        return "anime"
    if "watercolor" in p or "水彩" in p:
        return "watercolor"
    if "illustration" in p or "插画" in p:
        return "illustration"
    if "realistic" in p or "photorealistic" in p or "写实" in p or "3d" in p:
        return "realistic"
    return "default"


def with_style_prefix(style_prefix: str, body: str) -> str:
    """T5 三轨对齐（2026-08-18 两反馈批）：风格硬前置的共用拼装措辞。

    风格注入三轨——bulk system 硬约束（见 _generate_course_prompts_enhanced 的
    style_instruction）、重生端点硬前置（routers/video_gen.py）、角色图
    _with_style 前置（character_sheet_generator.py）。三轨不同构，改其一会加剧
    帧间割裂：前置两轨必须共用本函数（同一分隔符/同一空前缀语义），bulk 轨措辞
    同步升级。空前缀原样返回（无前导逗号）。
    """
    return f"{style_prefix}，{body}" if style_prefix else body


# T5（2026-08-18 两反馈批 P-06/反馈1问题2）：实拍风格判别——锚定卡对 corporate_live
# 等真人风格强制「真人行为场景、禁止机械/器物隐喻」（事故：商务实拍稿产出黄铜
# 天平/玻璃齿轮隐喻）。planner 只拿得到 style_prefix 串，按关键词判别：
# 「实拍/摄影/纪录片」。precision_eng 虽含「写实」但是 3D 风格——不含这些词，
# 天然排除（勿改成认「写实」）。
_LIVE_ACTION_MARKERS = ("实拍", "摄影", "纪录片")


def _is_live_action_style(style_prefix: str) -> bool:
    return any(m in (style_prefix or "") for m in _LIVE_ACTION_MARKERS)


# 2026-08-19（P0 兜底链风格注入）：风格保护三段指令的**同源**构造函数。修前这三段
# 内联在 _generate_course_prompts_enhanced（bulk system 独享），单帧兜底/主题兜底/
# 重生端点零风格信息——unaligned 降级帧裸奔出隐喻（corporate_live 帧 16-22/0-8 事故）。
# 抽成模块级函数供 bulk、单帧兜底、重生端点三轨共用；改措辞一处处改，禁止拷贝漂移
# （同 with_style_prefix 的三轨对齐纪律）。


def _build_style_instruction(style_prefix: str) -> str:
    """风格硬约束指令（全片声明式，T-I0-② 措辞）。空前缀返空串。"""
    return f"全片画面风格统一为硬约束：每条描述必须呈现「{style_prefix}」的风格特征，禁止出现与「{style_prefix}」冲突的画风或元素；风格特征自然融入每条描述，不要机械重复风格词。" if style_prefix else ""


def _build_live_action_instruction(style_prefix: str) -> str:
    """实拍禁隐喻条款（硬约束）。非实拍风格返空串（判别见 _is_live_action_style）。"""
    if not _is_live_action_style(style_prefix):
        return ""
    return (
        "实拍禁隐喻条款（硬约束）：本片为实拍风格，所有画面必须是真实人物在真实环境中执行的实景行为；禁止机械装置、器物隐喻、象征道具（如天平、齿轮、沙漏、雕塑、提线木偶、色块人偶）；抽象概念必须转换为真人可执行的工作或生活场景来呈现。\n\n"
    )


def _build_anchor_instruction(anchor_card: str) -> str:
    """全片锚定卡注入指令。空卡返空串（锚定调用失败回落，不更坏）。"""
    anchor_card = (anchor_card or "").strip()
    return f"全片锚定卡（硬约束——每条画面描述必须遵守，固定主角/场景/世界观/色调基调贯穿全片不得偏离，禁用清单逐条遵守）：\n{anchor_card}\n\n" if anchor_card else ""


def _style_guard_instruction(style_prefix: str, anchor_card: str) -> str:
    """单帧兜底/重生路径的风格守卫——与 bulk system 同源三段拼接（风格硬约束 + 实拍
    禁隐喻 + 锚定卡），尾部补分隔后前置到单帧 system。空前缀且无锚 → 空串（行为同现状）。"""
    joined = "".join(
        p
        for p in (
            _build_style_instruction(style_prefix),
            _build_live_action_instruction(style_prefix),
            _build_anchor_instruction(anchor_card),
        )
        if p
    )
    if joined and not joined.endswith("\n\n"):
        joined += "\n\n"
    return joined


# T5：全片锚定生成 system。分块并发（CHUNK=8）块间互不可见，全片一致性只能靠
# 分块前**一次**锚定调用产出同一份锚定卡注入每块（拍板②：default 不绑默认风格，
# 统一性由锚定卡兜底）。语言随 T4 拍板④同口径锁定（评审 c：防锚定卡本身成为新
# 漂移源）。style_string 供 IR style 槽统一覆写（_unify_ir_style_slots）。
_ANCHOR_SYSTEM = (
    "你是专业的视频美术指导。请通读全部旁白，为整支视频制定「全片视觉锚定卡」——"
    "后续分镜将分段生成，每段都会拿到这份锚定卡，主角/场景/世界观/色调必须与其一致。\n\n"
    "锚定卡内容（合计不超过150字）：\n"
    "1. 固定主角/主体：全片反复出场的人物或核心物体的固定外观\n"
    "2. 固定场景/世界观：全片统一的环境与时代设定\n"
    "3. 色调基调与画风：一句话视觉基调{style_clause}\n"
    "4. 禁用清单：全片禁止出现的元素（与基调冲突的画风、喧宾夺主的道具等）\n\n"
    # T5b（拍板⑦）：信息图版式卡——AI 结合全片内容与所选风格现场设计一次，
    # 全任务 graphic 帧逐字复用（治「10 张文字卡 10 种风格」）。
    "另产出一条「信息图版式卡」（与全片内容及上述视觉风格协调，全片信息图/文字卡帧将逐字复用同一段版式指令）：\n"
    "固定配色基调 + 字体风格 + 版式骨架，合计不超过60字。\n\n"
    # T6（2026-08-18 拍板①）：场景分组并入锚定调用结构化输出（零额外 LLM 调用）——
    # 通读全量旁白正是分组所需视野；start=组首旁白序号（0 起），首组 start 必须为 0。
    "场景分组：再输出 scene_groups，把全部旁白划分为若干连续场景组（同组=同一场景或同一话题段落，"
    "每组通常 3~10 帧相邻旁白；start 递增且互不相同，首组 start 必须为 0），供组内画面锚定与组首参考锚定使用。\n\n"
    "{live_action_clause}"
    # 2026-08-28（AC2 实证修复）：项目参考图在场时，锚定卡禁止虚构名册角色/道具的
    # 外观细节——真实事故：锚定卡凭空写「小林戴圆框眼镜、老王橙色围裙」，广播至 30 帧
    # （17 帧含「眼镜」/18 帧含「围裙」），文字与合成图参考打架，f17 性别、f22 眼镜失真。
    "{roster_clause}"
    "语言锁定：锚定卡、风格短语与版式卡必须使用与旁白相同的语言（旁白中文→中文，旁白英文→英文），严禁中英混用。\n"
    '严格输出 JSON：{{"anchor_card": "锚定卡全文（≤150字）", "style_string": "色调基调与画风短语（≤30字）", '
    '"graphic_layout_card": "信息图版式指令（配色基调+字体风格+版式骨架，≤60字）", '
    '"scene_groups": [{{"title": "场景小标题（与旁白同语言）", "start": 起始旁白序号}}]}}，不要输出其他任何文字。'
)

# 实拍风格的锚定卡强制条款（反馈1问题2：真人行为场景，禁机械/器物隐喻）。
_LIVE_ACTION_ANCHOR_CLAUSE = "本片为真人实拍风格：锚定卡必须写明「全片为真人行为场景——画面由真人或真实角色执行旁白描述的行为」，并在禁用清单中加入机械装置、器物隐喻、象征道具（如天平、齿轮、沙漏）等一切替代真人的画面元素。\n\n"


def _anchor_roster_clause(entity_roster: dict | None) -> str:
    """2026-08-28（AC2 实证修复）：名册角色/道具的外观锚定权移交渲染层参考图。

    名册非空 → 条款文本（锚定卡只点名、禁写外观细节）；空/None → 空串（契约零变化）。
    实证事故：锚定卡虚构「小林戴圆框眼镜/老王橙色围裙」广播 30 帧，与合成图参考打架
    （f17 性别、f22 眼镜失真）——文字与参考图冲突时部分帧文字胜出。
    """
    roster = entity_roster or {}
    chars = [str(c.get("name") or "").strip() for c in (roster.get("characters") or [])]
    props = [str(p.get("name") or "").strip() for p in (roster.get("props") or [])]
    names = [n for n in chars + props if n]
    if not names:
        return ""
    return f"项目参考图锁定外观：{('、'.join(names))} 等名册角色/道具的外观（性别、年龄、发型、是否戴眼镜、衣着等）以渲染时注入的项目参考图为准——锚定卡中对这些角色/道具只准点名，禁止虚构或描述任何外观细节（需要指代时仅写名字）。\n\n"


async def _generate_global_anchor(
    llm,
    *,
    title: str,
    narrations: list[str],
    style_prefix: str,
    entity_roster: dict | None = None,
) -> dict | None:
    """T5：分块前一次全片锚定调用。返回 {anchor_card, style_string, graphic_layout_card, scene_groups}；失败返回 None。

    评审 a：锚定调用失败/输出解析失败 → 返回 None（调用方跳过注入走现状分块逻辑，
    不更坏）+ warning。**任何**异常都吞（含测试桩传 None/MagicMock 时的
    AttributeError/TypeError——planner 系测试大量以 None LLM 直调）。
    T6：scene_groups 原始 list 顺带透传（校验/重建在 _scene_groups_from_anchor，
    无效不连坐锚定卡——同 graphic_layout_card 可选字段先例）。
    ``entity_roster``（2026-08-28 AC2 实证修复）：名册非空时 system 追加
    「外观以参考图为准、禁止虚构」条款（名册角色/道具只点名不写外观）；
    None/空 = 契约零变化（无项目链路不变量）。
    """
    try:
        style_clause = f"（须与「{style_prefix}」风格一致）" if style_prefix else ""
        live_action_clause = _LIVE_ACTION_ANCHOR_CLAUSE if _is_live_action_style(style_prefix) else ""
        roster_clause = _anchor_roster_clause(entity_roster)
        system = _ANCHOR_SYSTEM.format(style_clause=style_clause, live_action_clause=live_action_clause, roster_clause=roster_clause)
        user = json.dumps(
            {"title": title, "style_prefix": style_prefix, "narrations": "\n".join(narrations)},
            ensure_ascii=False,
        )
        resp = await _invoke_llm_bounded(llm, [{"role": "system", "content": system}, {"role": "user", "content": user}])
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = json.loads(_strip_markdown_fence(content))
        if not isinstance(parsed, dict):
            raise TypeError(f"anchor output is {type(parsed).__name__}, expected object")
        anchor_card = str(parsed.get("anchor_card", "") or "").strip()
        if not anchor_card:
            raise ValueError("anchor_card is empty")
        # style_string 缺失时回落整卡（语义正确，仅偏长；正常路径 ≤30 字）
        style_string = str(parsed.get("style_string", "") or "").strip() or anchor_card
        # T5b：版式卡可选——缺失返空串（渲染侧回落确定性默认版式卡，不连坐锚定卡）
        graphic_layout_card = str(parsed.get("graphic_layout_card", "") or "").strip()
        # T6：场景分组可选——非 list 归一空 list（无效由消费方兜底，不连坐锚定卡）
        scene_groups_raw = parsed.get("scene_groups")
        scene_groups_raw = scene_groups_raw if isinstance(scene_groups_raw, list) else []
        logger.info("全片锚定卡已生成（%d 字）：%s", len(anchor_card), anchor_card[:60])
        return {
            "anchor_card": anchor_card,
            "style_string": style_string,
            "graphic_layout_card": graphic_layout_card,
            "scene_groups": scene_groups_raw,
        }
    except Exception as e:
        logger.warning("全片锚定卡生成失败（%s）——跳过注入，分块逻辑按现状执行（不更坏）", e)
        return None


# T6（2026-08-18 两反馈批 P-05/拍板①）：普通任务生产分组器。修前唯一 SceneGroup
# 生产器 course_pipeline.script_to_storyboard 在 app/ 内零调用（仅 tests），普通任务
# scene_groups 恒空 → T-S1「同一分组」对 LLM 无指代、T-S2 三重死角之一。
def _deterministic_scene_groups(n: int) -> list[SceneGroup]:
    """确定性兜底分组：按 CHUNK 对齐切块（与 _fill_image_prompts_robust 块边界同源，
    T-S1 的组边界与块边界一致）；≤8 帧单块任务天然一组。标题「段落 N」沿用
    course_pipeline.script_to_storyboard 的既有先例。origin="planner" 供 worker
    v3 栅栏（worker_probe.should_use_segmented_pipeline）判别。"""
    return [
        SceneGroup(
            index=gi,
            title=f"段落 {gi + 1}",
            frame_indices=list(range(s, min(s + CHUNK, n))),
            origin="planner",
        )
        for gi, s in enumerate(range(0, n, CHUNK))
    ]


def _scene_groups_from_anchor(anchor: dict | None, n: int) -> list[SceneGroup] | None:
    """从锚定调用结构化输出解析场景分组；缺失/无效返回 None（调用方走确定性兜底）。

    只信 start（起点序）：起点集合即可重建连续覆盖 [0, n)——LLM 漏给 start=0 时
    首个起点上收到 0（头部帧并入首组，不造无标题的隐式头组）。单帧组归并（T-S2
    对单帧组无意义——组首即全组，锚定永不发生）：中段并入前一组、首组并入后一组。
    bool 是 int 子类，显式排除（True 会被当 1）。
    """
    if not isinstance(anchor, dict):
        return None
    raw = anchor.get("scene_groups")
    if not isinstance(raw, list) or not raw:
        return None
    by_start: dict[int, str] = {}
    for e in raw:
        if not isinstance(e, dict):
            continue
        start = e.get("start")
        if isinstance(start, bool) or not isinstance(start, int) or not (0 <= start < n):
            continue
        title = str(e.get("title", "") or "").strip()
        by_start.setdefault(start, title)
    if not by_start:
        return None
    starts = sorted(by_start)
    if starts[0] != 0:
        # 首组起点上收到 0：头部帧并入 LLM 给出的首组（保留其标题）
        by_start[0] = by_start.pop(starts[0])
        starts[0] = 0
    # 相邻边界切段 → SceneGroup；标题缺失回落「段落 N」
    segs: list[list] = []  # [start, end, title]
    for i, s in enumerate(starts):
        e = starts[i + 1] if i + 1 < len(starts) else n
        if e > s:
            segs.append([s, e, by_start.get(s) or f"段落 {len(segs) + 1}"])
    # 单帧组归并：中段并入前一组（延伸其 end）
    merged: list[list] = []
    for seg in segs:
        if seg[1] - seg[0] == 1 and merged:
            merged[-1][1] = seg[1]
        else:
            merged.append(seg)
    # 首组单帧并入后一组（保后组标题——主体段语义更准）；全任务单帧保留
    if len(merged) > 1 and merged[0][1] - merged[0][0] == 1:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return [SceneGroup(index=gi, title=str(seg[2]), frame_indices=list(range(seg[0], seg[1])), origin="planner") for gi, seg in enumerate(merged)] or None


def _slice_scene_groups_for_chunk(groups: list[SceneGroup], offset: int, chunk_len: int) -> list[SceneGroup]:
    """T6：全片 scene_groups 重基到块内局部序号（与块 frame 重编号同口径），供
    _generate_course_prompts_enhanced 的【组标题】分段格式化消费——T-S1 组级锚定
    指令由此获得真实指代（组边界跨块也可见）。块内无组员的组自然消失。"""
    sliced: list[SceneGroup] = []
    for g in groups:
        local = [fi - offset for fi in g.frame_indices if offset <= fi < offset + chunk_len]
        if local:
            sliced.append(SceneGroup(index=len(sliced), title=g.title, frame_indices=local))
    return sliced


def _unify_ir_style_slots(frames: list, style_string: str) -> None:  # noqa: ANN001
    """T5 确定性后处理：所有帧 visual_ir.style 槽统一覆写为锚定卡风格串。

    P-06 实物：default 风格下 IR style 槽逐块自由发明（块1「电影级高科技赛博微光」
    → 块2「未来主义三维网络科技·深暗蓝霓虹青」…）。锚定卡指令 LLM 可能不听——
    此处在**规划期汇总点**（smooth_camera_continuity 同款「全量收敛」位）把风格槽
    钉死。只覆写已有 dict 的 style 键：visual_ir=None 的帧不动（不凭空造值，
    legacy 透传不变量）。单帧重生端点**不得**调用本函数（评审 b：该端点已有
    style 硬前置，叠加覆写=双重风格词）。
    """
    for f in frames:
        ir = getattr(f, "visual_ir", None)
        if isinstance(ir, dict):
            ir["style"] = style_string


# ══ 镜头调度表（2026-08-26 计划 v2.2：两阶段提示词生成）══════════════════
# E8 逐帧化的回归：批量 8 合 1 的真正价值是**一次调用联合设计镜头调度**；逐帧并发下
# 每个请求看不到其他帧的构图产出，连续镜头人物位置/机位逐帧独立抽签（真片鬼畜）。
# 阶段 1 = _plan_shot_table 一次轻量调用产出全片调度表（输出 ~1/10 批量全文量级，
# 远离超时线）；阶段 2 = 逐帧扩写注入「本帧行 + ±1 邻行 + 硬指令」（见
# _fill_image_prompts_per_frame），shot_size/camera_move 从表确定读取。

# AC6 降级留痕 kind（degradations.record_degradation 收任意 kind 字符串；常量置本
# 模块而非 degradations.py——本计划白名单不含该文件）。
KIND_SHOT_TABLE_FALLBACK = "shot_table_fallback"

_SHOT_TABLE_SIZES = ("wide", "medium", "close")
_SHOT_TABLE_MOVES = ("static", "push_in", "pull_out", "pan", "tilt")

# 枚举缺口修复（2026-09-01，vg_775294e3b0f5 实锤）：模型系统性输出合法运镜的
# **方向细分变体**（pan_left/pan_right/tilt_up/tilt_down——62 行表 attempt1 21 行 /
# attempt2 25 行，全是这类），旧逻辑把它们计入 20% 失控阈值分子 → 整表连坐拒绝 →
# 降级逐帧 → graphic 推荐塌缩（调度表是全片推荐主力通道）。这些是语义可无损归一
# 的近邻值（≠ fade_out 类真幻觉）：确定性映射到 canonical 枚举后**不进阈值分子**；
# 归一后仍非法的才照旧计数/安全映射/整表拒。词条键经 _norm_enum_token 归一
# （大小写/连字符/空格同权：Pan-Left / PAN LEFT → pan_left）。
_SHOT_SIZE_ALIASES = {
    "extreme_wide": "wide",
    "ultra_wide": "wide",
    "establishing_shot": "wide",
    "establishing": "wide",
    "medium_shot": "medium",
    "mid_shot": "medium",
    "waist_shot": "medium",
    "close_up": "close",
    "closeup": "close",
    "extreme_close_up": "close",
    "extreme_closeup": "close",
    "detail_shot": "close",
}
_CAMERA_MOVE_ALIASES = {
    "pan_left": "pan",
    "pan_right": "pan",
    "pan_up": "pan",
    "pan_down": "pan",
    "tilt_up": "tilt",
    "tilt_down": "tilt",
    "zoom_in": "push_in",
    "zoom_out": "pull_out",
    "zoomin": "push_in",
    "zoomout": "pull_out",
    "dolly_in": "push_in",
    "dolly_out": "pull_out",
    "truck_left": "pan",
    "truck_right": "pan",
    "tracking_shot": "pan",
    "static_shot": "static",
    "hold": "static",
}


def _norm_enum_token(value: str) -> str:
    """枚举词条归一化键：strip + 小写 + 连字符/空格 → 下划线（Pan-Left 同 pan_left）。"""
    return value.strip().lower().replace("-", "_").replace(" ", "_")


_SHOT_TABLE_SYSTEM = (
    "你是专业的视频导演。请通读全部旁白与场景分组，为整支视频一次设计「镜头调度表」——"
    "全片每一帧一行，作为后续逐帧画面扩写的镜头依据（后续每帧扩写只能填画面细节，"
    "景别/机位/人物位置与视觉类型 visual_source 以本表为准）。\n\n"
    "调度原则：\n"
    "1. 同一场景组内：人物/主体在画面中的位置与朝向保持连续承接（上一镜在左就在左，"
    "变化须有动机），机位变化平滑，不得逐帧随机跳变；跨场景组才换场。\n"
    "2. 景别按叙事节奏设计：开场/新组首帧用 wide 建立环境，讲解主体用 medium，"
    "强调细节或情绪用 close；相邻帧景别有变化但不成机械循环。\n"
    "3. 相邻两镜不重复同一种运动运镜（static 连续合法）；不要连续相反运镜（推近紧接拉远）。\n"
    "4. subject_position 写人物/主体在画面中的明确位置与朝向（如「画面左三分之一处，侧身面向右」）；"
    "composition 写构图与画面元素的空间关系；continuity 写与前后镜的承接要点。\n"
    "5. visual_source 按全片内容节奏分配：数据/流程/要点密集段选 graphic，场景与动态段选主引擎；"
    "不要求均匀，也禁止全片只选一种。\n\n"
    # ── visual_source 路由判定（2026-08-31 路由并入调度表计划）──
    # 自 _generate_course_prompts_enhanced 的 routing_instruction **逐字移入**（防措辞
    # 漂移），仅删去 frames 数组/image_prompt 相关表述（调度表不产 image_prompt）。
    # 根因：E8 逐帧契约下 LLM 无帧间协调抓手，graphic 推荐暴跌（同稿组批量 4/8 →
    # 逐帧 0/8 实测）——并入本表即恢复全片视野规划，零新增 LLM 调用。
    "visual_source 路由（同一视频内视觉类型应**多样**，禁止全片只选一种；数据/流程/要点密集段优先 graphic，场景与动态段优先主引擎）。\n"
    "graphic 准入门槛——不满足任一条就不要选 graphic（回到主引擎）：\n"
    "- 旁白强调一个具体数字/百分比/倍数 → graphic（画面中呈现醒目图内文字）。\n"
    "  例：『从350亿美元攀升到830亿美元』『涨幅达到137%』『先来看第一个关键数据：平均售价…』——旁白念出具体数字且该镜围绕数字展开，就选 graphic。\n"
    "- 旁白描述 ≥3 步的先后流程 → graphic（画面为带序号标签的分步图示）\n"
    "- 旁白归纳 ≥2 条并列要点 → graphic（画面含标题与关键词标签）\n"
    "反例（必须选主引擎，不要上卡）：单句陈述、概念解释、过渡句、引出话题；只有一个要点的「伪归纳」。\n"
    "拿不准时的倾向（2026-08-17 零路由校准）：旁白念出具体数字且围绕它展开 → 宁可选 graphic；普通场景陈述 → 宁可回主引擎。\n"
    "路由细则：\n"
    "- 数据/统计强调（百分比、倍数、金额等具体数字）→ graphic。例：旁白「转化率提升了37%」→ visual_source=graphic（渲染为大数字卡）\n"
    "- 过程/步骤（有先后顺序的操作流程）→ graphic。例：旁白「先预热，再倒入面糊，最后翻面」→ visual_source=graphic（渲染为步骤卡）\n"
    "- 概念定义、大段理论要点归纳 → graphic 或 {vt}（并列要点多时选 graphic，渲染为要点卡）\n"
    "- 真实案例、具体场景、人物、环境 → {vt}\n"
    "- 动态演示、过程演变、镜头运动 → {vt}\n"
    "visual_source 只能从这两个值中选择: {vt}, graphic\n\n"
    "语言锁定：subject_position/composition/continuity 必须使用与旁白相同的语言。\n"
    '严格输出 JSON：{{"shot_table": [{{"frame_index": 0, "shot_size": "wide|medium|close", '
    '"camera_move": "static|push_in|pull_out|pan|tilt", "visual_source": "{vt}|graphic", '
    '"subject_position": "...", "composition": "...", "continuity": "..."}}]}}，'
    "shot_table 必须精确包含 {n} 行，frame_index 从 0 连续递增到 {n_last}，与输入帧一一对应，"
    "不得遗漏、不得合并、不得提前停止。不要输出其他任何文字。"
)


def _parse_shot_table_rows(content: str, n: int, *, main_engine: str, allowed_sources: tuple[str, ...] | None = None) -> list[ShotDirection]:
    """解析 + 校验调度表 LLM 输出（条数对齐 + 枚举域 + 帧号连续唯一）——分级语义。

    - **整表拒绝**（抛异常由调用方重试/降级）：条数不对齐 / frame_index 非法或
      不连续唯一（帧号错位无法安全映射）/ 行非对象 / 非法枚举**映射行 > 20%**
      （阈值外是模型失控，硬用比无表更糟——被注入的帧与零注入帧混排）。
    - **行级容错**（2026-09-01 长表稳定性）：非法 shot_size → 安全映射 ``medium``、
      非法 camera_move → ``static``（保守值），行数保持完整——82 帧任务第 77 行
      ``camera_move='fade_out'`` 连坐 81 行正确输出是零图文镜事故的第一环。
      映射行计数只含 shot_size/camera_move（visual_source 归一不进分子分母——
      那是既定放宽，性质不同）。
    - **别名归一**（2026-09-01 枚举缺口修复）：pan_left/tilt_up/zoom_in 等方向
      细分变体先经别名表确定性映射到 canonical 枚举（不进 20% 失控分子）——
      vg_775294e3b0f5 实锤 62 行表 21~25 行全是这类，旧逻辑整表误拒。
      归一后仍非法的才走上面的安全映射并计数。
    - ``visual_source``（2026-08-31 路由并入）：缺失/非法值只归一**该行** None
      （其余字段保留，不连坐整表）；None 行的帧不动，回退逐帧推荐结果。
      ``main_engine`` = 任务主引擎（合法值域 {main_engine, graphic}）。
    """
    parsed = json.loads(_strip_markdown_fence(content))
    if not isinstance(parsed, dict):
        raise ValueError(f"shot table output is {type(parsed).__name__}, expected object")
    rows = parsed.get("shot_table")
    if not isinstance(rows, list) or len(rows) != n:
        raise ValueError(f"shot table has {len(rows) if isinstance(rows, list) else '?'} rows, expected {n}")
    out: list[ShotDirection] = []
    seen: set[int] = set()
    n_mapped = 0  # 非法枚举映射行数（20% 阈值分子——只数 shot_size/camera_move 映射）
    n_aliased = 0  # 别名归一处数（方向细分变体→canonical；确定性语义映射，不进失控分子）
    for r in rows:
        if not isinstance(r, dict):
            raise ValueError("shot table row is not an object")
        fi = r.get("frame_index")
        if isinstance(fi, bool) or not isinstance(fi, int) or not (0 <= fi < n) or fi in seen:
            raise ValueError(f"shot table row has invalid frame_index {fi!r}")
        seen.add(fi)
        ss = str(r.get("shot_size") or "").strip()
        cm = str(r.get("camera_move") or "").strip()
        # 2026-09-01 两级容错：①别名归一（pan_left/tilt_up 类方向细分变体 →
        # canonical，不计数——语义无损的确定性映射）；②归一后仍非法才安全映射
        # （medium/static）并计入 20% 失控阈值（fade_out 类真幻觉照旧整表拒）。
        _row_mapped = False
        if ss not in _SHOT_TABLE_SIZES:
            _alias = _SHOT_SIZE_ALIASES.get(_norm_enum_token(ss))
            if _alias:
                ss = _alias
                n_aliased += 1
            else:
                logger.warning("shot table row %d: 非法 shot_size %r → 映射 medium", fi, ss)
                ss = "medium"
                _row_mapped = True
        if cm not in _SHOT_TABLE_MOVES:
            _alias = _CAMERA_MOVE_ALIASES.get(_norm_enum_token(cm))
            if _alias:
                cm = _alias
                n_aliased += 1
            else:
                logger.warning("shot table row %d: 非法 camera_move %r → 映射 static", fi, cm)
                cm = "static"
                _row_mapped = True
        if _row_mapped:
            n_mapped += 1
        vs = str(r.get("visual_source") or "").strip()
        if vs not in (allowed_sources or (main_engine, "graphic")):
            vs = ""  # 缺失/非法 → 归一 None（行级放水，不连坐整表）
        # remotion_course（T6）：layout 版式意图列——非法枚举剔除（行级 None，不连坐）
        layout = str(r.get("layout") or "").strip()
        if layout not in REMOTION_LAYOUTS:
            layout = ""
        out.append(
            ShotDirection(
                frame_index=fi,
                shot_size=ss,
                camera_move=cm,
                subject_position=str(r.get("subject_position") or "").strip(),
                composition=str(r.get("composition") or "").strip(),
                continuity=str(r.get("continuity") or "").strip(),
                visual_source=vs or None,
                layout=layout or None,
            )
        )
    # 20% 阈值兜底：阈值内是个别漂移（映射可救），阈值外是系统性失控（整表拒绝）。
    if n_mapped > n * 0.2:
        raise ValueError(f"shot table has {n_mapped}/{n} rows with out-of-enum shot_size/camera_move (>20%) — whole table rejected")
    if n_mapped:
        # 汇总一行（计划拍板，与逐行 warning 并存）：补总量视角——逐行保留供
        # 帧级定位，82 行表只看逐行刷屏不知道总量，汇总行是运维定位入口。
        logger.info("shot table: 映射 %d 行非法枚举 / 共 %d 行", n_mapped, n)
    if n_aliased:
        # 别名归一汇总（非异常，info）：方向细分变体是模型正常表达倾向，归一是
        # 确定性映射——留痕供运维对照「归一后表存活」的量级。
        logger.info("shot table: 归一 %d 处方向细分变体（pan_left/tilt_up 类 → canonical，不计失控阈值）/ 共 %d 行", n_aliased, n)
    out.sort(key=lambda r: r.frame_index)
    return out


async def _plan_shot_table(llm, storyboard: Storyboard, style_prefix: str) -> list[ShotDirection] | None:  # noqa: ANN001
    """S1 阶段 1：一次轻量 LLM 调用产出全片调度表（N 帧 N 行）。

    契约：条数对齐 + 枚举域 + 帧号连续唯一（见 _parse_shot_table_rows）；
    JSON 解析失败/不齐 → 重试 1 次；二次失败 → None（AC6：降级现状逐帧，
    调用方留 sidecar 留痕）。模型 = 调用方传入的 llm（生产 = script LLM，
    即 video_gen.script_model；S5 shot_table_model 可换独立实例）。

    超时按帧数缩放（AC2 实证修复，2026-08-28）：deepseek-v4-flash 实测 ≈3.6s/行，
    30 帧任务在默认 120s 下两次超时必然降级（真实任务日志实证）——本函数按
    ``min(900, max(180, n*8))`` 放大上限（30 帧→240s，96 帧→768s）。计划 AC4 的
    「80 帧 ≈15~30s」估计作废，以实测为准。2026-09-08 复核：deepseek-v4-flash-
    vision-exp 实测 ≈0.71s/行（30行调度表 21.4s，10/10 契约校验全过），8s/行公式
    裕度 ≥11 倍。2026-10-12 迁移注记：script_model 迁 qwen3.8-flash（deepseek
    删册；09-08 实测备选位 30行调度表 33.3s≈1.11s/行）——公式裕度仍 ≥7 倍，
    维持不重校准，部署后首周随 R7 观察期复核分镜耗时分布。
    """
    n = len(storyboard.frames)
    if n == 0:
        return None
    timeout = min(900.0, max(180.0, n * 8.0))
    # vt = 任务主引擎：visual_source 枚举随任务动态收窄（ai_video|graphic /
    # ai_image|graphic，沿用 D8 提示层收窄口径——调用方已门控仅 ai_image/ai_video 进来）
    _vt_raw = storyboard.config.video_type
    # remotion_course（T4/T6）：主引擎标签 = ai_image（visual_source 三值枚举见 addon）
    _vt = "ai_image" if _vt_raw == "remotion_course" else _vt_raw
    system = _SHOT_TABLE_SYSTEM.format(n=n, n_last=n - 1, vt=_vt)
    _allowed: tuple[str, ...] | None = None
    if _vt_raw == "remotion_course":
        system += (
            "本片为课程视频（remotion 版式渲染）：visual_source 可从 ai_image、ai_video、graphic 三个值中选择"
            "（动态演示段可给 ai_video，数据/流程/要点段给 graphic，其余给 ai_image）\n"
            "并额外输出 layout 字段（版式意图，课程视频画面版式），枚举值只能从以下选择: " + ", ".join(REMOTION_LAYOUTS) + "\nlayout 按旁白内容选：封面/开场→CoverPage，章节切换→ChapterDivider，数据大数字→ChartPage，"
            "步骤流程→ProcessPage，要点罗列→ConceptPage，金句强调→QuotePage，案例→CasePage，小结→SummaryPage，"
            "思考互动→QuizPage，对比→ComparisonPage，代码→CodePage，目标→ObjectivesPage，大纲→OutlinePage，"
            "结尾→EndingPage/EndCard，其余→ImageTextLeft/ImageTextRight/FullVisualPage\n"
        )
        _allowed = ("ai_image", "ai_video", "graphic")
    if storyboard.scene_groups:
        groups_payload = [
            {
                "title": g.title,
                "frames": [{"frame_index": i, "narration": storyboard.frames[i].narration} for i in g.frame_indices if i < n],
            }
            for g in storyboard.scene_groups
        ]
    else:
        groups_payload = [{"title": "", "frames": [{"frame_index": i, "narration": f.narration} for i, f in enumerate(storyboard.frames)]}]
    user = json.dumps(
        {"title": storyboard.title, "style_prefix": style_prefix, "total_frames": n, "scene_groups": groups_payload},
        ensure_ascii=False,
    )
    # 2026-09-01 长表稳定性：本次调用单独降温（bind 0.2，不动全局 script LLM 的
    # 0.7）——长结构化输出（82 行×7 列）在 0.7 下中途漂移（char 14268 malformed
    # 实证；max_tokens 32768 不是瓶颈）。hasattr 防御 = 既有测试桩 _LLM 只有
    # ainvoke（窄签名桩保护惯例），无 bind 时温度静默不生效（桩不产出真实漂移）；
    # bind 是 langchain Runnable 标准方法，PatchedChatOpenAI 继承 ChatOpenAI 兼容
    # （生效性经实网探针验收，见 changelog-2026-09-01）。
    structured_llm = llm.bind(temperature=0.2) if hasattr(llm, "bind") else llm
    last_err = ""
    for attempt in (1, 2):
        try:
            # T3（2026-09-01）：attempt≥2 的 user 追加上次错误摘要（自我纠正）——
            # JSON malformed/枚举越界对模型不可见，原样重发大概率重演（82 帧事故
            # 第二环：attempt2 同样断裂）。摘要截 200 字防爆 token。
            user_payload = user
            if attempt >= 2 and last_err:
                user_payload = f"{user}\n\n上次输出未通过校验：{last_err[:200]}。请严格按枚举域与 JSON 格式重新输出全部 {n} 行。"
            resp = await _invoke_llm_bounded(structured_llm, [{"role": "system", "content": system}, {"role": "user", "content": user_payload}], timeout=timeout)
            content = resp.content if hasattr(resp, "content") else str(resp)
            rows = _parse_shot_table_rows(content, n, main_engine=_vt, allowed_sources=_allowed)
            logger.info("镜头调度表已生成（%d 行）", len(rows))
            return rows
        except Exception as e:
            last_err = str(e)
            logger.warning("shot table attempt %d failed: %s", attempt, e)
    return None


def _sd_row_dict(r) -> dict:  # noqa: ANN001
    """ShotDirection 或原始 dict 行 → 注入用 dict（S2 注入/S3 插帧共用形态）。"""
    if isinstance(r, dict):
        fi = r.get("frame_index")
        return {
            "frame_index": fi,
            "shot_size": r.get("shot_size"),
            "camera_move": r.get("camera_move"),
            "subject_position": r.get("subject_position", ""),
            "composition": r.get("composition", ""),
            "continuity": r.get("continuity", ""),
        }
    return {
        "frame_index": r.frame_index,
        "shot_size": r.shot_size,
        "camera_move": r.camera_move,
        "subject_position": r.subject_position,
        "composition": r.composition,
        "continuity": r.continuity,
    }


def shot_direction_block(rows, frame_index: int) -> dict | None:  # noqa: ANN001
    """S2/S3 共用：本帧行 + ±1 邻行（存在才含）。无表/无本帧行 → None（零注入降级）。

    rows 兼容 list[ShotDirection]（规划内存形态）与 list[dict]（storyboard.json
    原始形态——插帧行级生成传 ±2 邻行时是 dict）。
    """
    if not rows:
        return None
    by_idx: dict[int, object] = {}
    for r in rows:
        fi = r.get("frame_index") if isinstance(r, dict) else getattr(r, "frame_index", None)
        if isinstance(fi, int) and not isinstance(fi, bool):
            by_idx[fi] = r
    cur = by_idx.get(frame_index)
    if cur is None:
        return None
    blk: dict = {"current": _sd_row_dict(cur)}
    if frame_index - 1 in by_idx:
        blk["prev"] = _sd_row_dict(by_idx[frame_index - 1])
    if frame_index + 1 in by_idx:
        blk["next"] = _sd_row_dict(by_idx[frame_index + 1])
    return blk


# S2 硬指令（计划写死：随调度行注入 user 负载，system 侧零改动）。
_SHOT_DIRECTIVE = (
    "调度指令（最高优先级）：shot_direction 是全片联合设计的镜头调度表，必须严格遵守——"
    "本镜的景别（shot_size）、机位（camera_move）与人物位置（subject_position）一律以 current 行为准，"
    "你只负责扩写画面细节（环境、动作、光影、氛围），不得改换景别/机位/人物站位；"
    "prev/next 是相邻镜的调度，用于保持人物位置与机位承接（人物站位跨镜连续，不得随机跳变）。"
)


def _apply_shot_table_fields(frames: list, rows, *, protect: frozenset[int] | set[int] = frozenset()) -> None:  # noqa: ANN001
    """S2 汇总点覆写：有行帧的 shot_size/camera_move ← 调度表（不再采信逐帧返回值）。

    覆盖兜底帧（LLM 失败走主题模板的帧 mini 无返回）——调度表的价值正在于
    与逐帧 LLM 合作度解耦。无行帧不动（插帧待生成行的零噪音降级态）。

    2026-08-31 路由并入：行 ``visual_source`` 非 None 时同样覆写
    ``frame.visual_source``（表覆写语义，与 shot_size/camera_move 同口径）；
    None 行不动（该行未产出路由列 → 帧保持逐帧推荐结果/现状值）。
    rows 兼容 dict（storyboard.json 原始形态，存量行无 visual_source 键 →
    None → 不动，只读兼容）。
    ``protect``（2026-09-01 审查 #1）：用户手改帧索引集（plan_course 规划前
    快照）——三列（shot_size/camera_move/visual_source）同豁免。P0-5 不变量
    「用户手改帧不被任何规划产物覆盖」覆盖表覆写；默认空集 = 既有调用方/测试
    零变化。
    """
    by_idx: dict[int, object] = {}
    for r in rows:
        fi = r.get("frame_index") if isinstance(r, dict) else getattr(r, "frame_index", None)
        if isinstance(fi, int) and not isinstance(fi, bool):
            by_idx[fi] = r
    for f in frames:
        r = by_idx.get(f.index)
        if r is None:
            continue
        if f.index in protect:
            continue  # P0-5：用户手改帧不被表覆写（三列同豁免）
        if isinstance(r, dict):
            f.shot_size, f.camera_move = r.get("shot_size"), r.get("camera_move")
            vs = r.get("visual_source")
            lo = r.get("layout")
        else:
            f.shot_size, f.camera_move = r.shot_size, r.camera_move
            vs = getattr(r, "visual_source", None)
            lo = getattr(r, "layout", None)
        if vs:
            f.visual_source = vs
        # remotion_course（T6/D14）：planner 版式 hint 只落空位——帧已有值（用户手改）
        # 不覆写，用户选择天然胜出
        if lo and getattr(f, "layout", None) is None:
            f.layout = lo


_SHOT_ROW_SYSTEM = (
    "你是专业的视频导演。视频已有全片镜头调度表，现**插入了一帧新旁白**，请为这一帧补一行调度，"
    "使它与前后相邻镜的人物位置与机位自然承接（人物站位连续，不随机跳变；景别与邻镜形成节奏）。\n"
    '严格输出 JSON：{"shot_size": "wide|medium|close", "camera_move": "static|push_in|pull_out|pan|tilt", '
    '"subject_position": "...", "composition": "...", "continuity": "..."}，'
    "只输出这一个 JSON 对象，不要输出其他任何文字。"
)


async def generate_shot_row_for_frame(
    llm,  # noqa: ANN001
    *,
    title: str,
    narration: str,
    prev_rows: list | None = None,
    next_rows: list | None = None,
    anchor_card: str = "",
    style_prefix: str = "",
) -> dict | None:
    """S3 插帧行级调度行生成（±2 邻行 + 新帧旁白 + 锚定卡，恰好 1 次 LLM 调用）。

    返回行**不带 frame_index**（调用方盖戳插帧位置）；坏输出 → None（不重试不阻断
    插帧——新帧无行走零注入降级，该帧镜头语言沿用逐帧返回值）。"""
    try:
        user = json.dumps(
            {
                "title": title,
                "style_prefix": style_prefix,
                "new_frame_narration": narration,
                "prev_neighbor_rows": [_sd_row_dict(r) for r in (prev_rows or [])],
                "next_neighbor_rows": [_sd_row_dict(r) for r in (next_rows or [])],
                "anchor_card": anchor_card,
            },
            ensure_ascii=False,
        )
        resp = await _invoke_llm_bounded(llm, [{"role": "system", "content": _SHOT_ROW_SYSTEM}, {"role": "user", "content": user}])
        content = resp.content if hasattr(resp, "content") else str(resp)
        parsed = json.loads(_strip_markdown_fence(content))
        if not isinstance(parsed, dict):
            raise ValueError(f"shot row output is {type(parsed).__name__}")
        ss = str(parsed.get("shot_size") or "").strip()
        cm = str(parsed.get("camera_move") or "").strip()
        if ss not in _SHOT_TABLE_SIZES or cm not in _SHOT_TABLE_MOVES:
            raise ValueError(f"shot row out-of-enum: {ss!r}/{cm!r}")
        return {
            "shot_size": ss,
            "camera_move": cm,
            "subject_position": str(parsed.get("subject_position") or "").strip(),
            "composition": str(parsed.get("composition") or "").strip(),
            "continuity": str(parsed.get("continuity") or "").strip(),
        }
    except Exception as e:
        logger.warning("inserted-frame shot row generation failed: %s（新帧无行，走零注入降级）", e)
        return None


# 2026-08-12（prompt-optimization-v1 T3）：从「氛围象征」模板改为「解释性视觉」模板。
# 旧版要求「刻画场景、氛围与象征元素」——概念艺术图指令，对科普片恰好相反（画面空洞）。
# 新版：实体锚定 + 抽象具象化 + 无文字示意图通道 + ≥2 信息层 + 分层结构。
# ⚠️ JSON 键名 image_prompts / video_prompts 是与 _parse_json_prompts 的契约，**不可更改**。
# D1（2026-08-19 拍板）：图片侧画面文字策略统一为「允许教学性文字」——修前
# 单帧兜底/重生路径禁文字（「全程无字幕无任何文字」），与 bulk 主路径不一致；
# ai_video 视频侧维持禁文字（独立烧字幕+视频文字乱码更重，见 _VIDEO_PROMPT_SYSTEM）。
_TEXT_POLICY_INSTRUCTION = (
    "画面文字策略：当帧内容包含数字、清单、步骤、对比或关键术语等教学要点时，可在画面中安排简洁、清晰、无错别字的中文文字元素（如标题、标注、图表标签），文字内容必须与旁白一致；非教学重点帧不必强行添加文字；禁止任何水印与平台标识。"
)
_IMAGE_PROMPT_SYSTEM = (
    "你是专业的视觉创意设计师。为每一条旁白创作一条详细的画面描述（100-180 字），\n"
    "用解释性视觉手法将旁白内容转化为具象画面。"
    # 审查偏离项 2（2026-08-19）：单帧兜底/重生 system 由绝对「中文」改为旁白相对锚
    # （拍板④），措辞与块级 language_instruction 同源；长度口径不动（中文稿不变）。
    "语言锁定：image_prompt 必须使用与旁白相同的语言（旁白中文→中文，旁白英文→英文），全套描述逐帧语言一致，严禁中英混用。\n\n"
    "画面分层结构（每条描述都应覆盖）：\n"
    "1. 主体：画面中直接呈现旁白提到的具体实体或过程。旁白里的名词必须变成看得见的画面元素（实体锚定）。\n"
    "   抽象概念（如免疫力、复利、光合作用）必须用具象场景落地，例如「免疫细胞战斗」画成"
    "白细胞包裹病原体的微观场景，细胞膜质感清晰。\n"
    "2. 构图：安排主体与环境的空间关系，可使用剖面/对比/流程等示意图构图——"
    "用纯图形元素表达（不用文字标注），模型擅长视觉示意而非文字标注。\n"
    "3. 光线与色彩：指定光源方向、色调氛围、材质质感。\n\n"
    "信息量要求：每镜至少两个视觉信息层（主体 + 关系/环境/细节），示意图构图优先于空洞场景。\n"
    f"{_TEXT_POLICY_INSTRUCTION}\n"
    "整套描述风格统一（统一的写实/插画风格与色调基调），各条之间不跳跃。\n"
    '严格输出 JSON：{"image_prompts": ["描述1", "描述2", ...]} 每条旁白对应一条描述，顺序一致；返回条数必须与输入旁白条数完全一致，不得提前停止、不得合并、不得遗漏。不要输出其他任何文字。'
)

# 2026-08-12（prompt-optimization-v1 T4）：解释性视觉 + 强制主体动作层 + 运镜分离 +
# 环境动态层。旧版只泛写「突出动态、镜头运动与动作」——「画面不动」的提示词层根因。
_VIDEO_PROMPT_SYSTEM = (
    "你是专业的视频创意设计师。为每一条旁白创作一条详细的视频生成描述（150-250 字），\n"
    "用解释性视觉手法将旁白内容转化为有动态的具象画面。"
    "语言锁定：video_prompt 必须使用与旁白相同的语言（旁白中文→中文，旁白英文→英文），全套描述逐帧语言一致，严禁中英混用。\n\n"
    "画面分层结构（每条描述都必须覆盖以下各层）：\n"
    "1. 主体动作（强制）：每镜必须明确写出「主体在做什么具体动作」。旁白里的名词必须变成看得见的、正在运动的实体（实体锚定）。\n"
    "   抽象概念（如免疫力、复利、光合作用）必须用具象动态场景落地，例如「免疫细胞战斗」画成"
    "白细胞缓缓包裹并吞噬病原体的微观动态过程。\n"
    "2. 运镜（与动作分离）：每条画面只用一个运镜（如缓慢推近/横向摇移/固定机位），\n"
    "   运镜描述与主体动作分开写，避免快速或剧烈运动。\n"
    "3. 环境动态：描述光影变化、微尘飘浮、背景元素的细微动态，营造时间流逝感。\n"
    "4. 构图与光线：安排主体与环境的空间关系，指定光源方向、色调氛围、材质质感。\n\n"
    "信息量要求：每镜至少两个视觉信息层（主体动作 + 环境变化/构图细节）。\n"
    "纯净要求：画面保持纯净，全程无字幕、无水印、无标识、无任何文字；"
    "避免选择天然携带文字的道具作为画面主体（如白板、海报、屏幕、桌牌、书籍封面）。\n"
    "整套描述风格统一，各条之间不跳跃。\n"
    '严格输出 JSON：{"video_prompts": ["描述1", "描述2", ...]} 每条旁白对应一条描述，顺序一致；返回条数必须与输入旁白条数完全一致，不得提前停止、不得合并、不得遗漏。不要输出其他任何文字。'
)


def _positive_only(text: str) -> str:
    """剔除含否定/禁用措辞的分句，只留正向描述。

    白熊效应（见文件头 :33-35 既定原则：负向引导无效，T1/T2 因此删负向后缀）——
    生图模型不理解否定，「禁止天平/齿轮」入 prompt 反而提示生成这些概念。锚定卡的
    禁用清单段（_ANCHOR_SYSTEM 要求 LLM 产出）注给 LLM system 没问题（LLM 懂否定），
    但直喂生图模型前必须剥掉。用于 _topic_fallback_prompt 的锚定卡嵌入。
    """
    parts = re.split(r"[，。；\n;]", text or "")
    kept = [p.strip() for p in parts if p.strip() and not any(w in p for w in ("禁止", "禁用", "不得", "避免", "切勿", "不要"))]
    return "，".join(kept)


def _topic_fallback_prompt(storyboard: Storyboard, style_prefix: str = "") -> str:
    """LLM 全兜底失败时的最后防线：主题写实场景 prompt（绝不回退 narration 原文）。

    QP1-6：narration 直发 seedance/生图 = 画面词退化根因（170 分镜事故）。
    T5（2026-08-17 H3 修复批）：①ai_video 任务追加动作+镜头语（vg_725bd6893311
    事故 4 帧实证：纯静态提示词 -> 成片全程无动作）；②标题压平空白（事故实测
    带换行的完整 title 被嵌进「」内）。零 call-site 改动（4 个调用点自动分派）。
    2026-08-19（P0 兜底链风格注入）：规划期调用点（planner 内 3 处）传 style_prefix，
    有锚定卡时以锚定卡为场景骨架、实拍风格追加真人实景尾——修前本函数零风格信息，
    是 unaligned 降级帧风格断裂的最后失守点。渲染期调用点（ai_image/ai_video 最后
    防线）不传 style_prefix（无此上下文），行为与修前一致。
    2026-08-19（审查跟进）：锚定卡先经 _positive_only 剥禁用清单段、实拍尾改正向
    表述——本函数产物直喂生图模型，负向词裸注 = 白熊效应（被禁概念原词反成提示）。
    """
    title = re.sub(r"\s+", " ", (storyboard.title or "课程主题").strip())
    anchor = getattr(storyboard, "global_anchor", None) or {}
    anchor_card = _positive_only(str(anchor.get("anchor_card", "") or "").strip())
    if anchor_card:
        base = f"以「{title}」为主题的具体写实教学场景，{anchor_card}，清晰主体，柔和均匀光线，简洁构图"
    elif style_prefix:
        base = f"{style_prefix}风格，以「{title}」为主题的具体写实教学场景，清晰主体，柔和均匀光线，简洁构图"
    else:
        base = f"以「{title}」为主题的具体写实教学场景，清晰主体，柔和均匀光线，简洁构图"
    if _is_live_action_style(style_prefix):
        base += "，画面呈现真人实景行为"
    if getattr(getattr(storyboard, "config", None), "video_type", "") == "ai_video":
        return base + "，主体正做出一个缓慢而明确的动作，镜头固定或缓慢推近"
    return base


async def _generate_image_prompts(
    llm,
    narrations: list[str],
    context: dict | None = None,
    timeout: float | None = None,
    style_guard: str = "",
) -> list[str]:
    """为每条旁白生成画面描述（语言随旁白：中文稿→中文，英文稿→英文，拍板④）。

    可选 context 注入 title + 相邻旁白（单帧兜底路径使用，默认 None 时行为与现状一致）。
    ``timeout`` 透传 _invoke_llm_bounded（None = 默认 120s；T3 逐帧兜底收紧为 60s）。
    ``style_guard``（2026-08-19 P0）：_style_guard_instruction 产物，非空时前置到 system
    ——修前单帧 system 零风格信息，unaligned 降级帧裸奔出隐喻。默认空串 = 行为同现状。
    ``context["shot_direction"]``（S3，2026-08-26 调度表计划）：单帧重生路径携带
    本帧行+±1 邻行（shot_direction_block 产物）时注入 user 负载首部（与 S2 同款
    秩序+硬指令）。默认无键 = 行为同现状。
    """
    _sd = (context or {}).get("shot_direction")
    payload: dict = {}
    if _sd:
        payload["shot_direction"] = _sd
        payload["shot_directive"] = _SHOT_DIRECTIVE
    payload["narrations"] = narrations
    if context:
        if context.get("title"):
            payload["title"] = context["title"]
        adj = context.get("adjacent_narrations")
        if adj:
            payload["context"] = adj
    joined = json.dumps(payload, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": f"{style_guard}{_IMAGE_PROMPT_SYSTEM}"},
        {"role": "user", "content": joined},
    ]
    resp = await _invoke_llm_bounded(llm, messages, timeout=timeout)  # E5: 有界超时
    content = resp.content if hasattr(resp, "content") else str(resp)
    try:
        return _parse_json_prompts(content, "image_prompts")
    except (json.JSONDecodeError, IndexError, TypeError):
        logger.warning("Failed to parse image_prompts JSON from LLM response, falling back")
        return []


def _parse_json_prompts(content: str, key: str) -> list[str]:
    """Extract a list of prompts from LLM JSON output, with markdown fence handling."""
    parsed = json.loads(_strip_markdown_fence(content))
    return parsed.get(key, [])


def _strip_markdown_fence(content: str) -> str:
    """剥掉 LLM 输出可能裹的 markdown 代码栅栏（T5：锚定卡 JSON 解析复用同款处理）。"""
    json_str = content
    if "```" in json_str:
        json_str = json_str.split("```")[1]
        if json_str.startswith("json"):
            json_str = json_str[4:]
        json_str = json_str.split("```")[0]
    return json_str.strip()


async def _generate_video_prompts(
    llm,
    narrations: list[str],
    context: dict | None = None,
    timeout: float | None = None,
    style_guard: str = "",
) -> list[str]:
    """为每条旁白生成视频描述（语言随旁白，拍板④；强调主体动作 + 运镜 + 环境动态）。

    可选 context 注入 title + 相邻旁白（单帧兜底路径使用，默认 None 时行为与现状一致）。
    ``timeout`` 透传 _invoke_llm_bounded（None = 默认 120s；T3 逐帧兜底收紧为 60s）。
    ``style_guard``（2026-08-19 P0）：同 _generate_image_prompts——单帧兜底/重生路径
    的风格守卫前置到 system。默认空串 = 行为同现状。
    ``context["shot_direction"]``（S3）：同 _generate_image_prompts（调度行注入首部）。
    """
    _sd = (context or {}).get("shot_direction")
    payload: dict = {}
    if _sd:
        payload["shot_direction"] = _sd
        payload["shot_directive"] = _SHOT_DIRECTIVE
    payload["narrations"] = narrations
    if context:
        if context.get("title"):
            payload["title"] = context["title"]
        adj = context.get("adjacent_narrations")
        if adj:
            payload["context"] = adj
    joined = json.dumps(payload, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": f"{style_guard}{_VIDEO_PROMPT_SYSTEM}"},
        {"role": "user", "content": joined},
    ]
    resp = await _invoke_llm_bounded(llm, messages, timeout=timeout)  # E5: 有界超时
    content = resp.content if hasattr(resp, "content") else str(resp)
    try:
        return _parse_json_prompts(content, "video_prompts")
    except (json.JSONDecodeError, IndexError, TypeError):
        logger.warning("Failed to parse video_prompts JSON, falling back")
        return []


async def plan(llm, *, title: str, narrations: list[str], config: StoryboardConfig) -> Storyboard:
    sb = Storyboard(title=title, config=config)

    image_prompts: list[str] = []
    if config.video_type in ("ai_image", "remotion_course"):
        # remotion_course（T4）：画面词按 ai_image 语义生成（ai_video B-roll 帧复用同词）
        image_prompts = await _generate_image_prompts(llm, narrations)
    elif config.video_type == "ai_video":
        image_prompts = await _generate_video_prompts(llm, narrations)
    elif config.video_type == "remotion":
        pass  # Remotion needs no visual prompts — rendering is template-driven

    for i, narration in enumerate(narrations):
        frame = StoryboardFrame(index=i, narration=narration)
        if config.video_type in ("ai_image", "ai_video", "remotion_course"):
            frame.image_prompt = image_prompts[i] if i < len(image_prompts) else narration
        sb.frames.append(frame)
    return sb


CHUNK = 8  # M1-1: 单次"画面词生成"LLM 调用帧数上限（安全低于 10-21 变动早停点）。非分镜总数。

# E8（2026-08-21 360 主力切换）：分镜提示词逐帧化默认参数。
# - ``prompt_chunk_size``（config）：1 = 逐帧（默认，T6/T7 实测逐帧在稳定性/总耗时/
#   质量三维全胜；线上 28% 分镜失败主因是 bulk 长输出撞自定 120s 超时）；>1 保留
#   批量路径作**配置级回退**（不对用户暴露）。
# - ``prompt_frame_concurrency``（config）：逐帧并发闸（默认 8，对齐既有
#   Semaphore(8) 兜底口径）。
PROMPT_CHUNK_DEFAULT = 1
PROMPT_FRAME_CONCURRENCY_DEFAULT = 8

# 逐帧模式的附加 system 指令（E8）：解释 script_narrations / current_frame_index
# 契约——请求携带**脚本全文上下文**（title + 全部旁白 + 当前帧序号，非 ±1 简配）。
_PER_FRAME_INSTRUCTION = (
    "逐帧生成模式：narrations 字段是**当前帧**的旁白，本次只输出当前帧这一条画面描述（frames 数组恰好 1 条）；"
    "script_narrations 是全片脚本（title 一并给出），current_frame_index 是当前帧在全片中的序号（0 起）——"
    "请通读全片脚本理解叙事上下文与前后衔接（主角/场景/世界观与锚定卡保持一致），但只描述当前帧的画面，"
    "不得替其他帧输出。\n\n"
)

# T3 (2026-08-14): 逐帧兜底的并发上限 + 总量封顶 + 单帧超时。
# bulk 失败后缺失帧曾在单个无界 gather 里一次性并发（120 帧 = 120 个并发 LLM 瞬时
# 打向网关，全部等满 120s）→ Semaphore(8) 限流；总量 300s 封顶，到点取消剩余，
# 未完成帧走 _topic_fallback_prompt 最终兜底（既有 :421 语义）。不加重试：
# 单帧一次尝试 + 主题兜底已是正确分层，重试只会放大网关压力。
_FALLBACK_CONCURRENCY = 8
_FALLBACK_PER_FRAME_TIMEOUT = 60.0  # s（bulk 120s 的一半——兜底帧不该比 bulk 更慢）
_FALLBACK_TOTAL_BUDGET = 300.0  # s

# 审查 Minor 3（2026-08-14）：_plan_course_batched（>50 帧有 scene_groups）总量预算。
# 批路径的 missing 逐帧重试是**串行**且每帧 120s 有界——无总量封顶时最坏 ~2h。
# 到点停止：剩余组/帧不调 LLM，image_prompt 留空由末尾主题兜底扫尾填（QP1-6 不变量）。
_BATCHED_TOTAL_BUDGET = 300.0  # s


def _expect_aligned(prompts: list[str], expected_n: int) -> bool:
    """LLM 返回的画面描述与输入旁白数量一致才可信。

    早停（少返）/多返/乱序都不可信--单镜重试才能保证一一对应。
    空串也算"不一致"（空描述无意义且会触发后续 j 错位）。
    """
    return len(prompts) == expected_n and all(p.strip() for p in prompts)


async def _fill_image_prompts_robust(
    llm,
    storyboard: Storyboard,
    style_prefix: str,
    *,
    chunk_size: int | None = None,
    frame_concurrency: int | None = None,
    entity_roster: dict | None = None,
    task_dir: Path | None = None,
    protect: frozenset[int] | set[int] = frozenset(),
) -> None:
    """M1-1: 分块并行 bulk 生成画面词；任何 bulk 漏掉的帧用单帧兜底，绝不回退 narration。

    修前 plan_course 非分组路径把全部 N 段旁白塞进一次 _generate_course_prompts_enhanced
    调用，LLM 提前停止返短 JSON（停止点飘忽 10-21）→ :178 回退 `image_prompt=narration`
    → "#22 起画面词==旁白"。本 helper 分块并行调 bulk，单块输出小不会早停；
    仍漏的帧用单帧 _generate_image_prompts/_generate_video_prompts 兜底（单帧绝不早停）。

    E8（2026-08-21 360 主力切换）：``chunk_size``（config ``prompt_chunk_size``，
    默认 1）= 1 时走**逐帧路径**（脚本全文上下文 + 单帧输出，见
    :func:`_fill_image_prompts_per_frame`）；>1 时按该块大小走本函数的批量路径
    （配置级回退，行为同修前，仅块大小可调）。
    ``protect``（2026-09-01 审查 #1）：用户手改帧索引集——透传到收尾
    _writeback_and_smooth → _apply_shot_table_fields（P0-5 表覆写豁免）。
    """
    eff_chunk = PROMPT_CHUNK_DEFAULT if chunk_size is None else max(1, int(chunk_size))
    eff_concurrency = PROMPT_FRAME_CONCURRENCY_DEFAULT if frame_concurrency is None else max(1, int(frame_concurrency))
    _erk = {"entity_roster": entity_roster} if entity_roster else {}
    if eff_chunk == 1:
        await _fill_image_prompts_per_frame(llm, storyboard, style_prefix, concurrency=eff_concurrency, task_dir=task_dir, protect=protect, **_erk)
        return
    await _fill_image_prompts_bulk(llm, storyboard, style_prefix, chunk=eff_chunk, task_dir=task_dir, protect=protect, **_erk)


def _writeback_and_smooth(
    storyboard: Storyboard,
    prompts_by_idx: dict[int, str],
    sources_by_idx: dict[int, str],
    write_sources: bool,
    style_prefix: str,
    *,
    protect: frozenset[int] | set[int] = frozenset(),
) -> None:
    """E8：两条路径（逐帧/批量）共用的收尾——QP1-6 写回 + 运镜收敛 + IR 风格钉死。

    与修前 _fill_image_prompts_robust 尾部逐字同源（抽函数防两路径拷贝漂移）。
    2026-08-31 路由并入：有表时**不采信**逐帧返回的 visual_source（不写入）——
    路由已由调度表阶段 1 产出，交由下方 _apply_shot_table_fields 表覆写。
    ``protect``（2026-09-01 审查 #1）：透传给表覆写——用户手改帧三列同豁免
    （P0-5 不变量覆盖表覆写）。"""
    _has_table = bool(getattr(storyboard, "shot_table", None))
    for i, f in enumerate(storyboard.frames):
        f.image_prompt = prompts_by_idx.get(i) or _topic_fallback_prompt(storyboard, style_prefix)
        # content-match T5：只填 None 帧——规划前已有值 = 用户手改（P0-5 不变量），
        # LLM 推荐不得覆盖；护栏（normalize 的 protect 集）同样不触碰这些帧。
        # 有表时整段跳过（表覆写语义，见 docstring）。
        if write_sources and not _has_table and i in sources_by_idx and not storyboard.frames[i].visual_source:
            f.visual_source = sources_by_idx[i]

    # Task 9：块间并发导致无法在生成时衔接，故此处（全部块汇总后）统一收敛运镜连贯性。
    # S2（2026-08-26 调度表计划）：有表时表即连贯性设计（阶段 1 联合产出）——
    # 表覆写 shot_size/camera_move（覆盖兜底帧）且**跳过 smooth**（后处理的
    # 首镜 wide 化/打断重复运镜会覆写表的调度，违背「取自调度表」）。
    if getattr(storyboard, "shot_table", None):
        _apply_shot_table_fields(storyboard.frames, storyboard.shot_table, protect=protect)
    else:
        smooth_camera_continuity(storyboard.frames)
    # T5：IR style 槽统一覆写（规划期汇总点①）——块间并发各块自由发明风格短语
    # （P-06 实物佐证），即使 system 有锚定卡指令，LLM 不听话时风格槽仍漂移；
    # 此处用锚定卡风格串钉死。单帧重生端点不复写（评审 b：手改帧不二次覆写）。
    anchor = getattr(storyboard, "global_anchor", None)
    if anchor and anchor.get("style_string"):
        _unify_ir_style_slots(storyboard.frames, anchor["style_string"])


async def _fill_image_prompts_per_frame(
    llm,
    storyboard: Storyboard,
    style_prefix: str,
    *,
    concurrency: int,
    entity_roster: dict | None = None,
    task_dir: Path | None = None,
    protect: frozenset[int] | set[int] = frozenset(),
) -> None:
    """E8（2026-08-21）：逐帧生成——脚本全文上下文 + 单帧输出（默认路径）。

    每帧一次 ``_generate_course_prompts_enhanced`` 调用（1 帧 mini，保留镜头语言/
    IR/visual_source 全部输出契约），user 消息额外携带 ``script_narrations``（全部
    旁白）+ ``current_frame_index``（title 本就在）。降级链简化：单帧失败 → 重试
    1 次 → 主题模板兜底（QP1-6 不变量；不影响其他帧）。并发闸限同时在途帧数。
    ``protect``（2026-09-01 审查 #1）：透传收尾 _writeback_and_smooth（P0-5 表
    覆写豁免）。
    """
    import asyncio

    frames = storyboard.frames
    n = len(frames)
    video_type = storyboard.config.video_type
    explicit_type = video_type in ("ai_image", "ai_video", "remotion_course")
    write_sources = (not explicit_type) or content_routing_enabled()
    narrations = [f.narration for f in frames]
    prompts_by_idx: dict[int, str] = {}
    sources_by_idx: dict[int, str] = {}
    sem = asyncio.Semaphore(max(1, concurrency))

    async def _attempt(i: int) -> tuple[Storyboard, list[str], list[str]]:
        mini = Storyboard(
            title=storyboard.title,
            config=storyboard.config,
            frames=[StoryboardFrame(index=0, narration=frames[i].narration)],
            scene_groups=_slice_scene_groups_for_chunk(storyboard.scene_groups, i, 1),
            global_anchor=getattr(storyboard, "global_anchor", None),
        )
        # S2（2026-08-26 调度表计划）：本帧行+±1 邻行注入 user 负载首部
        # （无表/无行帧 → None 零注入，条件传参保护窄签名测试桩）。
        _sd = shot_direction_block(getattr(storyboard, "shot_table", None), i)
        # 2026-08-31 路由并入：有表时逐帧 mini 不再含路由指令（表覆写语义）。
        _settled = bool(getattr(storyboard, "shot_table", None))
        prompts, sources = await _generate_course_prompts_enhanced(
            llm,
            mini,
            style_prefix,
            # E8：脚本全文上下文（全部旁白 + 当前帧序号），非 ±1 简配。
            script_context={"narrations": narrations, "current_frame_index": i},
            extra_system=_PER_FRAME_INSTRUCTION,
            # M2：名册 + 真实帧号（空名册不传——fake_enhanced 窄签名兼容）
            task_dir=task_dir,
            index_offset=i,
            **({"entity_roster": entity_roster} if entity_roster else {}),
            **({"shot_direction": _sd} if _sd else {}),
            **({"visual_source_settled": True} if _settled else {}),
        )
        return mini, prompts, sources

    async def _one(i: int) -> None:
        # S2：有行帧的 shot_size/camera_move 由调度表确定（汇总点统一覆写，
        # 覆盖兜底帧）——不再采信逐帧返回的这两个字段（独立抽签正是病灶）。
        _covered = shot_direction_block(getattr(storyboard, "shot_table", None), i) is not None
        for attempt in (1, 2):  # E8 降级链：首试 + 重试 1 次
            try:
                async with sem:
                    mini, prompts, sources = await _attempt(i)
            except Exception as e:
                logger.warning("per-frame prompt failed (frame %d, attempt %d): %s", i, attempt, e)
                continue
            if _expect_aligned(prompts, 1):
                prompts_by_idx[i] = prompts[0]
                if write_sources and len(sources) == 1 and sources[0]:
                    sources_by_idx[i] = sources[0]
                # 镜头语言/IR/M2 出场声明搬回真实帧（mini 对齐成功才可信）
                mf = mini.frames[0]
                if mf.shot_size and not _covered:
                    frames[i].shot_size = mf.shot_size
                if mf.camera_move and not _covered:
                    frames[i].camera_move = mf.camera_move
                if mf.visual_ir:
                    frames[i].visual_ir = mf.visual_ir
                # M2：visible_*（含 None=不合作——mini 帧经 _apply_visible_fields 写过才搬，
                # 未写（无名册）时 mini/真实帧同为 dataclass 默认 None，赋值幂等）
                frames[i].visible_characters = mf.visible_characters
                frames[i].visible_props = mf.visible_props
                return
            logger.warning("per-frame prompt unaligned (frame %d, attempt %d): got %d entries", i, attempt, len(prompts))
        # 两次均失败 → 留空，由收尾主题模板兜底（QP1-6：绝不回退 narration）

    if n:
        await asyncio.gather(*[_one(i) for i in range(n)], return_exceptions=True)
    _writeback_and_smooth(storyboard, prompts_by_idx, sources_by_idx, write_sources, style_prefix, protect=protect)


async def _fill_image_prompts_bulk(
    llm,
    storyboard: Storyboard,
    style_prefix: str,
    *,
    chunk: int,
    entity_roster: dict | None = None,
    task_dir: Path | None = None,
    protect: frozenset[int] | set[int] = frozenset(),
) -> None:
    """批量路径（E8 前的 _fill_image_prompts_robust 主体；chunk>1 时的配置级回退）。

    ``protect``（2026-09-01 审查 #1）：透传收尾 _writeback_and_smooth（P0-5 表
    覆写豁免）。
    """
    import asyncio

    frames = storyboard.frames
    video_type = storyboard.config.video_type
    explicit_type = video_type in ("ai_image", "ai_video", "remotion_course")
    # content-match T5：ai_image/ai_video 任务的逐镜 visual_source 推荐写回由
    # VIDEO_GEN_CONTENT_ROUTING 门控（D2 默认 on；off = 修前 explicit_type 闸门丢弃，
    # 行为逐字节一致）。vimax（非 explicit）维持原写回——引擎层不消费，行为不变。
    write_sources = (not explicit_type) or content_routing_enabled()
    n = len(frames)
    prompts_by_idx: dict[int, str] = {}
    sources_by_idx: dict[int, str] = {}

    # 1) 分块并行 bulk（每块构造 mini-storyboard，复用 _plan_course_batched 的模式）
    chunks = [(i, frames[i : i + chunk]) for i in range(0, n, chunk)]

    def _mini(offset: int, chunk: list) -> Storyboard:
        return Storyboard(
            title=storyboard.title,
            config=storyboard.config,
            frames=[StoryboardFrame(index=j, narration=f.narration) for j, f in enumerate(chunk)],
            # T6：全片 groups 切块内局部序号——T-S1 组级锚定指令获得真实指代
            # （【组标题】分段进入块 user 消息，组边界跨块也可见）。
            scene_groups=_slice_scene_groups_for_chunk(storyboard.scene_groups, offset, len(chunk)),
            # T5：锚定卡随 mini 携带——每块 system 注入同一份锚（见
            # _generate_course_prompts_enhanced 的 anchor_instruction）。
            global_anchor=getattr(storyboard, "global_anchor", None),
        )

    async def _bulk(offset: int, chunk: list) -> tuple[int, list[str], list[str]]:
        mini = _mini(offset, chunk)
        # M2 kwargs 条件传参（二期下半场终扫订正）：task_dir/index_offset 仅在
        # task_dir 存在（sidecar 落盘路径）时下发——无条件传参会打破既有窄签名
        # 测试桩（test_phase1_ir_wiring.fake_enhanced 三参形态，恒 TypeError 被
        # return_exceptions 吞成静默单帧兜底）。生产路径 task_dir 恒非 None，行为不变。
        _gen_kwargs = ({"task_dir": task_dir, "index_offset": offset} if task_dir is not None else {}) | ({"entity_roster": entity_roster} if entity_roster else {})
        # 2026-08-31 路由并入：有表时批量块同样不推荐（表覆写语义；无表 = 现状）。
        if getattr(storyboard, "shot_table", None):
            _gen_kwargs["visual_source_settled"] = True
        prompts, sources = await _generate_course_prompts_enhanced(llm, mini, style_prefix, **_gen_kwargs)
        # BUG 1-A 修复：LLM 返回与 chunk 长度不一致（早停/多返）-> 整块不可信。
        # 2026-08-19（P1 补齐重问）：先带条数强约束重问一次同块（1 次调用），仍不齐才落
        # missing 走单帧重试（8 次调用更贵且更易超时——corporate_live 事故兜底链触发源）。
        # 重问用新 mini：首次未对齐调用可能经 _apply_camera_fields/_apply_ir_fields 往
        # mini 帧写过错位字段，重问后的搬回须以干净帧为准。
        if not _expect_aligned(prompts, len(chunk)):
            logger.warning(
                "prompt chunk unaligned: got %d prompts for %d frames (offset %d) - re-ask chunk once with count constraint",
                len(prompts),
                len(chunk),
                offset,
            )
            mini = _mini(offset, chunk)
            prompts, sources = await _generate_course_prompts_enhanced(
                llm,
                mini,
                style_prefix,
                extra_system=(f"数量硬约束（上次违反）：上一次返回了 {len(prompts)} 条，与 {len(chunk)} 条旁白不对应；必须精确返回 {len(chunk)} 条 frames，与输入旁白逐条对应，不得提前停止、不得合并、不得遗漏。"),
                **_gen_kwargs,
            )
        if not _expect_aligned(prompts, len(chunk)):
            logger.warning(
                "prompt chunk still unaligned after re-ask: got %d prompts for %d frames (offset %d) - retry per-frame",
                len(prompts),
                len(chunk),
                offset,
            )
            return offset, [], []
        # content-match T5 语义修正：sources 未对齐只丢 sources 不连坐 prompts——
        # prompts 是逐位映射（错位不可信），sources 独立字段（丢掉即帧落默认类型）。
        # 修前与 prompts 连坐（整块返空），LLM 忽略 visual_source 字段（旧式输出）
        # 会把整块 prompts 也打掉；且空串推荐 = 无推荐，不是不可信信号。
        if write_sources and len(sources) != len(chunk):
            logger.warning(
                "source chunk unaligned: got %d sources for %d frames (offset %d) - sources dropped, prompts kept",
                len(sources),
                len(chunk),
                offset,
            )
            sources = []
        # 镜头语言搬回同理：已进入对齐分支才搬（避免 mini 帧错位写到真实帧）
        for j, mf in enumerate(mini.frames):
            if offset + j < len(frames):
                if mf.shot_size:
                    frames[offset + j].shot_size = mf.shot_size
                if mf.camera_move:
                    frames[offset + j].camera_move = mf.camera_move
                if mf.visual_ir:
                    frames[offset + j].visual_ir = mf.visual_ir
                # M2：出场声明同搬（None=不合作也搬——对齐分支下 mini 帧语义可信；
                # 无名册时两侧同为默认 None，赋值幂等）
                frames[offset + j].visible_characters = mf.visible_characters
                frames[offset + j].visible_props = mf.visible_props
        return offset, prompts, sources

    # P1-E9: 单块 LLM 失败不拖垮整批——return_exceptions 吸收，失败块帧落入下方单帧兜底
    # （修复前整任务随单块异常失败）。成功块的 prompts/sources 照常写回。
    for res in await asyncio.gather(*[_bulk(o, c) for o, c in chunks], return_exceptions=True):
        if isinstance(res, BaseException):
            logger.warning("image-prompt bulk chunk failed (%s); affected frames fall back to per-frame generation", res)
            continue
        offset, prompts, sources = res
        for j, p in enumerate(prompts):
            if p:
                prompts_by_idx[offset + j] = p
        if write_sources:
            for j, s in enumerate(sources):
                if s:
                    sources_by_idx[offset + j] = s

    # 2) 漏掉的帧 → 单帧兜底（Semaphore 限并发 + 总量封顶 + 增量收集），单帧绝不早停
    missing = [i for i in range(n) if i not in prompts_by_idx]
    if missing:
        gen = _generate_video_prompts if video_type == "ai_video" else _generate_image_prompts
        # 2026-08-19（P0）：单帧兜底注入风格守卫（风格硬约束+实拍禁隐喻+锚定卡，与 bulk
        # system 同源三段）。修前单帧 system 零风格信息——T-I0-② 曾注「风格统一由 system
        # prompt 的全片声明承担」，但该 system 并无此声明，unaligned 降级帧裸奔出隐喻
        # （corporate_live 帧 16-22/0-8 事故根因）。
        _anchor = getattr(storyboard, "global_anchor", None) or {}
        style_guard = _style_guard_instruction(style_prefix, str(_anchor.get("anchor_card", "") or ""))
        sem = asyncio.Semaphore(_FALLBACK_CONCURRENCY)

        async def _one(i: int) -> None:
            # T3: 结果**增量收集**——_one 完成即写 prompts_by_idx，不靠 gather 返回值
            # （否则总量封顶的取消潮会丢掉已完成帧的成果）。
            try:
                async with sem:
                    # T5: 构造上下文（title + 前后各 1 条相邻旁白），帮 LLM 解开指代
                    ctx: dict = {"title": storyboard.title}
                    if i > 0:
                        ctx["adjacent_narrations"] = {"prev": frames[i - 1].narration}
                    if i < n - 1:
                        ctx.setdefault("adjacent_narrations", {})["next"] = frames[i + 1].narration
                    # T3: 单帧超时收紧为 60s（bulk 120s 的一半——兜底帧不该比 bulk 更慢）
                    ps = await gen(llm, [frames[i].narration], context=ctx, timeout=_FALLBACK_PER_FRAME_TIMEOUT, style_guard=style_guard)
                    # T-I0-②：产物保持 LLM 原文（不前置/不后缀）
                    prompts_by_idx[i] = ps[0] if ps else ""
            except Exception as e:
                # P1-E9: 单帧兜底自身失败被吸收——该帧留空，
                # 由下方 QP1-6 _topic_fallback_prompt 兜底（绝不回退 narration）。
                logger.warning("single-frame prompt fallback failed for frame %d: %s", i, e)

        tasks = [asyncio.create_task(_one(i)) for i in missing]
        try:
            # T3: 总量封顶 300s——到点取消剩余，未完成帧自然落入 _topic_fallback_prompt。
            # 已完成帧的成果已在 prompts_by_idx（增量收集），不受取消潮影响。
            await asyncio.wait_for(asyncio.gather(*tasks), timeout=_FALLBACK_TOTAL_BUDGET)
        except TimeoutError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            remaining = sum(1 for i in missing if i not in prompts_by_idx)
            logger.warning(
                "single-frame prompt fallback budget exhausted (%.0fs): %d/%d frames fall back to topic prompt",
                _FALLBACK_TOTAL_BUDGET,
                remaining,
                len(missing),
            )

    # 3) 写回收尾（E8：抽共用 _writeback_and_smooth，与逐帧路径同源——QP1-6 兜底、
    # 运镜收敛、IR 风格钉死三件事两路径一份实现，防拷贝漂移）。
    _writeback_and_smooth(storyboard, prompts_by_idx, sources_by_idx, write_sources, style_prefix, protect=protect)


async def plan_course(
    llm,
    *,
    storyboard: Storyboard,
    style_context: dict | None = None,
    task_dir: Path | None = None,
    chunk_size: int | None = None,
    frame_concurrency: int | None = None,
    entity_roster: dict | None = None,
    shot_table_enabled: bool = False,
    shot_table_llm=None,  # noqa: ANN001
) -> None:
    """Course-aware storyboard planning with paragraph-aware enhanced prompt.

    Uses scene_groups context to generate better visual descriptions.
    Sets per-frame visual_source based on LLM recommendation.
    Supports batch processing for >50 frames.
    Mutates storyboard.frames in place.

    content-match T6/T7：末尾统一跑确定性路由护栏 + graphic spec 提取。
    ``task_dir`` 提供时 spec 拒绝写 degradations sidecar（None = 仅日志）。
    ``chunk_size``/``frame_concurrency``（E8，2026-08-21）：透传
    ``_fill_image_prompts_robust``（config ``prompt_chunk_size`` /
    ``prompt_frame_concurrency``；None = 模块默认逐帧）。
    ``shot_table_enabled``（S1，2026-08-26 调度表计划）：True 时分组建立后先跑
    阶段 1 调度表（``_plan_shot_table``）。函数级默认 False = 既有调用方/测试
    零变化（AC3 基线）；生产由 service 传 ``config.shot_table_enabled``（默认开）。
    ``shot_table_llm``（S5）：None = 跟随 ``llm``（script LLM = script_model）。
    范围排除：vimax 不生成调度表（本计划不动 vimax）。
    """
    style_prefix = (style_context or {}).get("style_prefix", "")
    video_type = storyboard.config.video_type
    narrations = [f.narration for f in storyboard.frames]
    has_groups = len(storyboard.scene_groups) > 0

    if video_type == "remotion":
        return

    # remotion_course（2026-09-10 独立管线计划 P3 双保险）：锚定卡/调度表/逐帧扩写/
    # 路由护栏/graphic spec 全套不执行——解析由 course_script.parse_course_script 一次
    # 到位（layout/layout_content/visual_prompt 直接产出），service 侧对 remotion_course
    # 已跳过本调用，此处早退兜住 API 直调/回归路径（对齐 remotion 先例）。
    if video_type == "remotion_course":
        return

    # content-match T5：快照规划前已存在的 visual_source——生产路径 plan_course
    # 只会收到全 None 的新 storyboard（service 两条路径均临时构造帧），非 None 值
    # 只可能来自用户手改（PUT 后的重建场景）。这些帧写回不覆盖（P0-5 不变量）、
    # 护栏不触碰（D8 管 LLM 推荐漂移，不管用户手选）。
    protected = frozenset(f.index for f in storyboard.frames if getattr(f, "visual_source", None))

    # T5（2026-08-18 两反馈批 P-06/反馈1问题2）：分块前**一次**全片锚定调用——块间
    # 互不可见的架构下，全片一致性（主角/场景/世界观/色调）只能靠同一份锚定卡钉住
    # （style=default 下此前唯一一致性来源为零）。锚挂到 storyboard.global_anchor 供
    # 每块 system 注入 + IR style 槽统一覆写消费；失败返回 None → 跳过注入走现状
    # 分块逻辑（评审 a：不更坏）。成本 = 每任务 +1 次小 LLM 调用。
    storyboard.global_anchor = await _generate_global_anchor(llm, title=storyboard.title, narrations=narrations, style_prefix=style_prefix, **({"entity_roster": entity_roster} if entity_roster else {}))
    # T5b（拍板⑦）：版式卡持久化到 storyboard（to_dict 落盘）——渲染/预览在别的时点
    # 从 storyboard.json 重读；锚定失败/无字段 = None → 渲染回落确定性默认版式卡。
    storyboard.graphic_layout_card = str((storyboard.global_anchor or {}).get("graphic_layout_card", "") or "").strip() or None

    # T6（2026-08-18 拍板①）：普通任务生产分组器。规划前已有 groups（course_pipeline
    # 段落分组）= 调用方意图，不覆盖；锚定调用结构化输出顺带产出场景边界（零额外
    # LLM 调用），失败/无效 → 确定性 CHUNK 对齐兜底（≤8 帧天然一组）。groups 落
    # storyboard（随 to_dict 持久化）供 T-S1 文本锚定与 T-S2 组锚定消费。
    if not storyboard.scene_groups and storyboard.frames:
        storyboard.scene_groups = _scene_groups_from_anchor(storyboard.global_anchor, len(storyboard.frames)) or _deterministic_scene_groups(len(storyboard.frames))

    # S1（2026-08-26 调度表计划）阶段 1：分组建立后、逐帧扩写前，一次轻量调用产出
    # 全片调度表（「同一场景组」直接复用 storyboard.scene_groups）。二次失败 →
    # None 降级现状逐帧 + degradations sidecar 留痕（AC6；task_dir 缺省仅日志）。
    # 范围排除：仅 ai_image/ai_video（vimax 不动）。
    # T-A5（R5，2026-09-09 feedback-round1）：remotion_course 纳入——T6 版式 hint
    # 走调度表 addon，门控漏本类型则调度表（含 layout 列）从不生成，D14 链中
    # planner hint 一环断裂。
    storyboard.shot_table = None
    if shot_table_enabled and storyboard.frames and video_type in ("ai_image", "ai_video", "remotion_course"):
        storyboard.shot_table = await _plan_shot_table(shot_table_llm or llm, storyboard, style_prefix)
        if storyboard.shot_table is None:
            logger.warning("镜头调度表二次失败——降级现状逐帧扩写（AC6）")
            if task_dir is not None:
                record_degradation(
                    task_dir,
                    kind=KIND_SHOT_TABLE_FALLBACK,
                    detail="shot table LLM failed twice (parse/misaligned) — degraded to per-frame prompting without schedule injection",
                )

    # Batch processing for large frame counts (plan section 2.4)
    # T6 两步纪律：has_groups 是**规划前快照**（本函数开头），planner 分组器产出的
    # groups 不改变分派——_plan_course_batched 转活是第二步独立验收（长期死代码、
    # 仅测试覆盖，直接转活自带隐患），本批 >50 帧任务仍走 robust 路径。
    if len(narrations) > 50 and has_groups:
        _erb = {"entity_roster": entity_roster} if entity_roster else {}
        # protect（2026-09-01 审查 #1）：规划前手改快照透传——表覆写对 protect 帧豁免。
        await _plan_course_batched(llm, storyboard, style_prefix, protect=protected, **_erb)
    else:
        # M1-1: 非分组路径走 _fill_image_prompts_robust（E8 起默认逐帧；>1 批量回退）。
        # 修前单次 _generate_course_prompts_enhanced 全量调用 → LLM 早停 → :178 回退 narration。
        # M2：entity_roster（出场声明名册）透传——None/空 = 无项目链路行为不变。
        _er = {"entity_roster": entity_roster} if entity_roster else {}
        await _fill_image_prompts_robust(llm, storyboard, style_prefix, chunk_size=chunk_size, frame_concurrency=frame_concurrency, task_dir=task_dir, protect=protected, **_er)

    # content-match T6：确定性护栏（块间并发互不可见，全部块汇总后统一收敛；
    # 内部自判任务类型与开关——vimax/off 两态不触碰/清空；protect 集=用户手改帧豁免）。
    # T3（2026-09-01）：任务级图文镜占比上限（cfg.graphic_ratio，30/40/50/70%）——
    # None/0 走 env/默认链（行为不变）；上限≠保障，仅抬规则 1 天花板。
    n_routed = sum(1 for f in storyboard.frames if f.visual_source == "graphic")
    normalize_visual_routing(
        storyboard,
        protect=protected,
        ratio_override=(storyboard.config.graphic_ratio / 100 if getattr(storyboard.config, "graphic_ratio", None) else None),
    )
    # content-match T7：graphic spec 提取（零 graphic 帧零 LLM 调用；
    # 拒绝/失败帧 spec=None → 渲染期按常规画面出图，绝不拖垮规划）。
    n_after_guard = sum(1 for f in storyboard.frames if f.visual_source == "graphic")
    n_written = await extract_graphic_specs(llm, storyboard, task_dir=task_dir, protect=protected) or 0
    n_final = sum(1 for f in storyboard.frames if f.visual_source == "graphic")
    # W3 可观测（2026-08-17）：一行汇总路由漏斗——零路由类问题直接看日志定位
    # （vg_f67b4b832663 零路由当时靠 sidecar 排除法反推，费三轮取证）。
    logger.info(
        "graphic 路由汇总: 推荐 %d → 护栏后 %d → 提取成功 %d → 最终 %d/%d 帧",
        n_routed,
        n_after_guard,
        n_written or 0,
        n_final,
        len(storyboard.frames),
    )


async def _plan_course_batched(
    llm,
    storyboard: Storyboard,
    style_prefix: str,
    *,
    entity_roster: dict | None = None,
    protect: frozenset[int] | set[int] = frozenset(),
) -> None:
    """Process large storyboards in batches by scene_group (plan section 2.4).

    ``protect``（2026-09-01 审查 #1）：plan_course 规划前手改快照——尾部队覆写
    对 protect 帧豁免（P0-5 不变量覆盖表覆写）。
    """
    # P0-5 → content-match T5：显式选定 ai_image/ai_video 时，推荐写回由
    # VIDEO_GEN_CONTENT_ROUTING 门控（off = 修前不写回）；vimax 维持原写回。
    explicit_type = storyboard.config.video_type in ("ai_image", "ai_video", "remotion_course")
    write_sources = (not explicit_type) or content_routing_enabled()
    # 2026-09-01 复核修复：有表时逐组返回的 visual_source 不写入（见组内写回点）。
    _has_table = bool(getattr(storyboard, "shot_table", None))
    # 审查 Minor 3：总量预算——每组分片前与每个单帧重试前检查；到点停止，
    # 剩余帧由末尾主题兜底扫尾填（QP1-6 绝不回退 narration）。
    budget_deadline = time.monotonic() + _BATCHED_TOTAL_BUDGET
    # 2026-08-19（P0）：单帧重试的风格守卫（风格硬约束+实拍禁隐喻+锚定卡，与 bulk
    # system 同源三段）——修前单帧 system 零风格信息，降级帧裸奔出隐喻。
    _anchor = getattr(storyboard, "global_anchor", None) or {}
    style_guard = _style_guard_instruction(style_prefix, str(_anchor.get("anchor_card", "") or ""))
    for group in storyboard.scene_groups:
        if time.monotonic() >= budget_deadline:
            logger.warning("plan_course batched budget exhausted — remaining groups use topic fallback")
            break
        valid_fis = [fi for fi in group.frame_indices if fi < len(storyboard.frames)]
        if not valid_fis:
            continue

        # Temporarily create a mini-storyboard for this group (0-based indices)
        def _mini() -> Storyboard:
            return Storyboard(
                title=storyboard.title,
                config=storyboard.config,
                frames=[StoryboardFrame(index=j, narration=storyboard.frames[fi].narration) for j, fi in enumerate(valid_fis)],
                scene_groups=[SceneGroup(index=0, title=group.title, frame_indices=list(range(len(valid_fis))))],
                # T5：锚定卡随 mini 携带（同 _fill_image_prompts_robust 的 _bulk）。
                global_anchor=getattr(storyboard, "global_anchor", None),
            )

        mini = _mini()
        # 2026-09-01（审查 #2，路由并入补齐）：有表时组批量不再做路由推荐——与
        # robust 路径（_fill_image_prompts_bulk/_per_frame 的 settled 传参）同款，
        # 省推荐输出 token + 消除 vs=None 帧的语义分叉（表覆写在该路径本就生效）。
        _gen_kwargs = {"entity_roster": entity_roster} if entity_roster else {}
        if getattr(storyboard, "shot_table", None):
            _gen_kwargs["visual_source_settled"] = True
        image_prompts, visual_sources = await _generate_course_prompts_enhanced(llm, mini, style_prefix, **_gen_kwargs)
        # BUG 1-A 修复：LLM 返回数量不一致（早停/多返）-> 整组不可信。
        # 2026-08-19（P1 补齐重问）：先带条数强约束重问一次同组（新 mini——首次未对齐
        # 调用可能经 _apply_camera_fields/_apply_ir_fields 写过错位字段），仍不齐才降级
        # 单帧重试。
        aligned = _expect_aligned(image_prompts, len(valid_fis))
        if not aligned:
            logger.warning(
                "batched group unaligned: %d prompts for %d frames - re-ask group once with count constraint",
                len(image_prompts),
                len(valid_fis),
            )
            mini = _mini()
            image_prompts, visual_sources = await _generate_course_prompts_enhanced(
                llm,
                mini,
                style_prefix,
                extra_system=(f"数量硬约束（上次违反）：上一次返回了 {len(image_prompts)} 条，与 {len(valid_fis)} 条旁白不对应；必须精确返回 {len(valid_fis)} 条 frames，与输入旁白逐条对应，不得提前停止、不得合并、不得遗漏。"),
                **_gen_kwargs,
            )
            aligned = _expect_aligned(image_prompts, len(valid_fis))
            if not aligned:
                logger.warning(
                    "batched group still unaligned after re-ask: %d prompts for %d frames - retry per-frame",
                    len(image_prompts),
                    len(valid_fis),
                )
        # 镜头语言：仅在对齐时搬回（避免 mini 帧错位）
        if aligned:
            for j, fi in enumerate(valid_fis):
                if j < len(mini.frames):
                    if mini.frames[j].shot_size:
                        storyboard.frames[fi].shot_size = mini.frames[j].shot_size
                    if mini.frames[j].camera_move:
                        storyboard.frames[fi].camera_move = mini.frames[j].camera_move
                    if mini.frames[j].visual_ir:
                        storyboard.frames[fi].visual_ir = mini.frames[j].visual_ir
                    # M2：出场声明同搬（无名册时两侧同为默认 None，赋值幂等）
                    storyboard.frames[fi].visible_characters = mini.frames[j].visible_characters
                    storyboard.frames[fi].visible_props = mini.frames[j].visible_props
        # content-match T5：sources 只看条数对齐（空串=无推荐），未对齐只丢 sources
        sources_aligned = (not write_sources) or len(visual_sources) == len(valid_fis)
        if write_sources and not sources_aligned:
            logger.warning(
                "batched group sources unaligned: %d for %d frames - sources dropped, prompts kept",
                len(visual_sources),
                len(valid_fis),
            )
        missing_fis: list[int] = []
        for j, fi in enumerate(valid_fis):
            if aligned and j < len(image_prompts) and image_prompts[j]:
                storyboard.frames[fi].image_prompt = image_prompts[j]
            else:
                missing_fis.append(fi)  # 未对齐时全部落单帧重试
            # content-match T5：只填 None 帧（用户手改值不覆盖，同 robust 路径）。
            # 2026-09-01 复核修复（审查再反馈）：有表时**不写入**逐组返回的
            # visual_source——settled mini 的输出契约仍含该字段（返回值应由调用方
            # 丢弃），写回层须与 _writeback_and_smooth 同款表门控；否则表行
            # vs=None 的帧被 settled-mini 残留推荐污染（表覆写对 None 行不触碰，
            # 残留存活），与 robust 路径（保持 None → 护栏归默认引擎）语义分叉。
            if write_sources and not _has_table and sources_aligned and j < len(visual_sources) and visual_sources[j] and not storyboard.frames[fi].visual_source:
                storyboard.frames[fi].visual_source = visual_sources[j]
        # QP1-6: LLM 漏帧绝不回退 narration——逐帧重试（视频用 video prompt 生成器），
        # 再失败用主题意象模板兜底。
        gen = _generate_video_prompts if storyboard.config.video_type == "ai_video" else _generate_image_prompts
        for fi in missing_fis:
            if time.monotonic() >= budget_deadline:
                logger.warning("plan_course batched budget exhausted — remaining missing frames use topic fallback")
                break
            prompt = ""
            try:
                # T5: 构造上下文（title + 前后各 1 条相邻旁白）
                ctx: dict = {"title": storyboard.title}
                if fi > 0 and fi - 1 < len(storyboard.frames):
                    ctx["adjacent_narrations"] = {"prev": storyboard.frames[fi - 1].narration}
                if fi + 1 < len(storyboard.frames):
                    ctx.setdefault("adjacent_narrations", {})["next"] = storyboard.frames[fi + 1].narration
                ps = await gen(llm, [storyboard.frames[fi].narration], context=ctx, style_guard=style_guard)
                prompt = ps[0] if ps else ""
            except Exception:
                logger.warning("single-frame prompt regen failed for frame %d", fi, exc_info=True)
            storyboard.frames[fi].image_prompt = prompt or _topic_fallback_prompt(storyboard, style_prefix)

    # QP1-6 扫尾（审查 Minor 3）：预算耗尽留下的未处理帧 → 主题意象兜底
    # （只填 None——绝不留空、绝不回退 narration；非预算路径不受影响）。
    for f in storyboard.frames:
        if not f.image_prompt:
            f.image_prompt = _topic_fallback_prompt(storyboard, style_prefix)

    # Task 9：批次间同样互不可见（按 scene group 逐批调 LLM），全部批次汇总后收敛一次。
    # S2（2026-08-26 调度表计划）：有表同 _writeback_and_smooth——表覆写 + 跳过
    # smooth（表是阶段 1 联合设计，后处理不得覆写）。批量路径的块级请求不注入
    # 调度行（本计划 S2 锚定逐帧路径；chunk>1 是配置级回退路径）。
    if getattr(storyboard, "shot_table", None):
        _apply_shot_table_fields(storyboard.frames, storyboard.shot_table, protect=protect)
    else:
        smooth_camera_continuity(storyboard.frames)
    # T5：IR style 槽统一覆写（规划期汇总点②，同 robust 路径语义）。
    anchor = getattr(storyboard, "global_anchor", None)
    if anchor and anchor.get("style_string"):
        _unify_ir_style_slots(storyboard.frames, anchor["style_string"])


async def _generate_course_prompts_enhanced(
    llm,
    storyboard: Storyboard,
    style_prefix: str,
    extra_system: str = "",
    script_context: dict | None = None,
    entity_roster: dict | None = None,
    task_dir: Path | None = None,
    index_offset: int = 0,
    shot_direction: dict | None = None,
    visual_source_settled: bool = False,
) -> tuple[list[str], list[str]]:
    """Enhanced paragraph-aware prompt with per-frame visual_source recommendation.

    Returns (image_prompts, visual_sources) lists.
    ``extra_system``（2026-08-19 P1 补齐重问）：附加到 system 尾部的补充指令
    （unaligned 重问的条数强约束）。默认空串 = 行为同现状。
    ``script_context``（E8，2026-08-21）：逐帧模式的脚本全文上下文
    （``{"narrations": [...], "current_frame_index": int}``）——进 user 消息
    （title 本就在）。默认 None = 批量路径行为同现状。
    ``entity_roster``（M2，2026-08-24 计划二期）：出场声明名册
    （``{"characters": [{"name","features"}], "props": [{"name","description"}]}``）。
    非空时 system 注入名册 + 输出契约追加 visible_* 字段，解析后经
    ``_apply_visible_fields`` 写回帧；None/空 = 契约零变化（无项目链路不变量）。
    ``task_dir``（M2）：名册外名字 declared_invalid 的 sidecar 落盘点（None = 仅日志）。
    ``shot_direction``（S2，2026-08-26 调度表计划）：本帧行+±1 邻行
    （``shot_direction_block`` 产物）。非 None 时注入 **user 负载首部**
    （``shot_direction`` + ``shot_directive`` 硬指令两个键，位于 title/narrations/
    script_context 之前——计划写死的组装秩序）；system 侧零改动。默认 None =
    无表/无行帧，行为与现状逐字节一致（AC3）。条件传参（既有窄签名测试桩保护）。
    ``visual_source_settled``（2026-08-31 路由并入）：True = 有表任务——mini
    system 的 routing_instruction 整段替换为一句「已由调度表确定」（逐帧不再
    推荐），返回的 sources 由调用方丢弃（写回层有表门控）。默认 False =
    无表降级路径，行为与现状逐字节一致（条件传参同上）。
    """
    # 2026-08-01（Task 2）：改中文措辞。原文是塞进中文 system prompt 的一句英文指令
    # （"Every prompt MUST start with: '<英文风格词>.'"），既要求以英文开头、本身又是英文，
    # 这是 LLM 把整条画面词写成英文的**主因**（反馈 5）。
    # T-I0-②（2026-08-17 图片止血）：退役「每条以风格词开头」的机械拼接--逐帧同词
    # 开头正是「千篇一律」的构造性来源；改为全片统一声明，风格特征由 LLM 自然融入。
    # T5 三轨对齐（2026-08-18）：软引导升级硬约束（「必须呈现…禁止…」）——与重生端点/
    # 角色图 _with_style 的 prompt 前置两轨同一语义（改一处须同步其余两轨）。
    # 2026-08-19：三段风格指令（风格硬约束/实拍禁隐喻/锚定卡）抽为模块级同源构造函数
    # _build_*——单帧兜底与重生端点经 _style_guard_instruction 共用同一措辞，禁止拷贝漂移。
    style_instruction = _build_style_instruction(style_prefix)
    # P1（2026-08-19）：实拍禁隐喻条款进 bulk system 本体——修前只进锚定调用一次，
    # 分块 system 没有，机械隐喻（绿拇指雕塑/废纸团/泡影网格）是 bulk 具象化指令
    # 「可用视觉隐喻」的合法产物（措辞基准=_LIVE_ACTION_ANCHOR_CLAUSE）。
    live_action_instruction = _build_live_action_instruction(style_prefix)
    # P1（2026-08-19）：具象化指令按风格分流——实拍走真人行为版，其余保留默认隐喻版
    # （原句逐字），否则实拍风格下「免疫细胞」式隐喻指令与禁隐喻条款自相冲突。
    concretization_instruction = (
        "抽象概念（如「反馈质量低」）一律用具象的真人行为场景落地（如：具体的人在具体场景中被误解、被纠正），不得使用器物或装置隐喻。"
        if live_action_instruction
        else "抽象概念用具象场景落地（如「免疫细胞战斗」画成白细胞包裹病原体的微观场景），"
    )
    # T5（2026-08-18 两反馈批 P-06/反馈1问题2）：全片锚定卡注入——storyboard.global_anchor
    # 由 plan_course 分块前一次锚定调用产出（mini storyboard 构造时随 title/config 一起
    # 携带），每块 system 拿到**同一份**锚（主角/场景/世界观/色调+禁用清单），块间漂移
    # 被钉住。无锚（调用失败回落，评审 a）时本段为空——现状行为。
    anchor = getattr(storyboard, "global_anchor", None) or {}
    anchor_instruction = _build_anchor_instruction(str(anchor.get("anchor_card", "") or ""))
    # T-I0-③：多样性指令--相邻帧景别/视角/句式变化，禁止相同开头结尾（千篇一律的
    # 第二构造性来源：逐帧模板化）。
    diversity_instruction = "多样性要求：相邻各条画面描述在景别、视角、句式上要有明显变化；禁止相邻两条以相同词语开头或收尾。\n\n"
    # P-04（2026-08-18 两反馈批）：语言锚--分块并发（CHUNK=8）块间互不可见，本 system
    # 此前是全 planner 唯一没有语言指令的 prompt，各块语言各自漂移（事故：实物稿
    # 18 帧 image_prompt 0-7 英文 / 8-15 中文 / 16-17 英文，与块边界逐帧对齐）。
    # 拍板④：锁语言与旁白一致；措辞与兜底 _IMAGE_PROMPT_SYSTEM / _VIDEO_PROMPT_SYSTEM
    # 的「**中文**画面描述」同款强调口径。锚是旁白相对措辞--每块按本块旁白语言自行
    # 判定，块间无需可见也自然收敛到同一语言。Ark seedream ≤300 汉字是长度约束，
    # 本锚只锁语言不碰长度，中文稿描述长度口径不变。
    language_instruction = "语言锁定：image_prompt 必须使用与旁白相同的语言（旁白中文→中文，旁白英文→英文），全套描述逐帧语言一致，严禁中英混用。\n\n"

    # Task 8：镜头语言只对 ai_video 有意义（生图无运镜概念）。
    # T6（2026-08-12）：ai_video 时追加主体动作 + 环境动态要求（「画面不动」根因修复）。
    camera_instruction = (
        "\n每条画面必须给出 shot_size（景别）与 camera_move（运镜），只能从给定枚举里选。开场用 wide 建立环境，讲解主体用 medium，强调细节用 close；相邻镜头不要重复同一种运镜。"
        "\nimage_prompt 必须包含明确的主体动作（谁在做什么具体动作）与环境动态（光影/天气/背景的细微变化）；运镜仅通过 camera_move 字段表达，image_prompt 文本中不要写运镜词（推近/拉远/摇移等），避免与 camera_move 字段双写冲突。"
        if storyboard.config.video_type == "ai_video"
        else ""
    )
    # Phase 1 T2（2026-08-17 方言层）：ai_video 帧产出结构化 IR（翻译器输入）。
    # D-A/D-B 已拍板：audio 写一句动作耦合音效；风格为中文短语（全链路中文）。
    ir_instruction = (
        "\n每帧同时输出 ir 对象：{subject: 主体+1-2个具体属性, action: 单一动词链+物理后果（动作引起可见变化）, "
        "environment: 场景+光源方向与质感一句, style: 视觉风格短语, audio: 一句与动作耦合的音效描述}。"
        "主体与动作必须具体（动词优先于形容词），风格短语全片统一。"
        if storyboard.config.video_type == "ai_video"
        else ""
    )
    ir_spec = ', "ir": {"subject": "...", "action": "...", "environment": "...", "style": "...", "audio": "..."}' if storyboard.config.video_type == "ai_video" else ""
    # T-C2（2026-08-17 Phase 2）：摄影规则指令级注入（ai_video 任务；D-C 拍板）。
    from ..cinematography_rules import cinematography_instruction

    cinematography_instructions = cinematography_instruction() if storyboard.config.video_type == "ai_video" else ""

    # M2（2026-08-24 计划二期）：出场声明名册注入——非空才追加（无名册 = 无项目/
    # 无资产/显式脱离任务，输出契约与现状逐字节一致——无项目链路不变量）。
    roster_instruction = ""
    visible_spec = ""
    if entity_roster and ((entity_roster.get("characters") or []) or (entity_roster.get("props") or [])):
        _roster_lines = []
        _chars = entity_roster.get("characters") or []
        _props = entity_roster.get("props") or []
        if _chars:
            _roster_lines.append("角色名册：" + "、".join(f"{c.get('name')}（{(c.get('features') or '').strip()}）" for c in _chars))
        if _props:
            _roster_lines.append("道具名册：" + "、".join(f"{p.get('name')}（{(p.get('description') or '').strip()}）" for p in _props))
        roster_instruction = (
            "\n".join(_roster_lines) + "\n逐镜出场声明（必须输出）：每帧列出本镜实际出场的角色与道具——旁白提及或画面呈现才算出场；"
            "只允许使用名册中的名字，不得发明新名字；未出场输出空数组 []。"
            "image_prompt 中应使用名册内的角色/道具名称指代（系统会在渲染时注入对应参考图）。\n"
            # 2026-08-28（AC2 实证修复）：外观锚定权在参考图——文字虚构外观会与注入的
            # 合成图/道具图打架且部分帧文字胜出（f17 性别、f22 眼镜失真实证）。
            "外观以参考图为准（硬约束）：禁止在 image_prompt 中虚构或描写名册角色/道具的外观细节"
            "（性别、年龄、发型、是否戴眼镜、衣着、颜色等）；涉及外观时只可写「外观与参考图一致」。\n\n"
        )
        # view 说明：角色仅本镜明显展示非正面时给出；道具 0=主图、1/2=补充角度
        # 次级项 1 修复（复核）：visible_props 数组补 "]"——修前以 "}" 结尾，拼出的
        # STRICT JSON 契约畸形（对象数组未闭合），平白增加 LLM 解析失败/早停风险。
        visible_spec = ', "visible_characters": [{"name": "角色名", "view": "front|side|back"}], "visible_props": [{"name": "道具名", "view": 0|1|2}]'

    # Format narrations with scene group context
    if storyboard.scene_groups:
        formatted = []
        for group in storyboard.scene_groups:
            group_narrations = [storyboard.frames[i].narration for i in group.frame_indices if i < len(storyboard.frames)]
            formatted.append(f"【{group.title}】\n" + "\n".join(group_narrations))
        formatted_script = "\n\n".join(formatted)
    else:
        formatted_script = "\n".join(f.narration for f in storyboard.frames)

    # content-match T4：visual_source 段重写——「内容类型 → 画法」映射表 + 每类
    # few-shot + 多样性要求；枚举按任务主引擎动态收窄（D8 零跨引擎在提示层先收窄，
    # 减少 T6 护栏回退 churn）。remotion 移出 ai_image/ai_video 任务的产出枚举
    # （解析层保留兼容存量值）；vimax 等非 explicit 任务维持旧三值（现状不变量）。
    vt = storyboard.config.video_type
    if vt in ("ai_image", "ai_video"):
        if visual_source_settled:
            # 2026-08-31 路由并入：有表时逐帧不再推荐——路由判定已移入调度表阶段 1
            # （_SHOT_TABLE_SYSTEM，措辞同源），mini 只扩写画面细节。输出契约保留
            # visual_source 字段（解析形状不变），返回值由调用方丢弃。
            routing_instruction = "visual_source 已由镜头调度表确定，你只扩写画面细节。"
        else:
            routing_instruction = (
                "visual_source 路由（同一视频内视觉类型应**多样**，禁止全片只选一种；数据/流程/要点密集段优先 graphic，场景与动态段优先主引擎）。\n"
                "graphic 准入门槛——不满足任一条就不要选 graphic（回到主引擎）：\n"
                "- 旁白强调一个具体数字/百分比/倍数 → graphic（画面中呈现醒目图内文字）。\n"
                "  例：『从350亿美元攀升到830亿美元』『涨幅达到137%』『先来看第一个关键数据：平均售价…』——旁白念出具体数字且该镜围绕数字展开，就选 graphic。\n"
                "- 旁白描述 ≥3 步的先后流程 → graphic（画面为带序号标签的分步图示）\n"
                "- 旁白归纳 ≥2 条并列要点 → graphic（画面含标题与关键词标签）\n"
                "反例（必须选主引擎，不要上卡）：单句陈述、概念解释、过渡句、引出话题；只有一个要点的「伪归纳」。\n"
                "拿不准时的倾向（2026-08-17 零路由校准）：旁白念出具体数字且围绕它展开 → 宁可选 graphic；普通场景陈述 → 宁可回主引擎。\n"
                "路由细则：\n"
                f"- 数据/统计强调（百分比、倍数、金额等具体数字）→ graphic。例：旁白「转化率提升了37%」→ visual_source=graphic（渲染为大数字卡）\n"
                f"- 过程/步骤（有先后顺序的操作流程）→ graphic。例：旁白「先预热，再倒入面糊，最后翻面」→ visual_source=graphic（渲染为步骤卡）\n"
                f"- 概念定义、大段理论要点归纳 → graphic 或 {vt}（并列要点多时选 graphic，渲染为要点卡）\n"
                f"- 真实案例、具体场景、人物、环境 → {vt}\n"
                f"- 动态演示、过程演变、镜头运动 → {vt}\n"
                f"visual_source 只能从这两个值中选择: {vt}, graphic\n"
                "graphic 帧的 image_prompt 与普通帧同一写作要求（完整画面描述、全片风格统一）——**不要**在 image_prompt 里写任何文字渲染/标签要求，系统会在渲染时自动注入经校验的图内文字指令。"
            )
        example_source = vt
    else:
        routing_instruction = "原则：\n1. 同段落画面应视觉连贯\n2. 理论讲解适合图表/文字动画(remotion)\n3. 案例分析适合场景画面(ai_image)\n4. 动态演示适合AI视频(ai_video)\nvisual_source 从以下三个值中选择一个: remotion, ai_image, ai_video"
        example_source = "remotion"

    system = (
        "你是一位专业的视频导演和教学设计师。请为以下课程旁白规划视觉分镜。\n\n"
        f"{style_instruction}"
        f"{live_action_instruction}"
        f"{anchor_instruction}"
        "原则：同段落画面应视觉连贯。\n\n"
        # T-S1（2026-08-17 Phase 3 文本锚定，E3 验证 -41% 感知差异）：同组复用锚定
        # 场景与道具，跨组才换场。
        "锚定规则：同一分组的各帧必须复用同一锚定场景与核心道具（同一间房/同一装置/\n"
        "同一桌面），只变化主体动作与视角；首帧确定的场景要素（材质、色调、陈设）须在\n"
        "后续各帧的描述中保持一致；跨分组才更换场景。\n\n"
        f"{diversity_instruction}"
        f"{routing_instruction}\n\n"
        f"{language_instruction}"
        f"{roster_instruction}"
        "image_prompt 写作要求：画面必须直接呈现旁白中的具体实体或过程（实体锚定），"
        # T5（2026-08-18）：实体锚定补行为类覆盖——旁白述行为/操作时画面必须呈现
        # 真人或角色执行该行为（corporate_live 实拍稿「念操作画面出天平隐喻」根因）。
        "旁白描述行为/操作时，画面必须呈现真人或角色正在执行该行为的具体场景（行为锚定），"
        f"{concretization_instruction}"
        "可用剖面/对比/流程示意图构图（纯图形元素，不用文字标注），每镜至少两个视觉信息层。\n"
        # D1（2026-08-19）：图片侧画面文字策略——允许教学性文字（与兜底/重生 system
        # 的 _TEXT_POLICY_INSTRUCTION 同一常量，三处同源防漂移）。
        f"{_TEXT_POLICY_INSTRUCTION}\n\n"
        # Task 8：扩展 frames 对象内部字段。**顶层键 frames 不可改名**（承重，见文件头警告）。
        '输出STRICT JSON: {"frames": [{"image_prompt": "...", "shot_size": "wide|medium|close", '
        f'"camera_move": "static|push_in|pull_out|pan|tilt", "visual_source": "{example_source}"{visible_spec}{ir_spec}}}]}}\n'
        # 2026-08-19（P1 LLM 守约性）：条数强约束——分块早停（8 帧只返 4~5 条）是
        # unaligned 兜底链的触发源；system 本就逐块构造，N 内联零成本。
        f"数量硬约束：frames 数组必须精确包含 {len(storyboard.frames)} 条，与输入旁白逐条对应，不得提前停止、不得合并、不得遗漏。\n"
        f"{camera_instruction}"
        f"{ir_instruction}"
        f"{cinematography_instructions}"
        f"{extra_system}"
    )

    # T5: title 注入用户消息（只改 LLM 输入，不碰输出契约，零解析风险）
    # E8: script_context（逐帧模式）——脚本全文上下文进 user 消息。
    # S2（2026-08-26 调度表计划）：调度行+邻行+硬指令位于 user 负载**首部**
    # （计划写死的组装秩序——调度优先级高于脚本上下文），title/narrations/
    # script_narrations 保持原相对顺序。
    user_payload: dict = {}
    if shot_direction:
        user_payload["shot_direction"] = shot_direction
        user_payload["shot_directive"] = _SHOT_DIRECTIVE
    user_payload["title"] = storyboard.title
    user_payload["narrations"] = formatted_script
    if script_context:
        user_payload["script_narrations"] = script_context.get("narrations", [])
        user_payload["current_frame_index"] = script_context.get("current_frame_index")
    joined = json.dumps(user_payload, ensure_ascii=False, indent=2)
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": joined},
    ]
    resp = await _invoke_llm_bounded(llm, messages)  # E5: 有界超时
    content = resp.content if hasattr(resp, "content") else str(resp)

    try:
        parsed = _parse_json_prompts(content, "frames")
        prompts = [entry.get("image_prompt", "") for entry in parsed if isinstance(entry, dict)]
        sources = [entry.get("visual_source", "") for entry in parsed if isinstance(entry, dict)]
        # Task 8：镜头语言写回**传入的 storyboard 帧**（副作用），而非扩返回元组。
        # 理由：返回值 (prompts, sources) 被 11 个测试 fake 与两条调用路径固定，
        # 改成 3-tuple 会牵动全部；调用方本就把 mini-storyboard 传进来，
        # 由调用方负责搬回真实帧（见 _fill_image_prompts_robust / _plan_course_batched）。
        _apply_camera_fields(storyboard.frames, parsed)
        _apply_ir_fields(storyboard.frames, parsed)
        # M2：出场声明写回（无名册时 no-op——帧保持 None = 旧行为）
        _apply_visible_fields(storyboard.frames, parsed, entity_roster, task_dir=task_dir, index_offset=index_offset)
        # T-I0-②（2026-08-17）：质量后缀追加退役（_enhance_prompt_quality 留 legacy）--
        # 逐帧同词结尾与「千篇一律」相关；产物保持 LLM 原文。
        return prompts, sources
    except (json.JSONDecodeError, IndexError, TypeError):
        logger.warning("Failed to parse enhanced course prompts, falling back")
        # T5: 解析失败兜底也传 title 上下文
        ctx = {"title": storyboard.title}
        # 2026-08-19（P0 补缺）：解析失败兜底同走风格守卫——style_prefix 与锚定卡
        # 均在作用域内，修前此分支调 _generate_image_prompts 未传 style_guard，
        # 静默丢失全部风格约束（与单帧兜底同型失守点）。
        _anchor_fb = getattr(storyboard, "global_anchor", None) or {}
        _guard_fb = _style_guard_instruction(style_prefix, str(_anchor_fb.get("anchor_card", "") or ""))
        prompts = await _generate_image_prompts(llm, [f.narration for f in storyboard.frames], context=ctx, style_guard=_guard_fb)
        # T-I0-②：同上，后缀追加退役。
        return prompts, [""] * len(prompts)
