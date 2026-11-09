"""B4: 项目 style_prefix 注入 ViMax frame prompts。

修前 ``ctx.style = payload.get("style")``（ViMax 任务美学旋钮），项目 ``style_prefix`` 完全
未接入 → 项目视觉风格预设对 ViMax 无效。修复：组合项目 prefix 前置到 ctx.style，经
``_with_style_prefix`` 进每帧 prompt。``style`` 在 vimax 管线仅作 prompt 前缀（非 lookup key），
故组合安全。A/B 目检留 live（不同风格预设下 ViMax 成片风格应有差异）。
"""

from app.gateway.video_gen.service import _combine_vimax_style


def test_combine_project_prefix_with_task_style() -> None:
    assert _combine_vimax_style("realistic movie style", "watercolor, high quality") == "watercolor, high quality, realistic movie style"


def test_combine_project_prefix_no_task_style() -> None:
    assert _combine_vimax_style(None, "watercolor") == "watercolor"


def test_no_project_returns_task_style() -> None:
    assert _combine_vimax_style("realistic movie style", None) == "realistic movie style"


def test_no_project_no_task_returns_none() -> None:
    assert _combine_vimax_style(None, None) is None


def test_payload_default_treated_as_unset() -> None:
    """Bug 2: schema 默认 'default' 视为未设置，不拼进 prompt。"""
    from app.gateway.video_gen.service import _combine_vimax_style

    assert _combine_vimax_style("default", "watercolor") == "watercolor"
    assert _combine_vimax_style("default", None) is None


def test_real_style_still_combines_v2() -> None:
    from app.gateway.video_gen.service import _combine_vimax_style

    assert _combine_vimax_style("realistic movie style", "watercolor") == "watercolor, realistic movie style"
