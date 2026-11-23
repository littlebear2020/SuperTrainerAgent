"""五份 ``_doubao_tts.py`` 镜像与 PPT 两个 backend 的账本发射（媒体计量计划 PR 3，Task 3 / Task 4）。

发射规则（计划 Task 3）：仅在供应商明确成功后发射；整个发射包在 try/except 内，永不抛错、
不写 stdout/stderr；目录取 ``DEER_FLOW_MEDIA_LEDGER_DIR``，缺省 ``/mnt/user-data/workspace/.usage``；
``event_id`` 为 32 位随机 hex；每次写入显式设权限（umask 不影响），保证跨 uid 的 gateway 可读。
"""

from __future__ import annotations

import base64
import http.client
import importlib.util
import io
import json
import os
import stat
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PPT_SCRIPTS = REPO_ROOT / "skills" / "public" / "ppt-master" / "scripts"
AUDIO = b"\xff\xfb\x90\x00fake-mp3-payload"
CONTRACT_KEYS = {"event_id", "kind", "with_timestamps", "char_count", "model", "width", "height", "elapsed_ms"}


def _doubao_mirrors() -> list[Path]:
    copies: list[Path] = []
    for root in (REPO_ROOT / "skills" / "public", REPO_ROOT / "skills" / "custom"):
        for path in root.glob("**/scripts/_doubao_tts.py"):
            parts = path.parts
            if ".deer-flow" in parts or "node_modules" in parts:
                continue
            copies.append(path)
    return sorted(copies)


MIRRORS = _doubao_mirrors()
MIRROR_IDS = [str(p.relative_to(REPO_ROOT / "skills")) for p in MIRRORS]


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sse(events: list[tuple[str, dict]]) -> bytes:
    out = []
    for event, payload in events:
        out.append(f"event: {event}\n")
        out.append(f"data: {json.dumps(payload)}\n\n")
    return "".join(out).encode("utf-8")


def _audio_event() -> tuple[str, dict]:
    return "352", {"code": 0, "data": base64.b64encode(AUDIO).decode("ascii")}


def _subtitle_event() -> tuple[str, dict]:
    return "364", {"sentence": {"words": [{"word": "你", "startTime": 0.0, "endTime": 0.2, "confidence": 1.0}]}}


def _finish_event() -> tuple[str, dict]:
    return "152", {"code": 20000000}


class FakeStream:
    def __init__(self, raw: bytes) -> None:
        self._lines = raw.splitlines(keepends=True)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter(self._lines)


def _install_fake_urlopen(mod, monkeypatch, attempts: list):
    """Each urlopen call consumes the next entry: an events list (success stream) or an Exception to raise."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        index = min(calls["n"], len(attempts) - 1)
        calls["n"] += 1
        entry = attempts[index]
        if isinstance(entry, Exception):
            raise entry
        return FakeStream(_sse(entry))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)  # mirror calls urllib.request.urlopen
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)  # retry backoff is real otherwise
    return calls


def _events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture
def ledger(tmp_path, monkeypatch) -> Path:
    ledger_dir = tmp_path / "workspace" / ".usage"
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(ledger_dir))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_test_bigtts")
    return ledger_dir / "media-usage.jsonl"


def test_discovery_finds_the_five_known_mirrors():
    assert len(MIRRORS) == 5, MIRROR_IDS
    assert "custom/course-video/scripts/_doubao_tts.py" in MIRROR_IDS
    assert "public/podcast-studio/scripts/_doubao_tts.py" in MIRROR_IDS


# ---------------------------------------------------------------------------
# Task 3 Step 1：五镜像发射
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mirror", MIRRORS, ids=MIRROR_IDS)
def test_synthesize_success_writes_one_tts_event(mirror, ledger, monkeypatch):
    mod = _load(mirror, f"doubao_{MIRRORS.index(mirror)}")
    _install_fake_urlopen(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    audio = mod.synthesize("你好世界")
    assert audio == AUDIO
    events = _events(ledger)
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "tts"
    assert event["with_timestamps"] is False
    assert event["char_count"] == len("你好世界") == 4
    assert len(event["event_id"]) == 32 and int(event["event_id"], 16) >= 0
    assert set(event) <= CONTRACT_KEYS, "只发契约字段"
    assert "model" not in event, "tts 事件的 model 由服务端固定，发射端不发"


@pytest.mark.parametrize("mirror", MIRRORS, ids=MIRROR_IDS)
def test_synthesize_with_timestamps_writes_timestamped_event(mirror, ledger, monkeypatch):
    mod = _load(mirror, f"doubao_ts_{MIRRORS.index(mirror)}")
    _install_fake_urlopen(mod, monkeypatch, [[_audio_event(), _subtitle_event(), _finish_event()]])
    audio, words = mod.synthesize_with_timestamps("带字幕")
    assert audio == AUDIO and words[0]["word"] == "你"
    events = _events(ledger)
    assert len(events) == 1
    assert events[0]["kind"] == "tts"
    assert events[0]["with_timestamps"] is True
    assert events[0]["char_count"] == 3


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_retry_writes_only_the_final_success(mirror, ledger, monkeypatch):
    mod = _load(mirror, "doubao_retry")
    http_503 = urllib.error.HTTPError("https://openspeech.bytedance.com/x", 503, "unavailable", None, io.BytesIO(b"unavailable"))
    calls = _install_fake_urlopen(mod, monkeypatch, [http_503, [_audio_event(), _finish_event()]])
    assert mod.synthesize("重试一次") == AUDIO
    assert calls["n"] == 2, "第一次 503 触发一次退避重试"
    events = _events(ledger)
    assert len(events) == 1 and events[0]["char_count"] == len("重试一次"), "重试只记最终成功那一次"


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
@pytest.mark.parametrize(
    ("attempts", "match"),
    [
        ([urllib.error.HTTPError("https://openspeech.bytedance.com/x", 400, "bad request", None, io.BytesIO(b"bad request"))], "HTTP 400"),
        ([[_audio_event(), ("153", {"code": 1, "message": "boom"})]], "session failed"),
        ([[_finish_event()]], "no audio"),
        ([http.client.RemoteDisconnected()], "connection error"),
    ],
    ids=["http-400", "sse-153", "no-audio", "network"],
)
def test_failure_writes_nothing(mirror, ledger, monkeypatch, attempts, match):
    mod = _load(mirror, "doubao_fail")
    _install_fake_urlopen(mod, monkeypatch, attempts)
    with pytest.raises(RuntimeError, match=match):
        mod.synthesize("不会记账")
    assert _events(ledger) == [], "HTTP/业务失败不估算、不发射"
    assert not ledger.exists()


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_ledger_line_survives_caller_disk_write_failure(mirror, ledger, monkeypatch, tmp_path):
    """供应商成功但调用方写盘失败：消耗已经发生，账本行必须已在。"""
    mod = _load(mirror, "doubao_caller")
    _install_fake_urlopen(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    audio = mod.synthesize("先消费再落盘")
    with pytest.raises(OSError):
        (tmp_path / "not-a-dir.txt" / "out.mp3").write_bytes(audio)  # caller's own write blows up
    assert [e["char_count"] for e in _events(ledger)] == [6]


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_unwritable_ledger_dir_still_returns_audio_quietly(mirror, tmp_path, monkeypatch, capsys):
    blocker = tmp_path / "blocker.txt"
    blocker.write_text("regular file, not a dir", encoding="utf-8")
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(blocker / ".usage"))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_test_bigtts")
    mod = _load(mirror, "doubao_unwritable")
    _install_fake_urlopen(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    assert mod.synthesize("目录不可写") == AUDIO
    captured = capsys.readouterr()
    assert "media-usage" not in captured.out + captured.err
    assert "event_id" not in captured.out + captured.err


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_ledger_is_cross_uid_readable_under_umask_077(mirror, ledger, monkeypatch):
    mod = _load(mirror, "doubao_umask")
    _install_fake_urlopen(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    old_umask = os.umask(0o077)
    try:
        mod.synthesize("umask 不影响权限")
    finally:
        os.umask(old_umask)
    assert ledger.exists()
    assert stat.S_IMODE(ledger.parent.stat().st_mode) == 0o755, ".usage 目录显式 0755"
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o644, "账本文件显式 0644"


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_default_ledger_dir_is_the_sandbox_workspace(mirror, monkeypatch):
    mod = _load(mirror, "doubao_default_dir")
    monkeypatch.delenv("DEER_FLOW_MEDIA_LEDGER_DIR", raising=False)
    assert mod._media_ledger_dir() == "/mnt/user-data/workspace/.usage"


@pytest.mark.parametrize("mirror", MIRRORS, ids=MIRROR_IDS)
def test_emitter_never_raises_on_any_internal_error(mirror, monkeypatch, capsys):
    mod = _load(mirror, f"doubao_never_{MIRRORS.index(mirror)}")
    monkeypatch.setattr(mod.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("sealed")))
    mod._emit_media_usage({"event_id": "0" * 32, "kind": "tts", "with_timestamps": False, "char_count": 1})  # must not raise
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""


@pytest.mark.parametrize("mirror", MIRRORS, ids=MIRROR_IDS)
def test_python_310_compatibility_no_datetime_utc(mirror):
    """只用 Python 3.10 可用的 stdlib API（沙箱解释器上限 3.10）。"""
    source = mirror.read_text(encoding="utf-8")
    assert "datetime.UTC" not in source
    assert "ExceptionGroup" not in source
    assert "tomllib" not in source


# ---------------------------------------------------------------------------
# Task 3 Step 4：后台（nohup 形态）与并发
# ---------------------------------------------------------------------------

SITECUSTOMIZE = """
import base64
import urllib.request

AUDIO = base64.b64encode(b"\\xff\\xfb\\x90\\x00fake-mp3-payload").decode("ascii")


class _Stream:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def __iter__(self):
        return iter([
            b"event: 352\\n",
            b'data: {"code": 0, "data": "' + AUDIO.encode("ascii") + b'"}\\n',
            b"\\n",
            b"event: 152\\n",
            b'data: {"code": 20000000}\\n',
            b"\\n",
        ])


urllib.request.urlopen = lambda req, timeout=0: _Stream()
"""

CHILD = """
import importlib.util, sys
spec = importlib.util.spec_from_file_location("doubao_child", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
for i in range(int(sys.argv[2])):
    mod.synthesize("句子" + str(i))
"""


def _write_sitecustomize(site_dir: Path) -> Path:
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    return site_dir


def _child_env(site_dir: Path, ledger_dir: Path) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONPATH": str(site_dir),
        "DEER_FLOW_MEDIA_LEDGER_DIR": str(ledger_dir),
        "VOLCENGINE_VOICE_ACCESS_KEY": "test-key",
        "DOUBAO_TTS_DEFAULT_SPEAKER": "zh_male_test_bigtts",
    }


def _run_child(mirror: Path, ledger_dir: Path, work: Path, n: int) -> subprocess.CompletedProcess:
    site_dir = _write_sitecustomize(work / "site")
    log = work / f"child-{n}-{os.getpid()}.log"
    env = _child_env(site_dir, ledger_dir)
    with log.open("wb") as stdout:
        return subprocess.run([sys.executable, "-c", CHILD, str(mirror), str(n)], cwd=str(work), env=env, stdout=stdout, stderr=subprocess.STDOUT, timeout=120, check=False)


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_subprocess_with_redirected_stdio_still_writes_ledger(mirror, tmp_path, capfd):
    """模拟 nohup：stdout/stderr 重定向到日志文件，采集不依赖回显。"""
    ledger_dir = tmp_path / "workspace" / ".usage"
    result = _run_child(mirror, ledger_dir, tmp_path / "work-a", 1)
    assert result.returncode == 0, result.stderr
    events = _events(ledger_dir / "media-usage.jsonl")
    assert [e["kind"] for e in events] == ["tts"]
    logs = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "work-a").glob("*.log"))
    assert "media-usage" not in logs and "event_id" not in logs and '"kind"' not in logs, "日志里不得出现任何计量标记"


@pytest.mark.parametrize("mirror", MIRRORS[:1], ids=MIRROR_IDS[:1])
def test_two_processes_append_concurrently_without_torn_lines(mirror, tmp_path):
    ledger_dir = tmp_path / "workspace" / ".usage"
    env = _child_env(_write_sitecustomize(tmp_path / "site"), ledger_dir)
    procs = [subprocess.Popen([sys.executable, "-c", CHILD, str(mirror), "25"], cwd=str(tmp_path), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) for _ in range(2)]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    lines = [line for line in (ledger_dir / "media-usage.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 50, "两个进程各 25 行，行数齐全"
    events = [json.loads(line) for line in lines]  # 逐行可解析 = 无撕裂
    assert len({e["event_id"] for e in events}) == 50
    assert all(e["kind"] == "tts" for e in events)


# ---------------------------------------------------------------------------
# Task 3 Step 3：PPT TTS 分片经共用 helper 发射 + P35 登记
# ---------------------------------------------------------------------------


@pytest.fixture
def ppt_tts_mod(monkeypatch):
    monkeypatch.syspath_prepend(str(PPT_SCRIPTS))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    for key in ("QIHU_API_KEY", "ZYUN_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    return _load(PPT_SCRIPTS / "tts_backends" / "backend_trainerai.py", "backend_trainerai_tts")


def _install_ppt_fake(mod, monkeypatch, attempts: list):
    """backend_trainerai 用 ``from urllib.request import urlopen`` → 模块属性可直接替换。"""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=0):
        index = min(calls["n"], len(attempts) - 1)
        calls["n"] += 1
        entry = attempts[index]
        if isinstance(entry, Exception):
            raise entry
        return FakeStream(_sse(entry))

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)
    return calls


def _skip_ffmpeg(mod, monkeypatch):
    """多分片路径用 ffmpeg concat；本文件只验计量，用文件复制代替合并。"""

    def fake_merge(chunk_paths, output_path):
        output_path.write_bytes(chunk_paths[0].read_bytes())

    monkeypatch.setattr(mod, "_merge_mp3_chunks", fake_merge)


def test_ppt_single_chunk_writes_one_tts_event(ppt_tts_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_tts_mod
    _install_ppt_fake(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    out = tmp_path / "narration.mp3"
    mod.generate("旁白短句", out, api_key="k", voice_id=None)
    assert out.read_bytes() == AUDIO
    events = _events(ledger)
    assert [(e["kind"], e["with_timestamps"], e["char_count"]) for e in events] == [("tts", False, 4)]


def test_ppt_multi_chunk_writes_one_event_per_chunk_not_merged(ppt_tts_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_tts_mod
    _skip_ffmpeg(mod, monkeypatch)
    text = ("测" * 77 + "。") * 26  # 13 句超 1000 字 → 多分片
    chunks = mod._split_text(text, mod.MAX_TEXT_CHARS)
    assert len(chunks) >= 2
    _install_ppt_fake(mod, monkeypatch, [[_audio_event(), _finish_event()]])
    mod.generate(text, tmp_path / "long.mp3", api_key="k", voice_id=None)
    events = _events(ledger)
    assert len(events) == len(chunks), "每个真实成功分片各一条，不合并"
    assert [e["char_count"] for e in events] == [len(c) for c in chunks]
    assert sum(e["char_count"] for e in events) == len(text)
    assert len({e["event_id"] for e in events}) == len(chunks)


def test_ppt_retry_writes_only_the_final_success(ppt_tts_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_tts_mod
    http_503 = urllib.error.HTTPError("https://openspeech.bytedance.com/x", 503, "unavailable", None, io.BytesIO(b"unavailable"))
    calls = _install_ppt_fake(mod, monkeypatch, [http_503, [_audio_event(), _finish_event()]])
    mod.generate("重试不重复", tmp_path / "r.mp3", api_key="k", voice_id=None)
    assert calls["n"] == 2
    assert [e["char_count"] for e in _events(ledger)] == [5]


@pytest.mark.parametrize(
    ("attempts", "match"),
    [
        ([urllib.error.HTTPError("https://openspeech.bytedance.com/x", 400, "bad request", None, io.BytesIO(b"bad"))], "HTTP 400"),
        ([[_audio_event(), ("153", {"code": 1, "message": "boom"})]], "session failed"),
    ],
    ids=["http-400", "sse-153"],
)
def test_ppt_failure_writes_nothing(ppt_tts_mod, ledger, monkeypatch, tmp_path, attempts, match):
    mod = ppt_tts_mod
    _install_ppt_fake(mod, monkeypatch, attempts)
    with pytest.raises(RuntimeError, match=match):
        mod.generate("不计费", tmp_path / "f.mp3", api_key="k", voice_id=None)
    assert _events(ledger) == []


def test_ppt_shared_helper_exists_and_never_raises(monkeypatch, capsys):
    helper_path = PPT_SCRIPTS / "media_usage_ledger.py"
    assert helper_path.exists(), "两个 backend 共用的发射 helper 必须存在"
    monkeypatch.syspath_prepend(str(PPT_SCRIPTS))
    helper = _load(helper_path, "media_usage_ledger")
    monkeypatch.setattr(helper.os, "makedirs", lambda *a, **k: (_ for _ in ()).throw(PermissionError("sealed")))
    helper.emit_media_usage({"event_id": "0" * 32, "kind": "tts", "with_timestamps": False, "char_count": 1})
    captured = capsys.readouterr()
    assert captured.out == "" and captured.err == ""
    source = helper_path.read_text(encoding="utf-8")
    assert "datetime.UTC" not in source and "tomllib" not in source, "只用 Python 3.10 可用的 stdlib API"


def test_ppt_helper_and_doubao_mirror_share_the_same_ledger_location(ppt_tts_mod, monkeypatch):
    monkeypatch.delenv("DEER_FLOW_MEDIA_LEDGER_DIR", raising=False)
    monkeypatch.syspath_prepend(str(PPT_SCRIPTS))
    helper = _load(PPT_SCRIPTS / "media_usage_ledger.py", "media_usage_ledger_dir")
    mirror = _load(MIRRORS[0], "doubao_dir_compare")
    assert helper.media_ledger_dir() == mirror._media_ledger_dir() == "/mnt/user-data/workspace/.usage"


def test_trainerai_patches_registers_p35():
    registry = (REPO_ROOT / "skills" / "public" / "ppt-master" / "TRAINERAI_PATCHES.md").read_text(encoding="utf-8")
    rows = [line for line in registry.splitlines() if line.startswith("| P35 |")]
    assert len(rows) == 1, "P35 必须且只登记一行"
    row = rows[0]
    assert row.count("|") >= 5, "沿用四列表格形态"
    for needle in ("media_usage_ledger.py", "backend_trainerai.py", "backend_qihu360.py"):
        assert needle in row, needle


# ---------------------------------------------------------------------------
# Task 4 Step 1：PPT 生图 backend_qihu360 经共用 helper 发射
# ---------------------------------------------------------------------------

PPT_DEFAULT_MODEL = "bytedance/doubao-seedream-5.0-pro"


@pytest.fixture
def ppt_image_mod(monkeypatch):
    monkeypatch.syspath_prepend(str(PPT_SCRIPTS))
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.delenv("QIHU_MODEL", raising=False)
    monkeypatch.delenv("QIHU_BASE_URL", raising=False)
    return _load(PPT_SCRIPTS / "image_backends" / "backend_qihu360.py", "backend_qihu360_image")


class _Resp:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._payload


def _png_b64() -> str:
    return base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8).decode("ascii")


def _install_ppt_image_fake(mod, monkeypatch, *, response: _Resp, get_error: Exception | None = None):
    monkeypatch.setattr(mod.requests, "post", lambda *a, **k: response)

    def fake_get(url, headers=None, timeout=None):
        if get_error is not None:
            raise get_error
        raise AssertionError("unexpected download")

    monkeypatch.setattr(mod.requests, "get", fake_get)


def test_ppt_image_success_writes_one_image_event_with_real_model_and_size(ppt_image_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_image_mod
    _install_ppt_image_fake(mod, monkeypatch, response=_Resp({"data": [{"b64_json": _png_b64()}]}))
    mod.generate("一只猫", aspect_ratio="16:9", output_dir=str(tmp_path), filename="cat.png")
    events = _events(ledger)
    assert len(events) == 1
    event = events[0]
    assert event["kind"] == "image"
    assert event["model"] == PPT_DEFAULT_MODEL, "该 backend 只支持默认模型，原样记录"
    assert (event["width"], event["height"]) == (2048, 1152)
    assert isinstance(event["elapsed_ms"], int) and event["elapsed_ms"] >= 0
    assert len(event["event_id"]) == 32
    assert set(event) == {"event_id", "kind", "model", "width", "height", "elapsed_ms"}, "只发契约字段"


def test_ppt_image_http_error_writes_nothing(ppt_image_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_image_mod
    _install_ppt_image_fake(mod, monkeypatch, response=_Resp({"error": "content policy"}, status_code=400))
    with pytest.raises(Exception):
        mod.generate("坏请求", output_dir=str(tmp_path), filename="x.png", max_retries=0)
    assert _events(ledger) == []


def test_ppt_image_200_without_image_writes_nothing(ppt_image_mod, ledger, monkeypatch, tmp_path):
    mod = ppt_image_mod
    _install_ppt_image_fake(mod, monkeypatch, response=_Resp({"data": []}))
    with pytest.raises(RuntimeError, match="empty data"):
        mod.generate("空数据", output_dir=str(tmp_path), filename="x.png", max_retries=0)
    assert _events(ledger) == []
    _install_ppt_image_fake(mod, monkeypatch, response=_Resp({"data": [{"foo": "bar"}]}))
    with pytest.raises(RuntimeError, match="missing url/b64_json"):
        mod.generate("无图字段", output_dir=str(tmp_path), filename="x.png", max_retries=0)
    assert _events(ledger) == [], "200 但无图不写"


def test_ppt_image_cdn_download_failure_keeps_the_event(ppt_image_mod, ledger, monkeypatch, tmp_path):
    """供应商已返回含图的 200 → 消耗已发生；本地下载失败不抹去账本行。"""
    mod = ppt_image_mod
    _install_ppt_image_fake(mod, monkeypatch, response=_Resp({"data": [{"url": "https://cdn.example/x.png"}]}), get_error=ConnectionError("cdn down"))
    with pytest.raises(Exception):
        mod.generate("下载会失败", output_dir=str(tmp_path), filename="x.png", max_retries=0)
    events = _events(ledger)
    assert len(events) == 1 and events[0]["kind"] == "image"
