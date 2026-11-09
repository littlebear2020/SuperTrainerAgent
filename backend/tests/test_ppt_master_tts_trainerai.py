"""PPT Master TTS trainerai 后端测试（恢复重写，含 360 SSE 空 code 回归）。"""

import importlib.util
import io
import os
import sys
import wave
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/tts_backends/backend_trainerai.py"
SCRIPTS_DIR = REPO_ROOT / "skills/public/ppt-master/scripts"
NOTES_MOD_PATH = SCRIPTS_DIR / "notes_to_audio.py"


@pytest.fixture()
def mod(monkeypatch):
    for key in ("QIHU_API_KEY", "ZYUN_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    spec = importlib.util.spec_from_file_location("backend_trainerai_tts", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _wav_bytes(frames: int = 100, rate: int = 24000) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)
    return buf.getvalue()


def test_generate_requires_api_key(mod):
    with pytest.raises(RuntimeError, match="QIHU_API_KEY|ZYUN_API_KEY"):
        mod.read_trainerai_api_key()


def test_output_extension_is_wav(mod):
    assert mod.output_extension() == ".wav"


def test_resolve_voice_id_plain_int(mod):
    assert mod.resolve_voice_id("146") == 146


def test_resolve_voice_id_preset_prefix(mod):
    assert mod.resolve_voice_id("preset:158") == 158


def test_resolve_voice_id_unknown_falls_back_to_default(mod):
    assert mod.resolve_voice_id("no-such-voice") == mod.DEFAULT_VOICE_ID


def test_merge_wav_chunks_single(mod):
    chunk = _wav_bytes()
    assert mod._merge_wav_chunks([chunk]) == chunk


def test_merge_wav_chunks_multi_concatenates_frames(mod):
    merged = mod._merge_wav_chunks([_wav_bytes(100), _wav_bytes(200)])
    with wave.open(io.BytesIO(merged), "rb") as w:
        assert w.getnframes() == 300
        assert w.getframerate() == 24000


def test_parse_sse_event_empty_code_is_not_error(mod):
    """回归：360 SSE 末尾 usage 事件 code="" 不得误判为错误（bde232508 同款）。"""
    mod._raise_for_event_code("")  # 不抛异常即通过
    mod._raise_for_event_code("0")  # 0 = 成功
    mod._raise_for_event_code(None)  # 无 code 字段


def test_parse_sse_event_real_error_raises(mod):
    with pytest.raises(RuntimeError):
        mod._raise_for_event_code("1001")


def test_synthesize_sse_payload_and_parsing(mod, monkeypatch):
    """请求体/事件结构必须严格对齐 qihu_tts_client（防回归：字段名错则 live 必败）。"""
    import base64 as b64
    import json as jsonlib

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def __iter__(self):
            wav = b64.b64encode(_wav_bytes()).decode()
            yield b"data: " + jsonlib.dumps({"code": 0, "data": {"content": wav}}).encode() + b"\n"
            yield b"\n"
            # 空 code 的 usage 事件：不得报错、不得产出音频
            yield b"data: " + jsonlib.dumps({"code": "", "usage": 12}).encode() + b"\n"
            yield b"\n"
            yield b"data: [DONE]\n"

    def fake_urlopen(req, timeout=0):
        captured["body"] = jsonlib.loads(req.data.decode())
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(mod, "urlopen", fake_urlopen)
    chunks = mod._synthesize_sse("你好", 146, "test-key")
    assert captured["body"] == {
        "model": "360/tts-stream",
        "input": "你好",
        "voice": 146,
        "response_format": "base64",
    }
    assert captured["auth"] == "Bearer test-key"
    assert len(chunks) == 1


@pytest.fixture()
def notes_mod(monkeypatch):
    """importlib 加载 notes_to_audio（scripts/ 需上 sys.path 供 config/tts_backends 导入）。"""
    monkeypatch.syspath_prepend(str(SCRIPTS_DIR))
    spec = importlib.util.spec_from_file_location("notes_to_audio", NOTES_MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    # @dataclass + future annotations 在 exec 时经 sys.modules 解析模块，须先注册
    monkeypatch.setitem(sys.modules, "notes_to_audio", m)
    spec.loader.exec_module(m)
    # 通用名 config 不残留 sys.modules，避免遮蔽其他测试的同名导入
    for name in [n for n in sys.modules if n == "config" or n.startswith("tts_backends")]:
        sys.modules.pop(name)
    return m


def test_env_loader_includes_trainerai_prefixes(tmp_path, monkeypatch, notes_mod):
    """.env 兜底加载必须含 QIHU_/ZYUN_（trainerai 后端的 key 此前永远加载不到）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("QIHU_API_KEY=test-qihu-key\nZYUN_API_KEY=test-zyun\n", encoding="utf-8")
    for key in ("QIHU_API_KEY", "ZYUN_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.chdir(tmp_path)
    try:
        notes_mod._load_tts_env_file()
        assert os.environ.get("QIHU_API_KEY") == "test-qihu-key"
        assert os.environ.get("ZYUN_API_KEY") == "test-zyun"
    finally:
        # setdefault 直写 os.environ，monkeypatch 不追踪；手动清理防跨文件污染
        for key in ("QIHU_API_KEY", "ZYUN_API_KEY"):
            os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# P8 provider 注册链断言（§八.1，2026-09-11 升级 6.3.0 新增）：
# 升级重打 P8 时此链路无安全网——notes_to_audio.py 的六个接线点必须全部在。
# 源码级断言（不 import：notes_to_audio 依赖 pptx_workspace 等 vendored 模块）。
# ---------------------------------------------------------------------------


def test_notes_to_audio_registers_trainerai_provider():
    source = NOTES_MOD_PATH.read_text(encoding="utf-8")
    # 1) import
    assert "backend_trainerai" in source, "notes_to_audio.py 未 import backend_trainerai"
    # 2) --provider choices + 默认 trainerai（P9）
    assert 'choices=["edge", "elevenlabs", "minimax", "qwen", "cosyvoice", "trainerai"]' in source, "--provider choices 缺 trainerai"
    assert 'default="trainerai"' in source, "--provider 默认值不是 trainerai（P9）"
    # 3) 构造分支 + 4) 生成分发分支 + 5) manifest 分支 + 6) list-voices 分支
    assert 'args.provider == "trainerai"' in source, "构造分支缺 trainerai"
    assert 'backend.provider == "trainerai"' in source, "生成分发分支缺 trainerai"
    assert 'backend.provider == "trainerai":' in source, "manifest 分支缺 trainerai"
    assert "backend_trainerai.PRESET_VOICES" in source, "list-voices 分支缺 trainerai"


def test_notes_to_audio_env_prefixes_include_qihu_zyun():
    source = NOTES_MOD_PATH.read_text(encoding="utf-8")
    assert '"QIHU_"' in source and '"ZYUN_"' in source, "env 白名单缺 QIHU_/ZYUN_ 前缀"
