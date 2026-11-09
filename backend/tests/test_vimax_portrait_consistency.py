"""Regression tests for ViMax character-portrait "costume drift" (验收一致性问题).

Real run vg_8159cd6cc3fa showed the FRONT portrait correctly rendered the courier
in a blue delivery jacket + yellow badge + helmet, but the SIDE portrait "changed
clothes" into a plain hoodie (lost badge + helmet). Root cause: the side/back
prompts only format ``{identifier}`` — they omit ``style`` and the character's
clothing (``dynamic_features``). The reference image (front portrait) constrains
likeness, but the image model still needs a TEXTUAL anchor for costume details to
keep them stable across views. This file pins that the side/back prompts carry the
style + clothing anchor.

These touch vendored ``packages/vimax`` — a deliberate, minimal, well-marked vendor
patch (plan §2.1 allows vendor edits; the cost is tracked). Tests assert on the
prompt string actually sent to the image generator.
"""

from unittest.mock import AsyncMock

import pytest
from vimax.agents.character_portraits_generator import (
    CharacterPortraitsGenerator,
)
from vimax.interfaces import CharacterInScene


def _courier() -> CharacterInScene:
    return CharacterInScene(
        idx=0,
        identifier_in_scene="Xiaoming the delivery courier",
        is_visible=True,
        static_features="young male, short black hair, slim build",
        dynamic_features="wearing a blue delivery jacket with a yellow chest badge, dark gray pants, and a bicycle helmet",
    )


def _captured_prompt(generator: CharacterPortraitsGenerator) -> str:
    """Pull the prompt arg from the mocked image_generator's last call."""
    call = generator.image_generator.generate_single_image.await_args
    assert call is not None, "generate_single_image was never awaited"
    return call.kwargs.get("prompt") or (call.args[0] if call.args else "")


# ── RED: side/back prompts must anchor style + clothing, not just identifier ──


@pytest.mark.asyncio
async def test_side_portrait_prompt_carries_style_and_clothing():
    """The side-view prompt must restate ``style`` and the character's clothing
    (``dynamic_features``) so the image model keeps costume details stable across
    views — fixing the '换装' (costume drift) seen in the real run."""
    gen = CharacterPortraitsGenerator(image_generator=AsyncMock())
    gen.image_generator.generate_single_image = AsyncMock(return_value=object())

    await gen.generate_side_portrait(_courier(), front_image_path="/tmp/front.png", style="realistic movie style")

    prompt = _captured_prompt(gen)
    assert "realistic movie style" in prompt, "side prompt dropped the style anchor"
    assert "yellow chest badge" in prompt or "delivery jacket" in prompt, "side prompt dropped the clothing anchor (costume drift risk)"


@pytest.mark.asyncio
async def test_back_portrait_prompt_carries_style_and_clothing():
    gen = CharacterPortraitsGenerator(image_generator=AsyncMock())
    gen.image_generator.generate_single_image = AsyncMock(return_value=object())

    await gen.generate_back_portrait(_courier(), front_image_path="/tmp/front.png", style="realistic movie style")

    prompt = _captured_prompt(gen)
    assert "realistic movie style" in prompt, "back prompt dropped the style anchor"
    assert "yellow chest badge" in prompt or "delivery jacket" in prompt, "back prompt dropped the clothing anchor (costume drift risk)"


@pytest.mark.asyncio
async def test_front_portrait_still_carries_style_and_features():
    """Front portrait already anchors style + features (the baseline that worked).
    Pin it so the consistency fix doesn't regress the one view that was correct."""
    gen = CharacterPortraitsGenerator(image_generator=AsyncMock())
    gen.image_generator.generate_single_image = AsyncMock(return_value=object())

    await gen.generate_front_portrait(_courier(), style="realistic movie style")

    prompt = _captured_prompt(gen)
    assert "realistic movie style" in prompt
    assert "yellow chest badge" in prompt or "delivery jacket" in prompt
