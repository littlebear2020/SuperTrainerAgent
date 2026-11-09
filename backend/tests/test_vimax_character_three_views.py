"""B5c: ViMax side/back portrait 注入用户三视图的 side/back。

修前 ``generate_side/back_portrait`` 只传 ``[front_image_path]``（生成的正面），用户上传/生成的
side/back 视图在 ViMax 边界被丢。修复后作额外参考图，提升跨视角一致性。A/B 目检留 live。
"""

from unittest.mock import MagicMock

import pytest
from vimax.agents.character_portraits_generator import CharacterPortraitsGenerator
from vimax.interfaces.character import CharacterInScene


class _Gen:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_single_image(self, **kw):  # noqa: ANN001,ANN003
        self.calls.append(kw)
        return MagicMock()


def _char() -> CharacterInScene:
    return CharacterInScene(
        idx=0,
        identifier_in_scene="小明",
        is_visible=True,
        static_features="红发男孩",
        dynamic_features="蓝色校服",
    )


@pytest.mark.asyncio
async def test_side_portrait_includes_user_side_back() -> None:
    gen = _Gen()
    g = CharacterPortraitsGenerator(
        image_generator=gen,
        character_reference="https://user/f.png",
        character_sheets=[{"front_url": "https://user/f.png", "side_url": "https://user/s.png", "back_url": "https://user/b.png"}],
    )
    await g.generate_side_portrait(_char(), front_image_path="/gen_front.png", style="watercolor")
    assert gen.calls, "应调 generate_single_image"
    refs = gen.calls[0]["reference_image_paths"]
    assert "/gen_front.png" in refs and "https://user/s.png" in refs and "https://user/b.png" in refs, refs


@pytest.mark.asyncio
async def test_back_portrait_includes_user_side_back() -> None:
    gen = _Gen()
    g = CharacterPortraitsGenerator(
        image_generator=gen,
        character_reference="https://user/f.png",
        character_sheets=[{"front_url": "https://user/f.png", "side_url": "https://user/s.png", "back_url": "https://user/b.png"}],
    )
    await g.generate_back_portrait(_char(), front_image_path="/gen_front.png", style="watercolor")
    refs = gen.calls[0]["reference_image_paths"]
    assert "/gen_front.png" in refs and "https://user/s.png" in refs and "https://user/b.png" in refs, refs


@pytest.mark.asyncio
async def test_no_sheets_keeps_front_only() -> None:
    gen = _Gen()
    g = CharacterPortraitsGenerator(image_generator=gen, character_reference="/f.png")
    await g.generate_side_portrait(_char(), front_image_path="/gen_front.png", style="watercolor")
    refs = gen.calls[0]["reference_image_paths"]
    assert refs == ["/gen_front.png"], refs


def test_init_extracts_user_side_back() -> None:
    g = CharacterPortraitsGenerator(
        image_generator=None,
        character_reference="/f.png",
        character_sheets=[{"front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png"}],
    )
    assert g._user_side == "/s.png"
    assert g._user_back == "/b.png"
