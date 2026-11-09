import inspect

from app.gateway.video_gen.providers.seedream import SeedreamImageProvider


def test_seedream_generate_image_consumes_reference_image_paths():
    """C: SeedreamImageProvider 须消费 reference_image_paths（i2i via Ark image 字段），
    不再静默丢弃。用源码契约断言（端到端需 live ARK_API_KEY，重）。"""
    src = inspect.getsource(SeedreamImageProvider.generate_image)
    assert "reference_image_paths" in src, "seedream 须读取 reference_image_paths kwarg"
    assert 'body["image"]' in src, "非空 refs 须加 Ark image 字段"


def test_seedream_to_data_uri_helper_exists():
    """C: seedream 须有 _to_data_uri helper（URL/本地文件→data URI），沿用 ViMax 桥路径。"""
    mod_src = inspect.getsource(inspect.getmodule(SeedreamImageProvider))
    assert "def _to_data_uri" in mod_src
