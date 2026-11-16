from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
IMAGE_CARDS_ROOT = REPO_ROOT / "skills/public/trainerai-image-cards"
COMIC_ROOT = REPO_ROOT / "skills/public/trainerai-comic"
CREATIVE_STUDIO_SOUL = REPO_ROOT / "backend/seed/agents/creative-studio/SOUL.md"
IMAGE_CARD_UNSUPPORTED = re.compile(r"portrait-3-4|portrait-2-3|3:4|2:3|4:5|1080×1350|1242×(?:1242|1660|1863)")
COMIC_UNSUPPORTED = re.compile(r"3:4|4:3|2:3|4:5")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _assert_image_card_contract(skill_root: Path) -> None:
    canvas = _read(skill_root / "references/elements/canvas.md")
    assert "| square | 1:1 | 2048×2048 |" in canvas
    assert "| landscape | 16:9 | 2048×1152 |" in canvas
    assert "| portrait-9-16 | 9:16 | 1152×2048 |" in canvas
    assert "**Default**: square" in canvas

    skill = _read(skill_root / "SKILL.md")
    assert "default `1:1` (`2048×2048`" in skill
    assert "`9:16` (`1152×2048`" in skill
    assert "`16:9` (`2048×1152`" in skill

    prompt = _read(skill_root / "references/workflows/prompt-assembly.md")
    assert prompt.count("**Aspect Ratio**: 1:1") == 2
    assert "keep aspect ratio `1:1`" in prompt

    presets = sorted((skill_root / "references/presets").glob("*.md"))
    assert presets
    for preset in presets:
        assert "ratio: square" in _read(preset), f"{preset.name} must default to square cards"

    for file in skill_root.rglob("*.md"):
        match = IMAGE_CARD_UNSUPPORTED.search(_read(file))
        assert match is None, f"{file} advertises unsupported 360 output ratio/size: {match.group(0) if match else ''}"


def _assert_soul_contract(soul_path: Path) -> None:
    soul = _read(soul_path)
    assert "图片卡片默认 1:1 / 2048×2048" in soul
    assert "漫画和明确竖版场景默认 9:16 / 1152×2048" in soul
    assert "横版 16:9 / 2048×1152" in soul
    assert "卡片默认 1080×1350" not in soul


def _assert_comic_contract(skill_root: Path) -> None:
    skill = _read(skill_root / "SKILL.md")
    assert "| `--aspect` | 9:16 (default, portrait), 16:9 (landscape), 1:1 (square) |" in skill
    assert "aspect `16:9`" in skill
    assert "default `9:16`" in skill
    assert "`9:16` → `1152×2048`" in skill
    assert "`16:9` → `2048×1152`" in skill
    assert "`1:1` → `2048×2048`" in skill

    assert "**Aspect Ratio**: 9:16" in _read(skill_root / "references/base-prompt.md")
    assert 'aspect_ratio: "9:16"' in _read(skill_root / "references/storyboard-template.md")
    assert 'aspect_ratio: "9:16"' in _read(skill_root / "references/analysis-framework.md")
    assert "default `9:16`" in _read(skill_root / "references/workflow.md")

    four_panel = _read(skill_root / "references/presets/four-panel.md")
    assert "| Aspect | 16:9 (landscape) |" in four_panel
    assert "--aspect 16:9" in four_panel
    assert "Recommended page aspect: **16:9**" in _read(skill_root / "references/layouts/four-panel.md")

    for file in skill_root.rglob("*.md"):
        match = COMIC_UNSUPPORTED.search(_read(file))
        assert match is None, f"{file} advertises unsupported 360 page ratio: {match.group(0) if match else ''}"


def test_image_card_defaults_and_prompts_use_actual_360_ratio_size_tiers() -> None:
    _assert_image_card_contract(IMAGE_CARDS_ROOT)


def test_creative_studio_soul_matches_card_comic_and_portrait_defaults() -> None:
    _assert_soul_contract(CREATIVE_STUDIO_SOUL)


def test_comic_page_defaults_and_prompts_use_only_actual_360_ratios() -> None:
    _assert_comic_contract(COMIC_ROOT)
