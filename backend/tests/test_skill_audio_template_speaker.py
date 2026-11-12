"""C1/C3（2026-10-12 配音音色加固批）：技能 TTS 模板必须显式携带 speaker 音色参数。

事故背景（2026-10-12 FDE 课程视频）：模板裸调 ``synthesize(text)`` → 音色静默落
``DOUBAO_TTS_DEFAULT_SPEAKER`` env（女声），agent 照模板写代码物理上不可能产出
男声 → 成片女声 + 假注释谎称男声。修复 = 模板恢复显式 ``SPEAKER`` 常量 +
``speaker`` 形参全链透传（不设 None 兜底——None = 静默落 env，正是事故形态）。

本测试 importlib 按路径加载两个模板模块，断言签名/常量及共享前置行为；
传输层使用假实现，**不发起任何真实 TTS 调用**（离线可跑，CI 假绿口径同
conftest hermetic_tts 约定）。
"""

import importlib.util
import inspect
import json
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
AVF_TEMPLATE = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "scripts" / "generate_audio_template.py"
CORP_TEMPLATE = REPO_ROOT / "skills" / "public" / "corporate-video" / "scripts" / "generate_tts.py"


def _load(path: Path, name: str):
    """按路径加载技能脚本模块（技能脚本不在 backend 包内，无真实调用副作用）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _assert_registered_male_doubao(speaker: str) -> None:
    """SPEAKER 常量必须在 voice_catalog 在册、provider=doubao、gender=male。

    防的是「凭记忆编造 speaker id」——2026-10-12 事故会话 1 的
    "resource ID is mismatched" 即 agent 测了未授权音色 id。
    """
    from app.gateway._shared.voice_catalog import CURATED_VOICES

    matches = [v for v in CURATED_VOICES if v.id == speaker or v.id.endswith(f":{speaker}")]
    assert matches, f"SPEAKER={speaker!r} 不在 voice_catalog.CURATED_VOICES 在册清单（勿凭记忆编造音色 id）"
    v = matches[0]
    assert str(v.provider) == "doubao", f"SPEAKER={speaker!r} 在册但非豆包音色（provider={v.provider}）"
    assert v.gender == "male", f"SPEAKER={speaker!r} 在册但非男声（gender={v.gender}）"


def _assert_speaker_param(mod, *fn_names: str) -> None:
    """每个命名函数都必须有 speaker 形参，且默认值 = SPEAKER 常量（非 None）。"""
    for fn_name in fn_names:
        fn = getattr(mod, fn_name, None)
        assert callable(fn), f"{mod.__name__}.{fn_name} 不存在（模板结构漂移？）"
        sig = inspect.signature(fn)
        assert "speaker" in sig.parameters, f"{fn_name} 缺 speaker 形参——模板裸调 = 音色静默落 env 默认（2026-10-12 事故根因）"
        default = sig.parameters["speaker"].default
        assert default is not None and default == mod.SPEAKER, f"{fn_name}.speaker 默认值必须是 SPEAKER 常量而非 None——None 兜底 = 静默回落 env 默认女声，正是事故形态"


# ── C1: auto-video-flow/generate_audio_template.py ─────────────────────────


def test_avf_speaker_constant_is_registered_male_doubao():
    mod = _load(AVF_TEMPLATE, "avf_audio_template_speaker_test")
    assert hasattr(mod, "SPEAKER"), "模板缺 SPEAKER 常量（CONFIG 区应有显式默认音色）"
    _assert_registered_male_doubao(mod.SPEAKER)


def test_avf_tts_functions_carry_speaker():
    """generate_tts / generate_tts_timestamped / 两个内部 helper 全链透传 speaker。"""
    mod = _load(AVF_TEMPLATE, "avf_audio_template_speaker_test2")
    _assert_speaker_param(
        mod,
        "generate_tts",
        "generate_tts_timestamped",
        "_doubao_tts_generate",
        "_doubao_tts_generate_timestamped",
    )


def _set_doubao_env(monkeypatch: pytest.MonkeyPatch, access_key="test-access-key", default_speaker=None) -> None:
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    if access_key is not None:
        monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", access_key)
    if default_speaker is not None:
        monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", default_speaker)


def _fake_transport(calls: list[tuple[str, str]], words=None):
    def synthesize(text, speaker):
        calls.append(("plain", speaker))
        return b"fake-mp3"

    def synthesize_with_timestamps(text, speaker):
        calls.append(("timestamped", speaker))
        return b"fake-mp3", list(words or [])

    return SimpleNamespace(synthesize=synthesize, synthesize_with_timestamps=synthesize_with_timestamps)


def test_avf_plain_explicit_speaker_only_needs_access_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, "avf_plain_explicit_preflight")
    _set_doubao_env(monkeypatch)
    calls = []
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls))
    output = tmp_path / "plain.mp3"

    mod.generate_tts("最短一句", str(output), speaker="explicit-speaker")

    assert calls == [("plain", "explicit-speaker")]
    assert output.read_bytes() == b"fake-mp3"


def test_avf_timestamped_explicit_speaker_only_needs_access_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, "avf_timestamped_explicit_preflight")
    _set_doubao_env(monkeypatch)
    calls = []
    words = [{"word": "好", "startTime": 0.0, "endTime": 0.2}]
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls, words))
    output = tmp_path / "timestamped.mp3"

    result = mod.generate_tts_timestamped("好", str(output), speaker="explicit-speaker")

    assert result == words
    assert calls == [("timestamped", "explicit-speaker")]
    assert output.read_bytes() == b"fake-mp3"


@pytest.mark.parametrize("entrypoint", ["generate_tts", "generate_tts_timestamped"])
def test_avf_missing_key_fails_before_transport_without_blaming_default_speaker(entrypoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, f"avf_missing_key_{entrypoint}")
    _set_doubao_env(monkeypatch, access_key=None, default_speaker="env-default")
    calls = []
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls))

    with pytest.raises(RuntimeError) as caught:
        getattr(mod, entrypoint)("不得合成", str(tmp_path / "missing.mp3"), speaker="explicit-speaker")

    assert "VOLCENGINE_VOICE_ACCESS_KEY" in str(caught.value)
    assert "DOUBAO_TTS_DEFAULT_SPEAKER" not in str(caught.value)
    assert calls == []


@pytest.mark.parametrize("entrypoint", ["generate_tts", "generate_tts_timestamped"])
def test_avf_none_speaker_requires_only_missing_default(entrypoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, f"avf_none_speaker_preflight_{entrypoint}")
    _set_doubao_env(monkeypatch)
    calls = []
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls))

    with pytest.raises(RuntimeError) as caught:
        getattr(mod, entrypoint)("不得合成", str(tmp_path / "missing.mp3"), speaker=None)

    assert "DOUBAO_TTS_DEFAULT_SPEAKER" in str(caught.value)
    assert "VOLCENGINE_VOICE_ACCESS_KEY" not in str(caught.value)
    assert calls == []


@pytest.mark.parametrize("entrypoint", ["generate_tts", "generate_tts_timestamped"])
def test_avf_none_speaker_and_missing_key_list_both_actual_missing_items(entrypoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, f"avf_both_missing_preflight_{entrypoint}")
    _set_doubao_env(monkeypatch, access_key=None)
    calls = []
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls))

    with pytest.raises(RuntimeError) as caught:
        getattr(mod, entrypoint)("不得合成", str(tmp_path / "missing.mp3"), speaker=None)

    assert "VOLCENGINE_VOICE_ACCESS_KEY" in str(caught.value)
    assert "DOUBAO_TTS_DEFAULT_SPEAKER" in str(caught.value)
    assert calls == []


@pytest.mark.parametrize("entrypoint", ["generate_tts", "generate_tts_timestamped"])
def test_avf_none_speaker_preserves_helper_env_fallback(entrypoint, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, f"avf_none_speaker_env_fallback_{entrypoint}")
    helper = _load(AVF_TEMPLATE.with_name("_doubao_tts.py"), f"avf_helper_env_fallback_{entrypoint}")
    _set_doubao_env(monkeypatch, default_speaker="env-default")
    calls = []

    def fake_request(request, words=None):
        payload = json.loads(request.data)
        calls.append((entrypoint, payload["req_params"]["speaker"]))
        return b"fake-mp3"

    monkeypatch.setattr(helper, "_request_with_retry", fake_request)
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: helper)

    getattr(mod, entrypoint)("保留默认音色", str(tmp_path / "fallback.mp3"), speaker=None)

    assert calls == [(entrypoint, "env-default")]


def test_avf_explicit_speaker_differs_from_env_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, "avf_explicit_overrides_default")
    _set_doubao_env(monkeypatch, default_speaker="env-default")
    calls = []
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: _fake_transport(calls))

    mod.generate_tts("显式音色", str(tmp_path / "explicit.mp3"), speaker="different-explicit-speaker")

    assert calls == [("plain", "different-explicit-speaker")]


def test_avf_word_level_degrade_reuses_explicit_speaker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    mod = _load(AVF_TEMPLATE, "avf_degrade_explicit_speaker")
    _set_doubao_env(monkeypatch)
    calls = []
    transport = _fake_transport(calls)

    def fail_timestamps(text, speaker):
        calls.append(("timestamped", speaker))
        raise RuntimeError("mock timestamp failure")

    transport.synthesize_with_timestamps = fail_timestamps
    monkeypatch.setattr(mod, "_load_doubao_module", lambda: transport)

    result = mod.generate_tts_timestamped("降级一句", str(tmp_path / "degrade.mp3"), speaker="explicit-speaker")

    assert result is None
    assert calls == [("timestamped", "explicit-speaker"), ("plain", "explicit-speaker")]


def _load_live_probe(name: str):
    return _load(REPO_ROOT / "backend" / "scripts" / "probe_auto_video_flow_explicit_speaker.py", name)


def test_explicit_speaker_probe_requires_live_opt_in(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture):
    probe = _load_live_probe("avf_probe_opt_in")
    monkeypatch.delenv("RUN_LIVE", raising=False)
    monkeypatch.setattr(probe, "generate_tts", lambda *args, **kwargs: pytest.fail("unexpected paid call"))

    assert probe.main() == 0
    assert "SKIP" in capsys.readouterr().out


def test_explicit_speaker_probe_uses_registered_speaker_with_default_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = _load_live_probe("avf_probe_explicit")
    monkeypatch.setenv("RUN_LIVE", "1")
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-only-key")
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.setenv("PROBE_OUTPUT_DIR", str(tmp_path))
    calls = []

    def fake_generate(text, output_path, speaker):
        calls.append((text, speaker))
        Path(output_path).write_bytes(b"fake-mp3")

    monkeypatch.setattr(probe, "generate_tts", fake_generate)
    monkeypatch.setattr(probe, "_audio_duration", lambda path: 1.25)

    assert probe.main() == 0
    assert calls == [("文字动效验收。", "zh_male_m191_uranus_bigtts")]
    assert (tmp_path / "explicit-speaker.mp3").read_bytes() == b"fake-mp3"


def test_explicit_speaker_probe_retryable_error_has_only_one_transport_attempt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = _load_live_probe("avf_probe_single_attempt")
    template = _load(AVF_TEMPLATE, "avf_probe_single_attempt_template")
    helper = template._load_doubao_module()
    monkeypatch.setattr(template, "_load_doubao_module", lambda: helper)
    monkeypatch.setattr(probe, "_load_doubao_module", lambda: helper, raising=False)
    _set_doubao_env(monkeypatch)
    assert helper._MAX_ATTEMPTS == 2
    calls = []

    def retryable_failure(request, timeout):
        calls.append((request.full_url, timeout))
        raise urllib.error.URLError("offline retryable failure")

    monkeypatch.setattr(urllib.request, "urlopen", retryable_failure)
    monkeypatch.setattr(helper.time, "sleep", lambda _delay: None)

    with pytest.raises(RuntimeError):
        probe.generate_tts("离线重试验证", str(tmp_path / "single.mp3"), speaker=probe.SPEAKER)

    assert len(calls) == 1
    assert helper._MAX_ATTEMPTS == 1


def test_explicit_speaker_probe_refuses_ambient_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    probe = _load_live_probe("avf_probe_refuses_default")
    monkeypatch.setenv("RUN_LIVE", "1")
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-only-key")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "ambient-default")
    monkeypatch.setattr(probe, "generate_tts", lambda *args, **kwargs: pytest.fail("unexpected paid call"))

    assert probe.main() == 2


# ── C3: corporate-video/generate_tts.py（同构缺陷，同法修复）────────────────


def test_corporate_speaker_constant_is_registered_male_doubao():
    mod = _load(CORP_TEMPLATE, "corp_tts_speaker_test")
    assert hasattr(mod, "SPEAKER"), "模板缺 SPEAKER 常量（CONFIG 区应有显式默认音色）"
    _assert_registered_male_doubao(mod.SPEAKER)


def test_corporate_tts_functions_carry_speaker():
    mod = _load(CORP_TEMPLATE, "corp_tts_speaker_test2")
    _assert_speaker_param(mod, "tts_one", "_doubao_tts_one")
