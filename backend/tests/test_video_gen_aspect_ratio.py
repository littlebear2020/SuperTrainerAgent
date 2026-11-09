"""M2-2: ai_image aspect_ratio must map to Ark seedream-native sizes (not 1080x1920).

The ai_image path builds `size = f"{cfg.width}x{cfg.height}"` from StoryboardConfig and
passes it verbatim to the Ark seedream API. Ark only accepts a fixed size set; an
unrecognized size (e.g. "1080x1920") makes seedream fall back to its default (16:9),
so a 9:16 request yields 16:9 images. _aspect_to_size must therefore return the same
Ark-native sizes the ViMax path (provider_bridge._ASPECT_TO_SIZE) already uses.
"""

from app.gateway.video_gen.engines.vimax.provider_bridge import _ASPECT_TO_SIZE
from app.gateway.video_gen.service import _aspect_to_size


def test_ai_image_aspect_maps_to_ark_native():
    ark_native = set(_ASPECT_TO_SIZE.values())
    for aspect in ("9:16", "16:9", "1:1"):
        w, h = _aspect_to_size(aspect)
        size = f"{w}x{h}"
        assert size in ark_native, f"{aspect} -> {size} not Ark-native {sorted(ark_native)}"
