"""E11: 重复 narration 帧的音频映射——frame_audio_map 按 frame.index 键、按帧计数（保 TTS 去重）。"""

from app.gateway.video_gen.storyboard import StoryboardFrame


def test_build_frame_audio_map_keys_by_index_no_overwrite():
    """两帧相同 narration → map 有两个独立 key（按 frame.index），不互相覆盖。"""
    from app.gateway.video_gen.service import _build_frame_audio_map

    frames = [
        StoryboardFrame(index=0, narration="dup", duration=2.0, audio_path="/a0.mp3", srt="s0"),
        StoryboardFrame(index=1, narration="dup", duration=2.1, audio_path="/a1.mp3", srt="s1"),
        StoryboardFrame(index=2, narration="uniq", duration=3.0, audio_path="/a2.mp3", srt="s2"),
    ]
    m = _build_frame_audio_map(frames)
    # 按 frame.index 键 → 3 个条目（修前按 md5(narration) 仅 2 个，重复旁白互相覆盖）
    assert set(m.keys()) == {0, 1, 2}
    # 各自指向自己的音频（不互相覆盖）
    assert m[0]["audio_path"] == "/a0.mp3"
    assert m[1]["audio_path"] == "/a1.mp3"
    assert m[2]["audio_path"] == "/a2.mp3"


def test_build_frame_audio_map_total_counts_frames():
    """total 应按帧数计（map 条目数 == 帧数），非 unique narration 数。"""
    from app.gateway.video_gen.service import _build_frame_audio_map

    frames = [StoryboardFrame(index=i, narration="same", duration=2.0, audio_path=f"/a{i}.mp3") for i in range(5)]
    m = _build_frame_audio_map(frames)
    # 5 帧全相同 narration → 仍 5 个条目（保 TTS 文件级去重，但映射口径按帧）
    assert len(m) == 5
    assert len(m) == len(frames), "total 须按帧数计，非 unique narration 数"
