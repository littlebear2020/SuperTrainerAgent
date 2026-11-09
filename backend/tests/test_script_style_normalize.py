"""F4 Bug 2: normalize_script 的 'default' 归一（全局，覆盖 generate 模式文档归一）。"""

from app.gateway.video_gen.orchestration.script import _effective_script_style


def test_default_normalized_to_kouyu() -> None:
    assert _effective_script_style("default") == "口语"
    assert _effective_script_style(None) == "口语"


def test_real_style_kept() -> None:
    assert _effective_script_style("书面") == "书面"
    assert _effective_script_style("口语") == "口语"
