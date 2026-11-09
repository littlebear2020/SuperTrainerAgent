"""P1-8: 生图成本按 caller 上报 metrics。"""

from app.gateway._shared.task_framework.metrics import metrics
from app.gateway.video_gen.pricing import IMAGE_UNIT_COST_CNY, record_image_cost


def test_record_image_cost_writer():
    metrics._counters.clear()
    record_image_cost("writer")
    rendered = metrics.render()
    assert "image_generation_cost_cny_total" in rendered
    assert 'caller="writer"' in rendered


def test_record_image_cost_gallery():
    metrics._counters.clear()
    record_image_cost("gallery")
    rendered = metrics.render()
    assert "image_generation_cost_cny_total" in rendered
    assert 'caller="gallery"' in rendered


def test_image_unit_cost_is_03():
    assert IMAGE_UNIT_COST_CNY == 0.3
