"""一期 C8：三轨风格措辞守护测试（历史三次修正事故的防回归位）。

三轨（docs/plans/2026-08-24-course-project-consistency-plan.md C8）：
1. bulk 路径 system 级 `_build_style_instruction`（orchestration/storyboard_planner.py）；
2. prompt 重生端点的 `with_style_prefix`（routers/video_gen.py，2 调用行）；
3. 角色图的 `_with_style`（character_sheet_generator.py，委托 planner.with_style_prefix，1 调用行）。

调用面口径：`with_style_prefix` 共 3 个调用行 / 2 个调用点（重生端点 ×2 + 角色图委托 ×1）。
注意 vimax 包内有同名异函数 `_with_style_prefix`（packages/vimax/.../script2video_pipeline.py:934），
本守护只锚 video_gen 模块，检索时勿混淆。第 4 轨（下发层风格尾 compose_dispatch_prompt，
决策 22）跟随项目时为空属预期，不纳入本守护。

完成判定：故意改一处措辞 → 测试红。
"""

from __future__ import annotations

import inspect
import re


def test_bulk_style_instruction_canonical_wording():
    """轨 1：bulk system 级风格硬约束措辞不被擅改（改词 → 红）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _build_style_instruction

    s = _build_style_instruction("水彩手绘风格")
    assert "全片画面风格统一为硬约束" in s, "bulk 风格硬约束措辞被改动"
    assert "每条描述必须呈现「水彩手绘风格」的风格特征" in s
    assert "禁止出现与「水彩手绘风格」冲突的画风或元素" in s
    # 空前缀 → 空指令（无前导噪声）
    assert _build_style_instruction("") == ""


def test_hard_prefix_wording_exact_separator():
    """轨 2/3 共用拼装：`with_style_prefix` 分隔符与空前缀语义（改词 → 红）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import with_style_prefix

    assert with_style_prefix("水彩手绘风格", "画面描述") == "水彩手绘风格，画面描述"
    assert with_style_prefix("", "画面描述") == "画面描述", "空前缀原样返回（无前导逗号）"


def test_character_sheet_rail_delegates_to_shared_assembler():
    """轨 3：角色图 `_with_style` 必须委托共用拼装（本地重写措辞 → 红）。"""
    from app.gateway.video_gen.character_sheet_generator import _with_style

    src = inspect.getsource(_with_style)
    assert "with_style_prefix(style_prefix, body)" in src, "_with_style 须委托 planner.with_style_prefix，禁止本地拷贝措辞"
    assert "，" not in re.sub(r'""".*?"""', "", src, flags=re.S).replace("with_style_prefix(style_prefix, body)", ""), "_with_style 函数体不得出现本地分隔符拼装（措辞拷贝漂移）"


def test_call_surface_three_lines_two_sites():
    """调用面口径：with_style_prefix 共 3 调用行 / 2 调用点（重生端点 ×2 + 角色图委托 ×1）。"""
    from app.gateway.routers import video_gen as router_mod

    rsrc = inspect.getsource(router_mod.regenerate_frame_prompt)
    assert len(re.findall(r"\bwith_style_prefix\(", rsrc)) == 2, f"重生端点应有 2 个 with_style_prefix 调用行，实际 {len(re.findall(r'with_style_prefix\\(', rsrc))}"

    from app.gateway.video_gen import character_sheet_generator as csg

    csrc = inspect.getsource(csg)
    assert len(re.findall(r"\bwith_style_prefix\(", csrc)) == 1, "character_sheet_generator 应恰有 1 个委托调用行"


def test_single_frame_guard_shares_bulk_instruction_builder():
    """单帧/重生路径的风格守卫与 bulk system 同源构造（措辞漂移 → 红）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _build_style_instruction, _style_guard_instruction

    guard = _style_guard_instruction("水彩手绘风格", "")
    assert _build_style_instruction("水彩手绘风格") in guard, "单帧守卫必须复用 bulk 的 _build_style_instruction 段"
