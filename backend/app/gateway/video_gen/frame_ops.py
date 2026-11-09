"""帧级操作的纯逻辑（2026-08-03 Task 14 从 service.py 抽出）。

service.py 曾达 3747 行：VideoGenService 53 个方法 + 1209 行游离模块函数。
本模块承载「不需要 service 实例状态」的帧级判定，使 VideoGenService 回归编排职责。
抽出是纯搬迁，零行为变化——守护测试见 tests/test_frame_ops_extraction.py。
"""

from __future__ import annotations


def frames_to_regenerate(frames, task_type: str) -> list[int]:  # noqa: ANN001
    """G2: 计算需要（重新）生成视觉的帧索引——类型感知的 E5c 续点。

    修前 ``only_frames=[f for f if not f.visual_url]`` 只看 visual_url 有无，把 draft 预览图
    (visual_type=image) 和冻结帧 (image_reuse) 都当成"已生成"跳过 → ai_video 任务里点过预览的
    帧最终是静态图。改为：仅当帧已有**期望类型的成功视觉**时才跳过，其余（预览图/冻结帧/缺失）都重生。

    期望类型按**逐帧 visual_source** 判断（非单一 task_type）——ai_visual mixed 任务里，
    ai_video 子源的帧期望 video，ai_image 子源的帧期望 image。visual_source 缺失时回落 task_type。
    image_reuse 是失败冻结标记，一律重生。

    content-match T3：graphic 帧期望 image（PNG 产物），与既有判定天然一致；但
    ``local_visual_path`` 是 gateway 产物（visuals/gfx_ 前缀）而非用户上传——用户把帧
    从 graphic 切回 ai_image 后，陈旧 gfx 卡必须重生成，不得像用户素材一样保留。
    """
    result: list[int] = []
    for f in frames:
        if not f.visual_url:
            # P0-1: 用户上传/替换的本地素材（visual_url=None + local_visual_path 有值）
            # 必须保留——不得当"无视觉"重抽覆盖（draft→render 上传被静默替换根因）。
            if getattr(f, "local_visual_path", None):
                # gfx PNG 只在帧仍是 graphic 时才算"已有成品"；切离 graphic 后是陈旧产物
                if (getattr(f, "visual_source", None) or task_type) == "graphic" or not str(f.local_visual_path).startswith(_GFX_VISUAL_PREFIX):
                    continue
            result.append(f.index)
            continue
        vs = getattr(f, "visual_source", None) or task_type
        expected = "video" if vs == "ai_video" else "image"
        if f.visual_type != expected:
            result.append(f.index)
    return result


# content-match T3：gateway graphic 渲染产物前缀（visuals/gfx_NNNN.png）。
# 区别于用户上传前缀（visuals/frame_，service._USER_VISUAL_PREFIX）：gfx 产物在帧
# 切离 graphic 后即陈旧，须重生成；用户素材永不重抽覆盖（P0-1 不变量）。
_GFX_VISUAL_PREFIX = "visuals/gfx_"


# ── S1（2026-08-26 镜头调度表计划）：插删帧行号重排 ─────────────────────
# 照抄 storyboard.remap_scene_groups_after_insert/delete 先例：纯函数、操作
# storyboard.json 原始 dict 行（service._add_frame/_delete_frame 的 updater 内联调用），
# 语义 = 旧行绑定随帧平移、内容不变。


def remap_shot_table_after_insert(rows: list[dict] | None, insert_index: int) -> list[dict] | None:
    """帧插入后的 shot_table 同步：frame_index ≥ insert_index 的行 +1。

    新帧**不在此造行**——行内容须由行级调度生成小调用产出（S3：±2 邻行 + 新帧
    旁白 + 锚定卡，仅 1 次 LLM），纯重排不凭空编造调度。无表原样返回（零行为变化）。
    """
    if not rows:
        return rows
    for r in rows:
        fi = r.get("frame_index")
        if isinstance(fi, int) and not isinstance(fi, bool) and fi >= insert_index:
            r["frame_index"] = fi + 1
    return rows


def remap_shot_table_after_delete(rows: list[dict] | None, deleted_index: int) -> list[dict] | None:
    """帧删除后的 shot_table 同步：移除被删帧的行，frame_index > deleted_index 的行 -1。"""
    if not rows:
        return rows
    kept = [r for r in rows if r.get("frame_index") != deleted_index]
    for r in kept:
        fi = r.get("frame_index")
        if isinstance(fi, int) and not isinstance(fi, bool) and fi > deleted_index:
            r["frame_index"] = fi - 1
    return kept
