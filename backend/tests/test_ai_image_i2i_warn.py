from app.gateway.video_gen.providers.registry import I2I_CAPABLE_IMAGE_PROVIDERS


def test_i2i_capable_set_includes_seedream():
    """seedream 实现 i2i 后须在白名单内；dashscope 不在。"""
    assert "seedream" in I2I_CAPABLE_IMAGE_PROVIDERS
    assert "gpt-image-2" in I2I_CAPABLE_IMAGE_PROVIDERS
    assert "dashscope" not in I2I_CAPABLE_IMAGE_PROVIDERS
