"""T6 词表数据文件（Phase 1）：两方言的镜头/术语/约束词映射--纯数据，升级方言只改表。

依据：docs/video-gen-prompt-translator-plan-2026-08-16.md §一 方言对照表
（MiniMax 官方 skill + 火山官方/社区指南交叉验证）。D-B（08-17 拍板）：H3 中文直出，
故 H3 侧术语为中文表述（官方三维语法概念保留：类型+幅度+速度）。
"""

from __future__ import annotations

# ── H3（MiniMax，中文三字段）──────────────────────────────────────────────
# 运镜三维语法（类型+幅度+速度）官方为英文；D-B 中文拍板后用中文等效表述。
H3_SHOT_TERMS: dict[str, str] = {
    "wide": "远景",
    "medium": "中景",
    "close": "特写",
}
H3_CAMERA_TERMS: dict[str, str] = {
    "static": "镜头保持固定机位",
    "push_in": "镜头以缓慢的速度小幅推近",
    "pull_out": "镜头以缓慢的速度小幅拉远",
    "pan": "镜头平稳地横向摇移",
    "tilt": "镜头平稳地纵向摇移",
}
# 公共不变量：worker concat -an 丢弃模型音频 -> 禁台词/对白；独立烧字幕 -> 无画面文字。
H3_CONSTRAINT_TAIL = "全程无人物对白与口型动作，画面不出现字幕、水印与任何文字。"
H3_MUSIC_NA = "N/A"  # BGM 由管线混（non_diegetic_music 恒 N/A）

# ── Seedance 2.0（火山系：seedance-ark 直连 + seedance 360 通道）─────────
SEEDANCE_SHOT_TERMS: dict[str, str] = {
    "wide": "远景",
    "medium": "中景",
    "close": "特写",
}
# 官方术语（一镜一运镜）。
SEEDANCE_CAMERA_TERMS: dict[str, str] = {
    "static": "固定镜头",
    "push_in": "缓慢推镜",
    "pull_out": "缓慢拉镜",
    "pan": "平稳横移",
    "tilt": "纵向摇镜",
}
SEEDANCE_CONSTRAINT_TAIL = "保持无字幕，不要生成任何水印或Logo标识，画面中不出现文字。"


def seedance_reference_injection(reference_count: int, kind: str | None = None, entity_names: list[str] | None = None, prop_count: int = 0) -> str:
    """参考图分工指代（T4）：传图必须显式指代（官方要求；修「传图不指代」gap）。

    kind = 参考图来源（R1 修订 2026-08-18：来源语义不同，文案必须区分——
    角色三视图的图片1是人物肖像，说「构图参考」会误导模型）：
    - "character"（角色三视图）：角色外观参考（不提构图/首帧）
    - "preview"（预览图首帧继承）：首帧参考（构图色调一致）
    - "preview_first"（2026-08-31 机制 M 混合形态）：图片1=预览图首帧 + 图片2..n=
      角色/道具外观参考（entity_names 对应图片 2..n，不含预览图——1:1 规则）
    - "group_anchor"（T-S2 组锚）：同组场景参考（场景光线一致、动作按本镜）
    - "style_anchor"（二期 M5 视频侧兜底）：系列风格参考（只约束色调画风，非首帧）
    - None/generic：中性首帧+构图表述（向后兼容既有测试）

    entity_names（二期 M4 带名指代）：帧声明实体名，**与参考图 1:1 对应且同序**
    （来源 = 帧级 visible_* 声明，视频侧每实体 1 张视图时成立；三视图全传等
    非 1:1 形态不传 → 现状文案）。传 names 且 kind="character" → 带名定稿文案。
    names 多于张数时按张数截断（防御调用方错位，不虚报图片序号）。

    prop_count（X8，2026-08-27 合成图方案 + 用户拍板区分措辞）：``entity_names``
    末尾 prop_count 个条目是道具（注入顺序 角色 → 道具）。角色条目标
    「{name}的三视图」（注入的是三视图合成图，强化模型读作单角色多视角），
    道具条目标「{name}的产品参考图」（道具实际只传 1 张主图——统一标三视图
    会诱导模型画道具三联格）。缺省 0 = 全按角色（窄签名调用方零改动）。
    """
    if reference_count <= 0:
        return ""
    if kind == "preview_first":
        # 机制 M（2026-08-31）混合形态：图片1=预览图（首帧，构图色调锚定），图片2..n=
        # 角色/道具参考。entity_names 1:1 对应图片 2..n（不含预览图）——带名时按
        # M4/X8 规则给 2..n 逐图分工措辞（序号 +1 偏移），否则通用分工文案。
        named = ""
        if entity_names:
            prop_start = len(entity_names) - prop_count if prop_count > 0 else len(entity_names)
            parts = [f"图片{i + 2}为{n}的产品参考图" if i >= prop_start else f"图片{i + 2}为{n}的三视图" for i, n in enumerate(entity_names[: reference_count - 1])]
            named = f"；{'，'.join(parts)}。仅生成被提及的角色与产品，外观与服装保持与对应参考图一致"
        return f"参考图分工：图片1为本镜预览图，作为首帧参考，构图与色调保持一致{named}。场景与动作按本镜描述生成。"
    if kind == "character" and entity_names:
        prop_start = len(entity_names) - prop_count if prop_count > 0 else len(entity_names)
        parts: list[str] = []
        for i, n in enumerate(entity_names[:reference_count]):
            parts.append(f"图片{i + 1}为{n}的产品参考图" if i >= prop_start else f"图片{i + 1}为{n}的三视图")
        named = "，".join(parts)
        return f"参考图分工：{named}。仅生成被提及的角色与产品，外观与服装保持与对应参考图一致，场景与动作按本镜描述生成。"
    if kind == "style_anchor":
        # M5 视频侧兜底锚图（定稿文案）：锚图是风格参考不是首帧——只约束色调画风。
        return f"图片{reference_count}为本系列风格参考，色调与画风保持一致，主体动作按本镜描述生成。"
    if kind == "character":
        return "参考图为角色外观参考，角色形象与服装保持一致，场景与动作按本镜描述生成。"
    if kind == "preview":
        return "图片1为本镜预览图，作为首帧参考，构图与色调保持一致。"
    if kind == "group_anchor":
        return "图片1为同组锚定场景参考，场景与光线保持一致，主体动作按本镜描述执行。"
    if reference_count == 1:
        return "图片1作为首帧与构图参考，画面主体保持一致。"
    return "图片1作为首帧与构图参考，图片2及以后作为角色外观参考，各参考图分工使用，画面主体保持一致。"


def h3_reference_injection(reference_count: int, kind: str | None = None, entity_names: list[str] | None = None, prop_count: int = 0) -> str:
    """H3 r2v 参考图分工指代（2026-09-04 H3 多图参考计划 S3）。

    与 :func:`seedance_reference_injection` 同构但**独立维护**（两套方言 prompt
    设计独立，用户红线：seedance 侧零改动）。r2v 语义下参考图全量进 content[]
    （360 网关全局 extra_body.role="reference"），声明必须与实际下发图集一致：
    - "character"（角色/道具参考，r2v 全量下发）：entity_names 存在 → 逐图分工
      （1:1 同序纪律同 seedance 侧：角色「{name}的三视图」/ 道具「{name}的产品
      参考图」，末尾 prop_count 个条目是道具），收尾锁定生成范围；names 缺失
      （非 roster 任务级角色图）→ 通用角色外观措辞；
    - "style_anchor"（风格锚图兜底，A/B 实测定稿走 r2v）：「图片N 为场景/风格
      参考」——只约束色调画风，非首帧；
    - "preview"/"preview_first"/"group_anchor"/None（i2v 首帧路径——参考图未
      实际全量下发或仅首图）→ **返回空串**（不得声明未下发的图）。
    """
    if reference_count <= 0:
        return ""
    if kind in ("preview", "preview_first", "group_anchor", None):
        return ""
    if kind == "character" and entity_names:
        prop_start = len(entity_names) - prop_count if prop_count > 0 else len(entity_names)
        parts = [f"图片{i + 1}为{n}的产品参考图" if i >= prop_start else f"图片{i + 1}为{n}的三视图" for i, n in enumerate(entity_names[:reference_count])]
        return f"参考图分工：{'，'.join(parts)}。仅生成被提及的角色与产品，外观与对应参考图一致。"
    if kind == "character":
        return "参考图为角色外观参考，角色形象与服装保持一致，场景与动作按本镜描述生成。"
    if kind == "style_anchor":
        return f"图片{reference_count}为场景/风格参考，色调与画风保持一致，主体动作按本镜描述生成。"
    return ""
