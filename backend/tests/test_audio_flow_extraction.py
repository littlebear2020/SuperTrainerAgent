"""Task 15: 音频编排抽出后行为不变。"""

from app.gateway.video_gen.audio_flow import reconcile_durations_with_audio
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(duration: float, audio: str | None) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=[StoryboardFrame(index=0, narration="n", duration=duration, audio_path=audio)])


def test_no_audio_path_is_noop(tmp_path, monkeypatch) -> None:
    sb = _sb(5.0, None)
    assert reconcile_durations_with_audio(sb, tmp_path) is False
    assert sb.frames[0].duration == 5.0


def test_drift_beyond_tolerance_is_corrected(tmp_path, monkeypatch) -> None:
    """记录 5.0s 但音频实测 8.9s → 以实测为准（音频是唯一真相）。"""
    from app.gateway.video_gen import audio_flow

    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 8.9)
    sb = _sb(5.0, "a.mp3")
    assert reconcile_durations_with_audio(sb, tmp_path) is True
    assert sb.frames[0].duration == 8.9
    assert sb.frames[0].speech_duration == 8.9


def test_within_tolerance_untouched(tmp_path, monkeypatch) -> None:
    from app.gateway.video_gen import audio_flow

    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 5.1)
    sb = _sb(5.0, "a.mp3")
    assert reconcile_durations_with_audio(sb, tmp_path) is False
    assert sb.frames[0].duration == 5.0


def test_probe_failure_leaves_duration(tmp_path, monkeypatch) -> None:
    from app.gateway.video_gen import audio_flow

    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 0.0)
    sb = _sb(5.0, "a.mp3")
    assert reconcile_durations_with_audio(sb, tmp_path) is False
    assert sb.frames[0].duration == 5.0


def test_total_duration_recomputed_on_change(tmp_path, monkeypatch) -> None:
    """任一帧被修正 → recompute_duration（调用方据此重建 SRT）。"""
    from app.gateway.video_gen import audio_flow

    monkeypatch.setattr(audio_flow, "_probe_audio_seconds", lambda p: 8.9)
    sb = _sb(5.0, "a.mp3")
    reconcile_durations_with_audio(sb, tmp_path)
    assert sb.total_duration == 8.9


def test_service_keeps_old_private_names(tmp_path) -> None:
    """搬迁是纯位置变更：service.py 的既有私名必须仍指向同一实现。

    这些私名被大量测试 monkeypatch（音频是时间轴唯一真相的入口），
    别名断链会让那些测试静默失效（打的是 service 上的名字，跑的是 audio_flow 的原件）。
    """
    from app.gateway.video_gen import audio_flow, service

    assert service._reconcile_durations_with_audio is audio_flow.reconcile_durations_with_audio
    assert service._concat_audio is audio_flow._concat_audio
    assert service._pad_audio_to_duration is audio_flow._pad_audio_to_duration
    assert service._probe_audio_seconds is audio_flow._probe_audio_seconds
    assert service._RECONCILE_TOLERANCE_SEC == audio_flow._RECONCILE_TOLERANCE_SEC


def test_concat_audio_still_reencodes() -> None:
    """C7 实测结论必须随代码一起搬：-c copy 会累积 mp3 padding 漂移（50 帧 +2.200s）。"""
    import inspect

    from app.gateway.video_gen import audio_flow

    src = inspect.getsource(audio_flow._concat_audio)
    # 只看代码行（docstring 本身写着「不得用 -c copy」，会误命中）
    code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith("#"))
    code = code.split('"""')[-1]
    assert "libmp3lame" in code
    assert '"copy"' not in code, "不得回退 stream copy（会累积 mp3 padding 漂移）"
    assert "2.200s" in (audio_flow._concat_audio.__doc__ or ""), "实测数据不得在搬迁中丢失"
