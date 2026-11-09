"""项目级 style_prefix 中文化 + 删 color_palette 噪声（Task 2 Step 4b，2026-08-01）。

**为什么这条链路必须单独测：** 初版计划只改任务级 `STYLE_PREFIXES`，但真实任务
`vg_d0779ddd81f8` 走的是**项目级**链路（`course_pipeline.build_style_context` →
`_load_course_project_style` → `plan_course(style_context=...)`），
完全绕开 `STYLE_PREFIXES`。所以只修任务级对「关联了项目的任务」一点用都没有。

该任务 storyboard.json 里的实际画面词（本地已复核）：

    3D render, saturation: 1, contrast: 1, professional lighting, high quality,
    detailed. 现代都市CBD全景，玻璃幕墙写字楼群…[中文正文], high quality, ...

即**中文正文被英文前缀 + 英文后缀夹在中间**。三处来源：
1. `visual_style_preset` 存的英文字面量 `'3D render'` 被原样拼进 prompt；
2. `color_palette` 的 `saturation: 1, contrast: 1` —— 它本是给 ffmpeg `eq` 滤镜的
   **数值参数**（`_ffmpeg_helpers._colorgrade_vf` 已消费并真实生效），拼进 prompt
   属重复消费，且 `1` 是恒等值，对生图模型是纯噪声 → **删而非译**；
3. 末尾写死的 `"professional lighting, high quality, detailed"`。
"""

from __future__ import annotations

from app.gateway.video_gen.course_models import CourseProject


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def test_style_prefix_is_chinese_and_drops_palette():
    """存量英文值须被翻译成中文，palette 噪声与英文质量词须消失。"""
    p = CourseProject(
        id="x",
        user_id="u",
        title="t",
        visual_style_preset="3D render",  # 存量英文字面量（实测 cp_f3d9cc794a25）
        color_palette={"saturation": 1, "contrast": 1},
    )
    s = p.style_prefix

    assert "3D render" not in s, "旧英文值须被翻译，不得原样进 prompt"
    assert "saturation" not in s, "palette 是 ffmpeg 参数，不进 prompt"
    assert "contrast" not in s, "palette 是 ffmpeg 参数，不进 prompt"
    assert "professional lighting" not in s, "末尾写死的英文质量词须移除"
    assert "high quality" not in s, "英文质量词由 _enhance_prompt_quality 负责，此处重复"
    assert _has_cjk(s), f"prefix 应含中文，实际={s!r}"
    # 应命中 education_3d 的中文 prefix
    assert "3D 渲染" in s or "立体质感" in s, f"应翻译为 3D 渲染风格，实际={s!r}"


def test_style_prefix_translates_all_legacy_english_literals():
    """三个实测存量英文值都要能翻译（不能只修一个）。"""
    for legacy, expect_kw in (
        ("3D render", "3D 渲染"),
        ("flat illustration", "扁平插画"),
        ("watercolor", "水彩"),
    ):
        p = CourseProject(id="x", user_id="u", title="t", visual_style_preset=legacy)
        s = p.style_prefix
        assert expect_kw in s, f"{legacy} 应翻译含「{expect_kw}」，实际={s!r}"
        assert legacy not in s, f"{legacy} 不得原样出现在 prompt 里"


def test_style_prefix_accepts_new_keys():
    """新 key（前端现在存的就是这些）同样要能查到中文 prefix。"""
    p = CourseProject(id="x", user_id="u", title="t", visual_style_preset="corporate")
    s = p.style_prefix
    assert "商务专业" in s, f"corporate 应译为商务专业风格，实际={s!r}"
    assert _has_cjk(s)


def test_style_prefix_unset_yields_empty():
    """未设置视觉风格时不得凭空注入英文质量词（原实现即使无 preset 也会拼一句英文）。"""
    p = CourseProject(id="x", user_id="u", title="t")
    s = p.style_prefix
    assert "professional lighting" not in s
    assert "high quality" not in s
    # 无风格、无角色、无场景参考 → 应为空（或纯空白）
    assert s.strip() == "", f"未设置任何风格要素时应为空，实际={s!r}"


def test_style_prefix_keeps_character_and_scene_refs():
    """角色描述保留；scene_style_refs 拼接已退役（C2 决策 7，2026-08-24 计划改写）。

    原断言「ref_a.png 不得丢失」——该字段名存实亡（URL 从未真正进生图通道），用户拍板
    静默退役；风格参考职责移交 style_anchor_image（C1/C4）。角色描述（本来就是中文，
    属有效信息）仍须保留。
    """
    p = CourseProject(
        id="x",
        user_id="u",
        title="t",
        visual_style_preset="corporate",
        character_reference="一位女讲师，深色西装",
        scene_style_refs=["ref_a.png", "ref_b.png"],
    )
    s = p.style_prefix
    assert "一位女讲师" in s, "角色描述不得丢失"
    assert "ref_a.png" not in s, f"场景风格图拼接已退役（决策 7），实际={s!r}"
    assert "商务专业" in s


def test_style_prefix_unknown_value_is_not_leaked_verbatim():
    """未知风格值不得原样进 prompt —— 它很可能就是个英文字面量。"""
    p = CourseProject(id="x", user_id="u", title="t", visual_style_preset="some unknown english style")
    s = p.style_prefix
    assert "some unknown english style" not in s, "未知值原样进 prompt 正是要修的 bug"


def test_color_palette_still_reaches_ffmpeg_side():
    """回归护栏：palette 从 prompt 删除，但**不得**把字段本身删掉。

    它仍要留在模型上供 worker 的 ffmpeg `eq` 滤镜消费 —— 删字段会让调色功能失效。
    """
    palette = {"saturation": 1.2, "contrast": 1.1}
    p = CourseProject(id="x", user_id="u", title="t", color_palette=palette)
    assert p.color_palette == palette, "color_palette 字段必须保留（ffmpeg 侧仍在用）"
    assert "saturation" not in p.style_prefix, "但不进 prompt"
