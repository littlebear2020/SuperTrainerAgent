"""B2: CourseProject voice_id / subtitle_style / tts_speed 透传到任务。

修前两条 storyboard 路径都 `payload.get("voice_id") or config` /
`payload.get("subtitle_style", "default")`，项目级 voice_id/subtitle_style 静默失效。
修复后优先级：payload 显式 > 项目 > 默认。subtitle_style 的 schema 默认是 "default"，
故 payload="default" 视为未设置 → 回退项目。

T-E11（D6，2026-09-09 feedback-round1）：tts_speed 项目级同构三级链
（payload 显式 > 项目 tts_speed > None→config 缺省）。
"""

from app.gateway.video_gen.service import VideoGenService


class _FakeProj:
    def __init__(self, voice_id, subtitle_style, tts_speed=None):
        self.voice_id = voice_id
        self.subtitle_style = subtitle_style
        self.tts_speed = tts_speed


class _FakeConfig:
    tts_voice = "default-voice"
    tts_speed = 1.0


def _svc(monkeypatch, voice, sub, speed=None):
    svc = VideoGenService.__new__(VideoGenService)
    svc._config = _FakeConfig()
    monkeypatch.setattr(
        svc,
        "_read_course_project",
        lambda pid, *, user_id="default": _FakeProj(voice, sub, speed) if pid else None,
    )
    return svc


def test_voice_project_fallback(monkeypatch) -> None:
    assert _svc(monkeypatch, "cloned-1", "s1")._resolve_voice_id(None, "cp_dddddddddddd", user_id="u1") == "cloned-1"


def test_payload_voice_overrides_project(monkeypatch) -> None:
    assert _svc(monkeypatch, "cp_eeeeeeeeeeee", "s1")._resolve_voice_id("pay", "cp_dddddddddddd", user_id="u1") == "pay"


def test_voice_no_project_returns_none(monkeypatch) -> None:
    svc = _svc(monkeypatch, "ignored", "s1")
    # pid None → 无项目 → None（调用方再走 lang 默认/config，见 service.py 调用处）
    assert svc._resolve_voice_id(None, None, user_id="u1") is None


def test_subtitle_project_fallback_when_payload_default(monkeypatch) -> None:
    # payload "default"=未设置 → 用项目 "bold"
    assert _svc(monkeypatch, "v", "bold")._resolve_subtitle_style("default", "cp_dddddddddddd", user_id="u1") == "bold"


def test_payload_subtitle_overrides_project(monkeypatch) -> None:
    assert _svc(monkeypatch, "v", "minimal")._resolve_subtitle_style("bold", "cp_dddddddddddd", user_id="u1") == "bold"


def test_subtitle_no_project_default(monkeypatch) -> None:
    svc = _svc(monkeypatch, "v", "ignored")
    assert svc._resolve_subtitle_style("default", None, user_id="u1") == "default"


def test_voice_empty_string_falls_through(monkeypatch) -> None:
    """F9: CourseProject.voice_id 默认 ''；空串应视为未设置 → 回退（不返回 ''）。"""
    svc = _svc(monkeypatch, "", "s1")  # 项目 voice_id=""
    assert svc._resolve_voice_id(None, "cp_dddddddddddd", user_id="u1") is None


# ── T-E11（D6）：项目级 tts_speed 三级链 ────────────────────────────────────


def test_tts_speed_project_fallback(monkeypatch) -> None:
    """挂项目且任务未显式传语速 → 取项目值。"""
    assert _svc(monkeypatch, "v", "s1", speed=1.5)._resolve_tts_speed(None, "cp_dddddddddddd", user_id="u1") == 1.5


def test_tts_speed_payload_overrides_project(monkeypatch) -> None:
    """任务显式传值最高优先。"""
    assert _svc(monkeypatch, "v", "s1", speed=1.5)._resolve_tts_speed(1.8, "cp_dddddddddddd", user_id="u1") == 1.8


def test_tts_speed_no_project_returns_none(monkeypatch) -> None:
    """无项目 → None（调用方回落 config tts_speed，见 service.py 消费点）。"""
    svc = _svc(monkeypatch, "v", "s1", speed=1.5)
    assert svc._resolve_tts_speed(None, None, user_id="u1") is None


def test_tts_speed_project_none_returns_none(monkeypatch) -> None:
    """存量项目无 tts_speed 字段（None）→ 视为未设置，不遮蔽 config 缺省。"""
    svc = _svc(monkeypatch, "v", "s1", speed=None)
    assert svc._resolve_tts_speed(None, "cp_dddddddddddd", user_id="u1") is None
