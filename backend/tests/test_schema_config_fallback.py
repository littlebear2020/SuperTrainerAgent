"""P1-E10: schema 写死默认值（aspect="9:16"/tts_speed=1.0）+ model_dump 无
exclude_unset → payload 永远携带写死值，config.yaml 的 default_aspect=16:9 /
tts_speed=1.2 静默不可达。另 service 多处 ``payload.get(key, config)`` 在 key
存在但值为 None 时返回 None（schema 改 None 后必现），必须改 ``or config``。
"""

from app.gateway.video_gen.schemas import CreateVideoRequest


def test_aspect_defaults_to_none():
    """schema 不再写死 9:16——不传时为 None，交 config default_aspect 决定。"""
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x")
    assert req.model_dump()["aspect"] is None


def test_tts_speed_defaults_to_none():
    """schema 不再写死 1.0——不传时为 None，交 config tts_speed 决定。"""
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x")
    assert req.model_dump()["tts_speed"] is None


def test_explicit_values_preserved():
    """显式传值仍保留（不被 None 默认吞掉）。"""
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x", aspect="1:1", tts_speed=1.5)
    assert req.aspect == "1:1"
    assert req.tts_speed == 1.5


def test_none_aspect_validates():
    """None 是合法值（Literal 联合 None）——前端不传时不 422。"""
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="x", aspect=None, tts_speed=None)
    assert req.aspect is None
    assert req.tts_speed is None
