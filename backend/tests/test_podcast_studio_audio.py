import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "skills/public/podcast-studio/scripts/podcast_studio.py"
SPEC = importlib.util.spec_from_file_location("podcast_studio_audio", MODULE_PATH)
ps = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = ps
SPEC.loader.exec_module(ps)
HELPER_PATH = ROOT / "skills/public/podcast-studio/scripts/_doubao_tts.py"


def load_helper():
    spec = importlib.util.spec_from_file_location("podcast_doubao_tts", HELPER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def full_source():
    text = "来源正文。"
    return {
        "source_id": "s1",
        "kind": "markdown",
        "requested_url": None,
        "final_url": None,
        "mime": "text/markdown",
        "charset": "utf-8",
        "converter": "passthrough",
        "warnings": [],
        "text": text,
        "raw_sha256": sha(text),
        "extracted_text_sha256": sha(text),
    }


def write_script(tmp_path, profile="news_single", turns=None, confirm=True):
    source = full_source()
    sources_path = tmp_path / "sources.json"
    sources_path.write_text(json.dumps({"schema_version": "podcast-studio.v1", "sources": [source]}), encoding="utf-8")
    if turns is None:
        role = "host" if profile in {"read_original", "news_single"} else "male"
        turns = [{"turn_id": "t0001", "role": role, "text": "第一段口播。第二句。", "source_ids": ["s1"]}]
    voices = {"host": ps.MALE_STOCK} if profile in {"read_original", "news_single"} else {"male": ps.MALE_STOCK, "female": ps.FEMALE_STOCK}
    draft = {
        "schema_version": "podcast-studio.v1",
        "profile": profile,
        "title": "测试/播客",
        "sources": [{key: source[key] for key in ("source_id", "raw_sha256", "extracted_text_sha256")}],
        "voices": voices,
        "turns": turns,
    }
    script = ps.normalize_script(draft, sources_path)
    if confirm and profile != "read_original":
        ps.confirm_script(tmp_path / "script.json")
        script = json.loads((tmp_path / "script.json").read_text(encoding="utf-8"))
    return tmp_path / "script.json", sources_path, script


@pytest.fixture(scope="session")
def tone_bytes(tmp_path_factory):
    path = tmp_path_factory.mktemp("tone") / "tone.mp3"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=24000:duration=0.24", "-c:a", "libmp3lame", str(path)],
        check=True,
    )
    return path.read_bytes()


def test_split_turn_sentence_boundary_and_overlong_sentence():
    text = "第一句。" * 40 + "第二句。" * 40
    segments = ps.split_turn(text)
    assert len(segments) > 1
    assert all(len(segment) <= 300 for segment in segments)
    assert "".join(segments) == text
    with pytest.raises(ps.PodcastError, match="300"):
        ps.split_turn("没有句号" * 100)


def test_single_voice_render_probe_and_duration(tmp_path, tone_bytes):
    script_path, sources_path, script = write_script(tmp_path)
    calls = []

    def synth(text, speaker):
        calls.append((text, speaker))
        return tone_bytes

    manifest = ps.render_script(script_path, sources_path, tmp_path / "outputs", synth=synth, timestamp="20261112T000000")
    assert calls == [("第一段口播。第二句。", "zh_male_m191_uranus_bigtts")]
    assert manifest["sample_rate"] == 24000
    assert manifest["codec"] == "mp3"
    assert manifest["segments"][0]["turn_id"] == "t0001"
    assert manifest["segments"][0]["role"] == "host"
    assert manifest["segments"][0]["speaker"] == "zh_male_m191_uranus_bigtts"
    assert manifest["inter_turn_pauses"] == 0
    assert manifest["duration_delta_seconds"] < 0.1
    assert Path(manifest["output_path"]).is_file()
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "stream=codec_name,sample_rate", "-of", "json", manifest["output_path"]]))
    assert probe["streams"][0]["codec_name"] == "mp3"
    assert probe["streams"][0]["sample_rate"] == "24000"
    subprocess.run(["ffmpeg", "-v", "error", "-i", manifest["output_path"], "-f", "null", "-"], check=True)


def test_default_timestamp_generates_mp3_and_success_manifest(tmp_path, tone_bytes):
    script_path, sources_path, _ = write_script(tmp_path)
    manifest = ps.render_script(script_path, sources_path, tmp_path / "outputs", synth=lambda text, speaker: tone_bytes)
    assert re.fullmatch(r"测试_播客_\d{8}T\d{12}Z\.mp3", Path(manifest["output_path"]).name)
    assert manifest["status"] == "success"
    assert Path(manifest["output_path"]).is_file()
    assert (tmp_path / "outputs" / "render_manifest.json").is_file()


def test_two_voice_order_and_inter_turn_pause_only(tmp_path, tone_bytes):
    turns = [
        {"turn_id": "t0001", "role": "male", "text": "男一。" * 120, "source_ids": ["s1"]},
        {"turn_id": "t0002", "role": "female", "text": "女一。", "source_ids": ["s1"]},
        {"turn_id": "t0003", "role": "male", "text": "男二。", "source_ids": ["s1"]},
    ]
    script_path, sources_path, _ = write_script(tmp_path, "interview_two", turns)
    calls = []

    def synth(text, speaker):
        calls.append((text, speaker))
        return tone_bytes

    manifest = ps.render_script(script_path, sources_path, tmp_path / "outputs", synth=synth, timestamp="20261112T000001")
    assert [speaker for _, speaker in calls] == ["zh_male_m191_uranus_bigtts", "zh_male_m191_uranus_bigtts", "zh_female_vv_uranus_bigtts", "zh_male_m191_uranus_bigtts"]
    assert [entry["turn_id"] for entry in manifest["segments"]] == ["t0001", "t0001", "t0002", "t0003"]
    assert manifest["inter_turn_pauses"] == 2
    assert manifest["duration_delta_seconds"] < 0.1
    assert abs(manifest["final_duration_seconds"] - (manifest["segment_duration_sum_seconds"] + 0.6)) < 0.1


def test_three_inter_turn_pauses_stay_within_duration_tolerance(tmp_path, tone_bytes):
    turns = [
        {"turn_id": "t0001", "role": "male", "text": "甲一", "source_ids": ["s1"]},
        {"turn_id": "t0002", "role": "female", "text": "乙一", "source_ids": ["s1"]},
        {"turn_id": "t0003", "role": "male", "text": "甲二", "source_ids": ["s1"]},
        {"turn_id": "t0004", "role": "female", "text": "乙二", "source_ids": ["s1"]},
    ]
    script_path, sources_path, _ = write_script(tmp_path, "interview_two", turns)
    manifest = ps.render_script(
        script_path,
        sources_path,
        tmp_path / "outputs",
        synth=lambda text, speaker: tone_bytes,
        timestamp="20261112T000004",
    )
    assert manifest["inter_turn_pauses"] == 3
    assert manifest["duration_delta_seconds"] < 0.1


def test_pending_and_stale_approval_zero_transport(tmp_path):
    script_path, sources_path, script = write_script(tmp_path, confirm=False)

    def forbidden(text, speaker):
        raise AssertionError("transport must not run")

    with pytest.raises(ps.PodcastError, match="approval|确认"):
        ps.render_script(script_path, sources_path, tmp_path / "pending", synth=forbidden)
    ps.confirm_script(script_path)
    script["turns"][0]["text"] = "确认后改稿"
    script_path.write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="hash|哈希|integrity"):
        ps.render_script(script_path, sources_path, tmp_path / "stale", synth=forbidden)
    assert not (tmp_path / "pending" / "render_manifest.json").exists()
    assert not (tmp_path / "stale" / "render_manifest.json").exists()


@pytest.mark.parametrize("voice", ["preset:146", "edge:zh-CN-XiaoxiaoNeural", "clone:custom", "doubao:unknown"])
def test_unknown_clone_preset_edge_voices_rejected_before_transport(tmp_path, voice):
    script_path, sources_path, script = write_script(tmp_path)
    script["voices"]["host"] = voice
    script_path.write_text(json.dumps(script), encoding="utf-8")
    with pytest.raises(ps.PodcastError, match="voice|音色"):
        ps.render_script(script_path, sources_path, tmp_path / "outputs", synth=lambda *args: pytest.fail("transport must not run"))


def test_missing_key_fails_before_network(monkeypatch):
    helper = load_helper()
    monkeypatch.delenv("VOLCENGINE_VOICE_ACCESS_KEY", raising=False)
    monkeypatch.setattr("urllib.request.urlopen", lambda *args: pytest.fail("network must not run"))
    with pytest.raises(RuntimeError, match="VOLCENGINE_VOICE_ACCESS_KEY"):
        helper.synthesize("文本", "zh_male_m191_uranus_bigtts")


def sse_response(events):
    class Response:
        def __init__(self):
            self.lines = [f"event: {event}\ndata: {json.dumps(data)}\n\n".encode() for event, data in events]

        def readline(self):
            return self.lines.pop(0) if self.lines else b""

        def __iter__(self):
            return self

        def __next__(self):
            line = self.readline()
            if not line:
                raise StopIteration
            return line

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    return Response()


def test_retryable_http_errors_exactly_two_attempts(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setattr(helper.time, "sleep", lambda _: None)
    for status in [429, 500]:
        calls = []

        def transient(request, timeout=None):
            calls.append(request)
            raise urllib.error.HTTPError(request.full_url, status, "transient", {}, None)

        monkeypatch.setattr("urllib.request.urlopen", transient)
        monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: sse_response([(352, {"code": 0, "data": ""})]) if len(calls) == 2 else transient(request, timeout))
        with pytest.raises(RuntimeError):
            helper.synthesize("文本", "zh_male_m191_uranus_bigtts")
        assert len(calls) == 2


def test_normal_4xx_and_sse_failure_exactly_once(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    calls = []

    def bad_request(request, timeout=None):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", bad_request)
    with pytest.raises(RuntimeError):
        helper.synthesize("文本", "zh_male_m191_uranus_bigtts")
    assert len(calls) == 1
    calls.clear()
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: calls.append(request) or sse_response([(153, {"code": 400, "message": "bad session"})]))
    with pytest.raises(RuntimeError):
        helper.synthesize("文本", "zh_male_m191_uranus_bigtts")
    assert len(calls) == 1


def test_empty_audio_fails(monkeypatch):
    helper = load_helper()
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setattr("urllib.request.urlopen", lambda request, timeout=None: sse_response([(152, {"code": 20000000})]))
    with pytest.raises(RuntimeError, match="audio|音频|empty"):
        helper.synthesize("文本", "zh_male_m191_uranus_bigtts")


def test_cache_reuse_missing_segment_and_text_invalidation(tmp_path, tone_bytes):
    script_path, sources_path, script = write_script(tmp_path)
    calls = []

    def synth(text, speaker):
        calls.append((text, speaker))
        return tone_bytes

    output = tmp_path / "outputs"
    first = ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000002")
    count = len(calls)
    assert count > 0
    ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000002")
    assert len(calls) == count
    Path(first["segments"][0]["path"]).unlink()
    with pytest.raises(ps.PodcastError, match="missing|缺"):
        ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000002")
    script["turns"][0]["text"] = "改稿后的新内容"
    script["approval"]["status"] = "pending"
    script["approval"]["approved_sha256"] = None
    script["integrity_sha256"] = ps._integrity(script)
    script_path.write_text(json.dumps(script), encoding="utf-8")
    ps.confirm_script(script_path)
    ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000002")
    assert len(calls) > count


def test_role_and_turn_order_invalidation(tmp_path, tone_bytes):
    turns = [
        {"turn_id": "t0001", "role": "male", "text": "甲", "source_ids": ["s1"]},
        {"turn_id": "t0002", "role": "female", "text": "乙", "source_ids": ["s1"]},
    ]
    script_path, sources_path, script = write_script(tmp_path, "debate_two", turns)
    calls = []

    def synth(text, speaker):
        calls.append((text, speaker))
        return tone_bytes

    output = tmp_path / "outputs"
    ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000003")
    assert len(calls) == 2
    script["turns"][0]["role"] = "female"
    script["approval"]["status"] = "pending"
    script["approval"]["approved_sha256"] = None
    script["integrity_sha256"] = ps._integrity(script)
    script_path.write_text(json.dumps(script), encoding="utf-8")
    ps.confirm_script(script_path)
    ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000003")
    assert len(calls) == 4
    script["turns"] = [
        {"turn_id": "t0001", "role": "female", "text": "乙", "source_ids": ["s1"]},
        {"turn_id": "t0002", "role": "male", "text": "甲", "source_ids": ["s1"]},
    ]
    script["approval"]["status"] = "pending"
    script["approval"]["approved_sha256"] = None
    script["integrity_sha256"] = ps._integrity(script)
    script_path.write_text(json.dumps(script), encoding="utf-8")
    ps.confirm_script(script_path)
    ps.render_script(script_path, sources_path, output, synth=synth, timestamp="20261112T000003")
    assert len(calls) == 6


def test_failed_turn_leaves_no_final_or_success_manifest(tmp_path):
    turns = [
        {"turn_id": "t0001", "role": "male", "text": "甲", "source_ids": ["s1"]},
        {"turn_id": "t0002", "role": "female", "text": "乙", "source_ids": ["s1"]},
    ]
    script_path, sources_path, _ = write_script(tmp_path, "debate_two", turns)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        ps.render_script(script_path, sources_path, tmp_path / "outputs", synth=lambda text, speaker: (_ for _ in ()).throw(RuntimeError("synthetic failure")))
    assert not list((tmp_path / "outputs").glob("*.mp3")) or not list((tmp_path / "outputs").glob("测试*mp3"))
    manifest = tmp_path / "outputs" / "render_manifest.json"
    assert not manifest.exists() or json.loads(manifest.read_text(encoding="utf-8")).get("status") != "success"


def test_probe_skip_is_cli_guard_without_network():
    probe = ROOT / "backend/scripts/probe_podcast_studio_tts.py"
    result = subprocess.run([sys.executable, str(probe)], check=False, capture_output=True, text=True, env={key: value for key, value in os.environ.items() if key != "RUN_LIVE"})
    assert result.returncode == 0
    assert "SKIP" in result.stdout
