import inspect


def test_ai_image_render_has_no_negative_prompt_param():
    """E: AIImageVisualSource.render 须无 negative_prompt 死参数。"""
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    sig = inspect.signature(AIImageVisualSource.render)
    assert "negative_prompt" not in sig.parameters


def test_ai_video_render_has_no_negative_prompt_param():
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    sig = inspect.signature(AIVideoVisualSource.render)
    assert "negative_prompt" not in sig.parameters
