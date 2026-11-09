"""视觉风格预设查表 —— 任务级与项目级的**单一来源**（Task 1/2，2026-08-01）。

为什么独立成模块：`service.py` 已 import `course_models`，若把表放在 `service`
里再由 `course_models` 反向 import 就成环。表下沉到本模块，两边都单向依赖它。

三条不变量：
1. **prefix 一律中文。** 原先是英文，而这段英文被塞进中文 system prompt 要求
   「每条画面描述必须以此开头」，LLM 顺势把整条 prompt 写成英文 —— 即用户反馈 5
   「选了风格后提示词变英文」的主因。中文对当前默认生图 provider（Ark seedream）
   已在生产验证（vg_d0779ddd81f8），Ark 官方文档亦明写 prompt 支持 ≤300 汉字。
2. **key 语言无关。** UI 的 value 绝不能直接进 prompt（项目级历史上就是把
   `'3D render'` 原样拼进去的）。
3. **旧值只读兼容。** 存量草稿 payload 与存量项目文件都不能 422、也不能因查不到
   而退化成无风格前缀。
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# 风格 key → 生图 prompt 的中文视觉风格前缀。
# key 与 frontend/src/core/video-gen/style-presets.ts 的 STYLE_PRESETS 一一对应。
STYLE_PREFIXES: dict[str, str] = {
    # ── 职场 ──
    "corporate": "商务专业风格，简洁构图，柔和均匀打光",
    "education_flat": "扁平插画风格，几何色块，清晰线条，明快配色",
    "education_3d": "3D 渲染风格，立体质感，柔和环境光，微缩场景感",
    "minimalist": "极简图形风格，大量留白，单一主体，克制配色",
    # ── 实景 ──
    "photorealistic": "写实摄影风格，真实质感，自然光线，细腻材质",
    "documentary": "纪录片质感，抓拍视角，真实环境光，克制调色",
    # ── 叙事 ──
    "cinematic": "电影质感画面，戏剧性光影，浅景深，经过调色",
    "warm_casual": "轻松生活化摄影，温暖自然光，放松氛围",
    # ── 旧值别名（存量草稿 / 存量项目仍在用，删除会退化为无前缀）──
    # professional/casual：任务级旧下拉值
    "professional": "商务专业风格，简洁构图，柔和均匀打光",
    "casual": "轻松生活化摄影，温暖自然光，放松氛围",
    # watercolor：UI 已移除「水彩手绘」，但实测存量项目 cp_a9bd1420219b 存的就是它
    "watercolor": "水彩手绘风格，柔和笔触，通透质感，纸纹肌理",
    "default": "",
}

# ── 五风格体系（2026-08-17 Phase 0 样张 20/20 用户终审进 v1；词域=验证样张原文提炼）──
# 视频侧：精密工程/纸艺拼贴/纸艺剧场/3D角色/商务实景；图片侧：精密工程/纸艺拼贴/
# 等距信息图/白板手绘/商务实景。三风格两用（工程/拼贴/实景），四档专属。
# 原理：「物理世界模拟器」--风格以真实物理材质存在（金属/纸/黏土/真人）才可靠。
# 词域含材质+光线+构图留白（图片侧文字友好）+调性；全链路中文（D-B）。
STYLE_PREFIXES.update(
    {
        "precision_eng": "写实3D精密工程风格，哑光拉丝金属材质，冷白主光从侧上方打入，灰白工程美学基调，细腻材质纹理，背景浅景深虚化",
        "paper_collage": "纸艺拼贴风格，米白色纹理纸背景，真实纸张厚度与柔和投影，边缘裁切毛边，黑色半调网点点缀",
        "papercraft_theater": "纸艺定格剧场风格，立体纸雕舞台分层布景，纸偶关节与折痕可见，暖黄灯光下纸张投影清晰，前后景层次分明",
        "character_3d": "皮克斯风格3D卡通渲染，Q版角色大头短身轮廓圆润，柔和次表面散射皮肤质感，暖色调柔和窗光，肢体弹性挤压感",
        "corporate_live": "写实商务实拍风格，自然窗光，浅景深虚化背景，纪实质感，现代办公环境，人物保持中远景",
        "isometric_info": "2.5D等距视角信息图风格，浅灰蓝纯色背景，柔和等距投影，明快清爽的蓝橙白配色，边缘锐利的干净渲染，画面大面积留白",
        "whiteboard": "白板手绘信息图风格，纯白背景，黑色马克笔手绘线条勾勒，蓝色与黄色荧光笔色块点缀，笔触自然流畅的简笔画",
    }
)

# 五风格清单（供前端/文档引用；education_flat 不在其中=退役，仅存量只读）。
VIDEO_STYLE_KEYS = ("precision_eng", "paper_collage", "papercraft_theater", "character_3d", "corporate_live")
IMAGE_STYLE_KEYS = ("precision_eng", "paper_collage", "isometric_info", "whiteboard", "corporate_live")


# ── 风格锚图「无人物」变体（2026-08-30 方案 B）────────────────────────────────
# 背景：AI 生成风格锚图是纯文生图，模型自由发挥容易把人物画进风格图，与角色三视图
# 冲突。方案 B 拍板：默认不勾选「包含人物」时，改用本表的去人物变体前缀 + 全局
# NO_PEOPLE_DIRECTIVE 硬约束。只给前缀里本就含人物/角色词的 key 定义变体（不做
# 字符串替换——脆弱），其余 key 由 style_prefix_for 回落原 prefix。
# 完整性 guard：tests/test_course_project_style_anchor.py::test_tc_c6_07b ——
# STYLE_PREFIXES 含人物词的 key 必须都在本表有变体，新增预设时防漏。
STYLE_PREFIX_NO_PEOPLE: dict[str, str] = {
    # 原："……现代办公环境，人物保持中远景"
    "corporate_live": "写实商务实拍风格，自然窗光，浅景深虚化背景，纪实质感，现代办公环境，空景与静物特写为主",
    # 原："……Q版角色大头短身轮廓圆润，……肢体弹性挤压感"
    "character_3d": "皮克斯风格3D卡通渲染，柔和次表面散射质感，暖色调柔和窗光，Q版场景与道具轮廓圆润",
    # 原："立体纸雕舞台分层布景，纸偶关节与折痕可见"——纸偶即角色，须去人物化
    "papercraft_theater": "纸艺定格剧场风格，立体纸雕舞台分层布景，纸张折痕与剪纸层次可见，暖黄灯光下纸张投影清晰，前后景层次分明",
}

# 无人物硬约束：拼接在风格锚图 prompt 末尾，确保所有风格预设下都生效。
NO_PEOPLE_DIRECTIVE = "画面中不要出现任何人物、人脸或角色，仅展示场景、材质与氛围"

# 人物词表（单一来源，2026-08-31 起）：guard 测试（前缀含任一词的 key 必须配
# STYLE_PREFIX_NO_PEOPLE 变体）与 style_prefix_for 运行时告警共用。初版测试文件里
# 只列「人物/角色/纸偶」三个词——B1 教训说明词表漏收即防线失效（纸偶即角色但
# 差点没人想到）。新增预设/改写前缀时若用到下列近义词，必须同时配无人物变体。
PERSON_TERMS: tuple[str, ...] = (
    "人物",
    "角色",
    "人像",
    "人脸",
    "真人",
    "模特",
    "演员",
    "男孩",
    "女孩",
    "少年",
    "少女",
    "男人",
    "女人",
    "舞者",
    "娃娃",
    "纸偶",
    "卡通形象",
)


# 存量英文字面量 → 当前风格 key 的只读别名映射。
#
# 项目级 visual_style_preset 历史上把前端下拉的 **英文 value 原样存库**，
# 且 course_models.style_prefix 把它**原样拼进生图 prompt** —— 反馈 5 的根因之一。
# 实测存量数据：
#   cp_f3d9cc794a25='3D render'、cp_d11fb1691f4f='flat illustration'、
#   cp_a9bd1420219b='watercolor'
# 只读兼容：不写库、不做 DB 迁移（与保留 professional/casual 同理）。
# 与 frontend 的 LEGACY_STYLE_VALUES 保持一致。
_LEGACY_STYLE_ALIASES: dict[str, str] = {
    "3D render": "education_3d",
    "flat illustration": "education_flat",
    "professional": "corporate",
    "casual": "warm_casual",
}


def resolve_style_key(value: str | None) -> str:
    """Normalise any historical style value to a current key.

    新 key 原样返回；存量英文字面量按 ``_LEGACY_STYLE_ALIASES`` 翻译；
    **未知值原样返回（不猜）** —— 由调用方决定如何处理，避免静默改写用户数据。
    """
    if not value:
        return ""
    # 已是当前表里的 key（含 watercolor 等保留项）→ 原样用
    if value in STYLE_PREFIXES:
        return value
    return _LEGACY_STYLE_ALIASES.get(value, value)


def style_prefix_for(value: str | None, *, no_people: bool = False) -> str:
    """把任意历史/当前风格值翻译成中文 prefix；未知或未设置返回空串。

    未知值**不原样输出** —— 它可能是英文字面量，原样拼进 prompt 正是要修的 bug。

    ``no_people=True``（风格锚图默认路径）优先返回 STYLE_PREFIX_NO_PEOPLE 的去人物
    变体；该 key 无变体时回落原 prefix，行为与旧调用完全一致。
    """
    key = resolve_style_key(value)
    if no_people:
        variant = STYLE_PREFIX_NO_PEOPLE.get(key)
        if variant is not None:
            return variant
    prefix = STYLE_PREFIXES.get(key, "")
    if no_people and prefix:
        # B1 教训的制度化出口：无变体但前缀含人物词（词表漏收/新预设漏配变体）→
        # 与 NO_PEOPLE_DIRECTIVE 拼出自相矛盾 prompt。guard 测试挡 CI，这里挡线上
        # ——响亮留痕，不静默。
        hit = next((t for t in PERSON_TERMS if t in prefix), None)
        if hit:
            logger.warning(
                "style_prefix_for(no_people=True): key=%r 无无人物变体且前缀含人物词「%s」，将拼出自相矛盾 prompt——须补 STYLE_PREFIX_NO_PEOPLE[%r]",
                key,
                hit,
                key,
            )
    return prefix
