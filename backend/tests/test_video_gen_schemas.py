import pytest
from pydantic import ValidationError

from app.gateway.video_gen.schemas import CreateVideoRequest, ReorderRequest


def test_create_video_request_defaults():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="金钱的作用")
    assert req.aspect is None  # 有意变更：不写死 9:16 遮蔽 config；由 config.default_aspect 在 service 层解析
    assert req.n_scenes is None  # 一.4: 不再默认 5（避免漏进 payload 覆盖 target_duration）；由 _resolve_n_scenes 推导
    assert req.seconds_per_frame is None  # 新增字段，默认 None（前端单图时长不再被 schema 丢弃）
    assert req.lang == "zh"


def test_fixed_mode_requires_script():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="fixed")


def test_generate_mode_requires_topic():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate")


def test_remotion_rejected_from_schema():
    """Remotion removed (2026-07-15 split): video_type="remotion" is no longer a
    valid CreateVideoRequest — remotion renders via chat direct-write, /api/video/create
    returns 422 for it."""
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="remotion", mode="fixed", script="x")
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="remotion", mode="generate", topic="x")


def test_subtitle_style_defaults_to_default():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.subtitle_style == "default"


def test_subtitle_style_accepts_valid_values():
    for style in ("default", "bold", "minimal", "outline"):
        req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", subtitle_style=style)
        assert req.subtitle_style == style


def test_subtitle_style_rejects_invalid():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", subtitle_style="tiktok")


def test_transition_defaults():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.transition == "none"  # E（2026-08-31 转场清理）：默认硬切
    assert req.transition_duration == 0.5


def test_transition_accepts_valid_values():
    for t in ("none", "fade", "dissolve", "slide_left", "slide_right", "wipe_left", "wipe_right", "zoom_in"):
        req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", transition=t)
        assert req.transition == t


def test_transition_rejects_invalid():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", transition="spin")


def test_transition_duration_bounds():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", transition_duration=0.1)
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", transition_duration=3.0)


def test_bgm_defaults_to_none():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.bgm is None


def test_bgm_accepts_string():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", bgm="morning_piano")
    assert req.bgm == "morning_piano"


def test_template_id_defaults():
    req = CreateVideoRequest(video_type="ai_image", mode="fixed", script="hello")
    assert req.template_id is None
    assert req.template_params is None


def test_template_id_accepts_values():
    req = CreateVideoRequest(
        video_type="ai_image",
        mode="fixed",
        script="hello",
        template_id="remotion-news",
        template_params={"title": "Breaking News", "theme_color": "#FF0000"},
    )
    assert req.template_id == "remotion-news"
    assert req.template_params["title"] == "Breaking News"


# ---------------------------------------------------------------------------
# output_format + quality
# ---------------------------------------------------------------------------


def test_output_format_defaults_to_mp4():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.output_format == "mp4"


def test_output_format_webm():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", output_format="webm")
    assert req.output_format == "webm"


def test_output_format_gif():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", output_format="gif")
    assert req.output_format == "gif"


def test_output_format_invalid():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", output_format="avi")


def test_quality_defaults_to_standard():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.quality == "standard"


def test_quality_draft():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", quality="draft")
    assert req.quality == "draft"


def test_quality_high():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", quality="high")
    assert req.quality == "high"


def test_quality_preview():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", quality="preview")
    assert req.quality == "preview"


def test_quality_invalid():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", quality="ultra")


# ---------------------------------------------------------------------------
# video_count（已移除，决策 1 / 2026-07-31）
# ---------------------------------------------------------------------------
#
# 「生成数量」下拉允许一次提交 1-5 个任务，但这些任务除内部 version_index 外
# **完全相同**——同 topic 同 prompt 无 seed 差异，5 倍成本换来 5 条几乎一样的
# 成片。配合 n_scenes ≤ 200，一次点击最多 1000 次付费生成。
# 产品决策：**移除该功能**（实际无人使用）。想要第二版就再点一次「重新生成」。


def test_video_count_field_removed():
    """schema 不再有 video_count 字段。"""
    assert "video_count" not in CreateVideoRequest.model_fields


def test_legacy_video_count_is_ignored_not_error():
    """老客户端/存量 payload 带 video_count 时**忽略**而非 422。

    前端会随本次一起发版，但存量草稿的 payload 可能已持久化了这个字段；
    让它 422 等于把老草稿全变成打不开。
    """
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", video_count=5)
    assert not hasattr(req, "video_count")


# ---------------------------------------------------------------------------
# engine
# ---------------------------------------------------------------------------


def test_engine_defaults_to_auto():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="test")
    assert req.engine == "auto"


def test_engine_auto_valid_for_all_types():
    for vt in ("ai_image", "ai_video"):
        req = CreateVideoRequest(video_type=vt, mode="fixed", script="test.", engine="auto")
        assert req.engine == "auto"


def test_engine_kling_invalid_for_ai_image():
    with pytest.raises(ValidationError, match="not valid"):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", engine="kling")


def test_engine_unknown_rejected():
    with pytest.raises(ValidationError, match="not valid"):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="test", engine="nonexistent")


# ---------------------------------------------------------------------------
# n_scenes limit for long course videos
# ---------------------------------------------------------------------------


def test_n_scenes_allows_up_to_200():
    """n_scenes should allow up to 200 for long course videos."""
    req = CreateVideoRequest(
        video_type="ai_image",
        mode="generate",
        topic="test",
        n_scenes=200,
    )
    assert req.n_scenes == 200


def test_n_scenes_rejects_over_200():
    """n_scenes should reject values over 200."""
    with pytest.raises(ValidationError):
        CreateVideoRequest(
            video_type="ai_image",
            mode="generate",
            topic="test",
            n_scenes=201,
        )


# ---------------------------------------------------------------------------
# ReorderRequest
# ---------------------------------------------------------------------------


def test_reorder_request_valid():
    req = ReorderRequest(from_index=0, to_index=5)
    assert req.from_index == 0
    assert req.to_index == 5


def test_reorder_request_rejects_negative():
    with pytest.raises(ValidationError):
        ReorderRequest(from_index=-1, to_index=0)
    with pytest.raises(ValidationError):
        ReorderRequest(from_index=0, to_index=-1)
