"""P1#1: ViMax narration 兜底不得返回不存在的 audio.mp3（防无声成片）。"""

from unittest.mock import patch

import pytest

from app.gateway.video_gen.engines.vimax import narration_generator as ng


@pytest.mark.asyncio
async def test_fallback_returns_none_when_no_storyboard_and_no_audio(tmp_path):
    working = tmp_path / "vimax"
    working.mkdir()
    task_dir = tmp_path / "task"
    task_dir.mkdir()  # 注意：无 audio.mp3（skip_tts=True）
    with patch.object(ng, "_find_storyboard_files", return_value=[]):
        result = await ng.generate_vimax_narration(
            working_dir=working,
            task_dir=task_dir,
            original_script="x",
            llm=None,
            voice_id="preset:146",
        )
    assert result is None, "无 storyboard 且无 audio.mp3 必须返回 None（走 engine loud-fail），而非不存在的路径"


@pytest.mark.asyncio
async def test_fallback_returns_path_when_audio_exists(tmp_path):
    working = tmp_path / "vimax"
    working.mkdir()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    (task_dir / "audio.mp3").write_bytes(b"fake")
    with patch.object(ng, "_find_storyboard_files", return_value=[]):
        result = await ng.generate_vimax_narration(
            working_dir=working,
            task_dir=task_dir,
            original_script="x",
            llm=None,
            voice_id="preset:146",
        )
    assert result == task_dir / "audio.mp3"
