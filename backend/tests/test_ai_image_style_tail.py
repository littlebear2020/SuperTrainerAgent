# backend/tests/test_ai_image_style_tail.py
from app.gateway.video_gen.visual_sources.ai_image import compose_dispatch_prompt
from app.gateway.video_gen.visual_sources.graphic_text import _prompt_units

PREFIX = "写实商务实拍风格，自然窗光，浅景深虚化背景，纪实质感，现代办公环境，人物保持中远景"


def test_style_tail_appended():
    out = compose_dispatch_prompt("程序员在工位调试代码", "corporate_live", ark_budget=False)
    assert out.startswith("程序员在工位调试代码")
    assert out.endswith(PREFIX)


def test_unknown_style_untouched():
    assert compose_dispatch_prompt("一只猫", "default", ark_budget=False) == "一只猫"
    assert compose_dispatch_prompt("一只猫", None, ark_budget=False) == "一只猫"


def test_ark_budget_trims_to_300_units():
    base = "超长画面描述" * 200  # 1200 CJK 字符
    out = compose_dispatch_prompt(base, "corporate_live", ark_budget=True)
    assert _prompt_units(out) <= 300
    assert out.endswith(PREFIX)  # 裁头不裁尾，风格尾必须完整
