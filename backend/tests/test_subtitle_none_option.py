"""字幕「无」选项：schema 接受 none + build_worker_payload 置空 subtitle_path。"""

import pytest
from pydantic import ValidationError

from app.gateway.video_gen.schemas import CreateVideoRequest
from app.gateway.video_gen.service import build_worker_payload
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(subtitle_style: str) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(
            video_type="ai_image",
            width=1080,
            height=1920,
            subtitle_style=subtitle_style,
        ),
        frames=[StoryboardFrame(index=0, narration="n0", duration=3.0)],
    )


def test_schema_accepts_subtitle_style_none():
    req = CreateVideoRequest(video_type="ai_image", mode="generate", topic="t", subtitle_style="none")
    assert req.subtitle_style == "none"


def test_schema_still_rejects_garbage_subtitle_style():
    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_image", mode="generate", topic="t", subtitle_style="rainbow")


def test_worker_payload_drops_subtitle_path_when_none():
    payload = build_worker_payload(_sb("none"), subtitle_path="/x/subtitle.srt", audio_concat_path="/x/a.mp3")
    assert payload["subtitle_path"] is None
    # config 里的样式原样透传（前端/详情页仍可见用户选择）
    assert payload["config"]["subtitle_style"] == "none"


@pytest.mark.parametrize("style", ["default", "bold", "minimal", "outline"])
def test_worker_payload_keeps_subtitle_path_for_other_styles(style):
    payload = build_worker_payload(_sb(style), subtitle_path="/x/subtitle.srt", audio_concat_path="/x/a.mp3")
    assert payload["subtitle_path"] == "/x/subtitle.srt"
