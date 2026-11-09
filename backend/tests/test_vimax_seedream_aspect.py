# backend/tests/test_vimax_seedream_aspect.py
import inspect

from app.gateway.video_gen.engines.vimax import provider_bridge as pb


def test_aspect_to_size_helper():
    """13: seedream 须把 aspect_ratio → size（不再硬编码 9:16）。"""
    assert pb._aspect_to_size("16:9") == "2048x1152"
    assert pb._aspect_to_size("9:16") == "1152x2048"
    assert pb._aspect_to_size("1:1") == "1152x1152"
    assert pb._aspect_to_size(None) == "1152x2048"  # 默认 9:16


def test_seedream_generator_honors_aspect_ratio():
    """13: generate_single_image 须用 _aspect_to_size(self._aspect_ratio)，不再硬编码。"""
    src = inspect.getsource(pb.SeedreamGeneratorForViMax.generate_single_image)
    assert "_aspect_to_size" in src, "须把 aspect_ratio → size，不再硬编码 1152x2048"
