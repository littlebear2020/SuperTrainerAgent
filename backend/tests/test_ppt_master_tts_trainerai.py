"""PPT Master TTS trainerai 后端测试（2026-11-16：换字节 seed-tts-2.0）。

覆盖：openspeech URL/请求头/请求体、自动语种、成功 152 fail-closed、长文分片
ffmpeg 合成与原子发布、默认与显式 speaker、MP3 产物、HTTP 错误、只读
VOLCENGINE_VOICE_ACCESS_KEY；notes_to_audio 的 trainerai 接线与 CLI 帮助。
"""

import base64
import binascii
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/tts_backends/backend_trainerai.py"
SCRIPTS_DIR = REPO_ROOT / "skills/public/ppt-master/scripts"
NOTES_MOD_PATH = SCRIPTS_DIR / "notes_to_audio.py"

ENDPOINT = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
AUDIO_BYTES = b"\xff\xfb\x90\x00fake-mp3-payload"


def _sse(events: list[tuple[str, dict]]) -> bytes:
    chunks = []
    for event, payload in events:
        chunks.append(f"event: {event}\n")
        chunks.append(f"data: {json.dumps(payload)}\n\n")
    return "".join(chunks).encode("utf-8")


def _audio_event() -> tuple[str, dict]:
    return "352", {"code": 0, "data": base64.b64encode(AUDIO_BYTES).decode("ascii")}


def _finish_event() -> tuple[str, dict]:
    return "152", {"code": 20000000, "message": "ok"}


class FakeStream:
    def __init__(self, raw: bytes):
        self._lines = raw.splitlines(keepends=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


@pytest.fixture()
def mod(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    for key in ("QIHU_API_KEY", "ZYUN_API_KEY", "VOLCENGINE_VOICE_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    # 清掉本机 .env 可能已加载的值，确保只用用例显式设置的环境。
    for key in ("QIHU_API_KEY", "ZYUN_API_KEY", "VOLCENGINE_VOICE_ACCESS_KEY"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("backend_trainerai_tts", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _capture_urlopen(mod, monkeypatch, events):
    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["headers"] = {key.lower(): value for key, value in req.headers.items()}
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeStream(_sse(events))

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    return captured


def test_default_speaker_constant(mod):
    assert mod.DEFAULT_SPEAKER == "zh_male_m191_uranus_bigtts"


def test_output_extension_is_mp3(mod):
    assert mod.output_extension() == ".mp3"


def test_read_api_key_only_reads_volcengine_variable(mod, monkeypatch):
    monkeypatch.setenv("ZYUN_API_KEY", "zyun-value")
    with pytest.raises(RuntimeError, match="VOLCENGINE_VOICE_ACCESS_KEY"):
        mod.read_trainerai_api_key()

    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "volc-value")
    assert mod.read_trainerai_api_key() == "volc-value"


def test_generate_posts_openspeech_sse_and_writes_mp3(mod, monkeypatch, tmp_path):
    captured = _capture_urlopen(mod, monkeypatch, [_audio_event(), _finish_event()])
    output = tmp_path / "narration.mp3"

    mod.generate("你好，这是旁白。", output, api_key="test-key", voice_id="")

    assert captured["url"] == ENDPOINT
    assert captured["headers"]["x-api-key"] == "test-key"
    assert captured["headers"]["x-api-resource-id"] == "seed-tts-2.0"
    req_params = captured["body"]["req_params"]
    assert req_params["text"] == "你好，这是旁白。"
    assert req_params["speaker"] == mod.DEFAULT_SPEAKER
    assert req_params["audio_params"]["format"] == "mp3"
    additions = json.loads(req_params["additions"])
    assert "explicit_language" not in additions
    assert additions["enable_language_detector"] is True
    assert output.read_bytes() == AUDIO_BYTES


def test_generate_uses_explicit_voice_id_as_speaker(mod, monkeypatch, tmp_path):
    captured = _capture_urlopen(mod, monkeypatch, [_audio_event(), _finish_event()])

    mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="zh_female_custom")

    assert captured["body"]["req_params"]["speaker"] == "zh_female_custom"


def test_generate_raises_on_sse_failure(mod, monkeypatch, tmp_path):
    events = [("153", {"code": 40000000, "message": "invalid speaker"})]
    _capture_urlopen(mod, monkeypatch, events)

    with pytest.raises(RuntimeError, match="40000000|invalid speaker"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


def test_generate_raises_when_no_audio_event(mod, monkeypatch, tmp_path):
    _capture_urlopen(mod, monkeypatch, [_finish_event()])

    with pytest.raises(RuntimeError, match="no audio"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


@pytest.mark.parametrize(
    "events",
    [
        [_audio_event(), ("151", {"code": 0, "message": "not finished"})],
        [_audio_event()],
    ],
    ids=["151-is-not-a-successful-finish", "eof-before-152"],
)
def test_generate_rejects_audio_without_successful_152(mod, monkeypatch, tmp_path, events):
    _capture_urlopen(mod, monkeypatch, events)

    with pytest.raises(RuntimeError, match="152|finish"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


def test_generate_rejects_malformed_sse_json_even_with_audio_and_152(mod, monkeypatch, tmp_path):
    raw = _sse([_audio_event()]) + b"event: 352\ndata: {not-json}\n\n" + _sse([_finish_event()])
    monkeypatch.setattr(mod, "urlopen", lambda req, timeout=0: FakeStream(raw))

    with pytest.raises(RuntimeError, match="JSON"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


@pytest.mark.parametrize("control_data", ["", "[DONE]"])
def test_generate_ignores_empty_and_done_sse_control_frames(mod, monkeypatch, tmp_path, control_data):
    raw = _sse([_audio_event()]) + f"event: 351\ndata: {control_data}\n\n".encode() + _sse([_finish_event()])
    monkeypatch.setattr(mod, "urlopen", lambda req, timeout=0: FakeStream(raw))
    output = tmp_path / "control.mp3"

    mod.generate("hi", output, api_key="test-key", voice_id="")

    assert output.read_bytes() == AUDIO_BYTES


def test_generate_rejects_truncated_sse_json_even_with_audio_and_152(mod, monkeypatch, tmp_path):
    raw = _sse([_audio_event()]) + b'event: 352\ndata: {"code": 0, "data": "truncated\n' + _sse([_finish_event()])
    monkeypatch.setattr(mod, "urlopen", lambda req, timeout=0: FakeStream(raw))

    with pytest.raises(RuntimeError, match="JSON"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


def test_generate_decodes_base64_with_embedded_whitespace(mod, monkeypatch, tmp_path):
    encoded = base64.b64encode(AUDIO_BYTES).decode("ascii")
    wrapped = f" \t{encoded[:6]}\n{encoded[6:12]}\r\n{encoded[12:]} \t"
    _capture_urlopen(
        mod,
        monkeypatch,
        [("352", {"code": 0, "data": wrapped}), _finish_event()],
    )
    output = tmp_path / "wrapped.mp3"

    mod.generate("hi", output, api_key="test-key", voice_id="")

    assert output.read_bytes() == AUDIO_BYTES


def test_generate_still_rejects_non_base64_characters(mod, monkeypatch, tmp_path):
    encoded = base64.b64encode(AUDIO_BYTES).decode("ascii") + "$"
    _capture_urlopen(
        mod,
        monkeypatch,
        [("352", {"code": 0, "data": encoded}), _finish_event()],
    )

    with pytest.raises(binascii.Error):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


def test_generate_wraps_http_error(mod, monkeypatch, tmp_path):
    def fake_urlopen(req, timeout=0):
        raise urllib.error.HTTPError(
            req.full_url,
            400,
            "Bad Request",
            {},
            io.BytesIO(b'{"error":"invalid api key"}'),
        )

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    with pytest.raises(RuntimeError, match=r"HTTP 400.*invalid api key"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="test-key", voice_id="")


def test_retry_rebuilds_request_id_without_changing_payload(mod, monkeypatch, tmp_path):
    attempts = []

    def fake_urlopen(req, timeout=0):
        attempts.append(
            {
                "request_id": req.headers["X-api-request-id"],
                "body": json.loads(req.data.decode("utf-8")),
                "api_key": req.headers["X-api-key"],
            }
        )
        if len(attempts) == 1:
            raise urllib.error.URLError("temporary network failure")
        return FakeStream(_sse([_audio_event(), _finish_event()]))

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    output = tmp_path / "retry.mp3"

    mod.generate(
        "retry this exact text",
        output,
        api_key="test-key",
        voice_id="zh_female_custom",
    )

    assert len(attempts) == 2
    assert attempts[0]["request_id"]
    assert attempts[1]["request_id"]
    assert attempts[0]["request_id"] != attempts[1]["request_id"]
    for attempt in attempts:
        assert attempt["body"]["req_params"]["text"] == "retry this exact text"
        assert attempt["body"]["req_params"]["speaker"] == "zh_female_custom"
        assert attempt["api_key"] == "test-key"


def test_generate_rejects_empty_api_key(mod, tmp_path):
    with pytest.raises(RuntimeError, match="API key|api_key"):
        mod.generate("hi", tmp_path / "a.mp3", api_key="", voice_id="")


def test_generate_chunks_long_notes_by_sentence_and_merges_with_ffmpeg(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "MAX_TEXT_CHARS", 20, raising=False)
    requested_texts = []
    ffmpeg_calls = []

    def fake_request(request_factory):
        request = request_factory()
        requested_texts.append(json.loads(request.data)["req_params"]["text"])
        return f"chunk-{len(requested_texts)}".encode()

    def fake_ffmpeg(command, **kwargs):
        ffmpeg_calls.append((command, kwargs))
        Path(command[-1]).write_bytes(b"merged-mp3")
        return subprocess.CompletedProcess(command, 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(mod, "_request_with_retry", fake_request)
    monkeypatch.setattr(mod, "subprocess", SimpleNamespace(run=fake_ffmpeg), raising=False)
    output = tmp_path / "long.mp3"

    mod.generate(
        "第一句。第二句。Third sentence.",
        output,
        api_key="test-key",
        voice_id="",
    )

    assert requested_texts == ["第一句。第二句。", "Third sentence."]
    assert output.read_bytes() == b"merged-mp3"
    assert len(ffmpeg_calls) == 1
    command = ffmpeg_calls[0][0]
    assert command[0] == "ffmpeg"
    assert command.count("-i") == 2
    concat_filter = command[command.index("-filter_complex") + 1]
    assert "concat=n=2:v=0:a=1" in concat_filter
    assert command[command.index("-c:a") + 1] == "libmp3lame"
    assert command[command.index("-f") + 1] == "mp3"


def test_generate_does_not_publish_when_any_chunk_fails(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "MAX_TEXT_CHARS", 20, raising=False)
    requested_texts = []

    def fake_request(request_factory):
        request = request_factory()
        requested_texts.append(json.loads(request.data)["req_params"]["text"])
        if len(requested_texts) == 2:
            raise RuntimeError("second chunk failed")
        return b"first-chunk"

    monkeypatch.setattr(mod, "_request_with_retry", fake_request)
    output = tmp_path / "long.mp3"
    output.write_bytes(b"previous-good-audio")

    with pytest.raises(RuntimeError, match="second chunk failed"):
        mod.generate(
            "第一句。第二句。Third sentence.",
            output,
            api_key="test-key",
            voice_id="",
        )

    assert len(requested_texts) == 2
    assert output.read_bytes() == b"previous-good-audio"


def test_generate_does_not_publish_when_ffmpeg_merge_fails(mod, monkeypatch, tmp_path):
    monkeypatch.setattr(mod, "MAX_TEXT_CHARS", 20, raising=False)
    monkeypatch.setattr(mod, "_request_with_retry", lambda request_factory: b"chunk")

    def failed_ffmpeg(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, stdout=b"", stderr=b"invalid audio")

    monkeypatch.setattr(mod, "subprocess", SimpleNamespace(run=failed_ffmpeg), raising=False)
    output = tmp_path / "long.mp3"
    output.write_bytes(b"previous-good-audio")

    with pytest.raises(RuntimeError, match="ffmpeg|merge"):
        mod.generate(
            "第一句。第二句。Third sentence.",
            output,
            api_key="test-key",
            voice_id="",
        )

    assert output.read_bytes() == b"previous-good-audio"


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="real MP3 concat verification requires ffmpeg and ffprobe",
)
def test_generate_long_notes_produces_real_concatenated_mp3(mod, monkeypatch, tmp_path):
    segment = tmp_path / "segment.mp3"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=0.2",
            "-ar",
            "24000",
            "-ac",
            "1",
            "-c:a",
            "libmp3lame",
            "-y",
            str(segment),
        ],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(mod, "MAX_TEXT_CHARS", 20, raising=False)
    requested_texts = []

    def fake_request(request_factory):
        request = request_factory()
        requested_texts.append(json.loads(request.data)["req_params"]["text"])
        return segment.read_bytes()

    monkeypatch.setattr(mod, "_request_with_retry", fake_request)
    output = tmp_path / "long.mp3"

    mod.generate(
        "第一句。第二句。Third sentence.",
        output,
        api_key="test-key",
        voice_id="",
    )

    assert requested_texts == ["第一句。第二句。", "Third sentence."]
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert float(probe.stdout.strip()) > 0.3


@pytest.fixture()
def notes_mod(monkeypatch):
    """importlib 加载 notes_to_audio（scripts/ 需上 sys.path 供 config/tts_backends 导入）。"""
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("notes_to_audio", NOTES_MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "notes_to_audio", m)
    spec.loader.exec_module(m)
    for name in [n for n in sys.modules if n == "config" or n.startswith("tts_backends")]:
        sys.modules.pop(name)
    return m


def test_notes_help_describes_bytedance_cli(monkeypatch, notes_mod, capsys):
    monkeypatch.setattr(notes_mod, "_load_tts_env_file", lambda: None)
    monkeypatch.setattr(sys, "argv", ["notes_to_audio.py", "--help"])

    with pytest.raises(SystemExit) as exc_info:
        notes_mod.main()

    assert exc_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "ByteDance seed-tts-2.0" in help_text
    assert "<project_path> --provider trainerai" in help_text
    assert "Voice Twin 360 preset voices" not in help_text


def test_env_loader_only_includes_volcengine_prefix(tmp_path, monkeypatch, notes_mod):
    """.env 兜底加载只接受 VOLCENGINE_；QIHU_/ZYUN_ 已从 trainerai 通道移除。"""
    env_file = tmp_path / ".env"
    env_file.write_text(
        "VOLCENGINE_VOICE_ACCESS_KEY=test-volc\nQIHU_API_KEY=test-qihu\nZYUN_API_KEY=test-zyun\n",
        encoding="utf-8",
    )
    for key in ("VOLCENGINE_VOICE_ACCESS_KEY", "QIHU_API_KEY", "ZYUN_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        notes_mod._load_tts_env_file()
        assert os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY") == "test-volc"
        assert "QIHU_API_KEY" not in os.environ
        assert "ZYUN_API_KEY" not in os.environ
    finally:
        for key in ("VOLCENGINE_VOICE_ACCESS_KEY", "QIHU_API_KEY", "ZYUN_API_KEY"):
            os.environ.pop(key, None)


def test_default_command_uses_bytedance_speaker_and_writes_mp3(tmp_path, monkeypatch, notes_mod):
    """脚本级：默认 `--provider trainerai`（不传 voice）→ 字节默认 speaker + MP3。"""
    project = tmp_path / "deck"
    (project / "notes").mkdir(parents=True)
    (project / "notes" / "slide_01.md").write_text("大家好，欢迎来到第一页。", encoding="utf-8")
    monkeypatch.setattr(notes_mod, "_load_tts_env_file", lambda: None)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.delenv("QIHU_API_KEY", raising=False)
    monkeypatch.delenv("ZYUN_API_KEY", raising=False)

    captured = {}

    def fake_urlopen(req, timeout=0):
        captured["url"] = req.full_url
        captured["body"] = json.loads(req.data.decode("utf-8"))
        return FakeStream(_sse([_audio_event(), _finish_event()]))

    monkeypatch.setattr(notes_mod.backend_trainerai, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        sys,
        "argv",
        ["notes_to_audio.py", str(project), "--provider", "trainerai"],
    )

    assert notes_mod.main() == 0

    output = project / "audio" / "slide_01.mp3"
    assert output.is_file()
    assert output.read_bytes() == AUDIO_BYTES
    assert not (project / "audio" / "slide_01.srt").exists()
    assert captured["url"] == ENDPOINT
    assert captured["body"]["req_params"]["speaker"] == "zh_male_m191_uranus_bigtts"

    manifest = json.loads((project / "audio" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "trainerai"
    assert manifest["model"] == "seed-tts-2.0"
    assert manifest["audio_format"] == "mp3"
    assert "subtitles" not in manifest


# ---------------------------------------------------------------------------
# P8 provider 注册链断言：trainerai 的接线点必须全部在（2026-11-16 更新为
# 字节 seed-tts-2.0 / VOLCENGINE_ 前缀）。源码级断言（不 import：notes_to_audio
# 依赖 pptx_workspace 等 vendored 模块）。
# ---------------------------------------------------------------------------


def test_notes_to_audio_registers_trainerai_provider():
    source = NOTES_MOD_PATH.read_text(encoding="utf-8")
    assert "backend_trainerai" in source, "notes_to_audio.py 未 import backend_trainerai"
    assert 'choices=["elevenlabs", "minimax", "qwen", "cosyvoice", "trainerai"]' in source, "--provider choices 缺 trainerai"
    assert 'default="trainerai"' in source, "--provider 默认值不是 trainerai"
    assert 'args.provider == "trainerai"' in source, "构造分支缺 trainerai"
    assert 'backend.provider == "trainerai"' in source, "生成分发分支缺 trainerai"
    assert 'backend.provider == "trainerai":' in source, "manifest 分支缺 trainerai"
    assert "backend_trainerai.DEFAULT_SPEAKER" in source, "默认 speaker 未接线"
    assert "backend_trainerai.PRESET_VOICES" not in source, "旧 360 预置音色表未移除"
    assert '"VOLCENGINE_"' in source, "env 白名单缺 VOLCENGINE_ 前缀"
    assert '"QIHU_"' not in source and '"ZYUN_"' not in source, "旧 QIHU_/ZYUN_ env 前缀未移除"
    assert '"seed-tts-2.0"' in source, "manifest/model 未记录 seed-tts-2.0"
