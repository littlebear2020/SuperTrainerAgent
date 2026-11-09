"""Test configuration for the backend test suite.

Sets up sys.path and pre-mocks modules that would cause circular import
issues when unit-testing lightweight config/registry code in isolation.
"""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

# Make 'app' and 'deerflow' importable from any working directory
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

# Break the circular import chain that exists in production code:
#   deerflow.subagents.__init__
#     -> .executor (SubagentExecutor, SubagentResult)
#       -> deerflow.agents.thread_state
#         -> deerflow.agents.__init__
#           -> lead_agent.agent
#             -> subagent_limit_middleware
#               -> deerflow.subagents.executor  <-- circular!
#
# By injecting a mock for deerflow.subagents.executor *before* any test module
# triggers the import, __init__.py's "from .executor import ..." succeeds
# immediately without running the real executor module.
_executor_mock = MagicMock()
_executor_mock.SubagentExecutor = MagicMock
_executor_mock.SubagentResult = MagicMock
_executor_mock.SubagentStatus = MagicMock
_executor_mock.MAX_CONCURRENT_SUBAGENTS = 3
_executor_mock.get_background_task_result = MagicMock()

sys.modules["deerflow.subagents.executor"] = _executor_mock


@pytest.fixture()
def provisioner_module():
    """Load docker/provisioner/app.py as an importable test module.

    Shared by test_provisioner_kubeconfig and test_provisioner_pvc_volumes so
    that any change to the provisioner entry-point path or module name only
    needs to be updated in one place.
    """
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "docker" / "provisioner" / "app.py"
    spec = importlib.util.spec_from_file_location("provisioner_app_test", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Auto-set user context for every test unless marked no_auto_user
# ---------------------------------------------------------------------------
#
# Repository methods read ``user_id`` from a contextvar by default
# (see ``deerflow.runtime.user_context``). Without this fixture, every
# pre-existing persistence test would raise RuntimeError because the
# contextvar is unset. The fixture sets a default test user on every
# test; tests that explicitly want to verify behaviour *without* a user
# context should mark themselves ``@pytest.mark.no_auto_user``.


@pytest.fixture(autouse=True)
def _auto_user_context(request):
    """Inject a default ``test-user-autouse`` into the contextvar.

    Opt-out via ``@pytest.mark.no_auto_user``. Uses lazy import so that
    tests which don't touch the persistence layer never pay the cost
    of importing runtime.user_context.
    """
    if request.node.get_closest_marker("no_auto_user"):
        yield
        return

    try:
        from deerflow.runtime.user_context import (
            reset_current_user,
            set_current_user,
        )
    except ImportError:
        yield
        return

    user = SimpleNamespace(id="test-user-autouse", email="test@local")
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


# ---------------------------------------------------------------------------
# 离线 TTS（2026-10-12 CI 修复）：GitHub runner 无 .env（无 QIHU key），且
# edge-tts 对 GitHub runner IP 持续 403（2026-10-12 两次复跑确定性失败）——
# 此前 5 个用例靠真实外网 TTS 侥幸通过：本地因 app_config import 期
# load_dotenv() 恒有真 key、且本机 edge 可达而从未暴露。以下 fixture 把
# TTS 外呼整体拦下，用例自此离线确定性。
# ---------------------------------------------------------------------------


def _minimal_wav_bytes(duration_s: float = 0.2) -> bytes:
    """最小合法 WAV（44 字节头 + 静音采样）。ffmpeg/ffprobe 按内容嗅探可解析。

    2026-10-12 教训：mock 返回 b"wav_bytes" 这类假数据会在 wav→mp3 转码时
    抛错，被 _synthesize_qihu 的 except 吞掉后**静默落 edge 真外呼**——假数据
    必须过得了 ffmpeg，否则 mock 形同虚设。
    """
    import struct

    rate = 8000
    n = max(1, int(rate * duration_s))
    data = b"\x00\x00" * n
    hdr = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(data))
    return hdr + data


@pytest.fixture()
def tts_wav_bytes():
    """暴露最小 WAV 构造——给需要走真实 ``_synthesize_qihu`` 代码路径的用例。"""
    return _minimal_wav_bytes


@pytest.fixture()
def hermetic_tts(monkeypatch):
    """拦截 orchestration.tts 全部四条合成外呼（doubao/clone/qihu/edge）。

    假合成写最小 WAV 落盘并返回 TTSResult；任何未被拦净的真实外呼会被
    ``edge_tts.Communicate`` / ``QihuTTSClient`` tripwire 以 AssertionError
    响亮失败——不再允许「静默依赖外部服务」的假绿。需要验证 _synthesize_qihu
    真实内部逻辑的用例（如 test_week5_remediation 的参数形状断言）请勿用本
    fixture（它会整体替换掉被测对象），改用 tts_wav_bytes + 自行 mock 客户端。
    """
    from app.gateway.video_gen.orchestration import tts as tts_mod

    async def _fake(text, voice_id, speed, output_path, **kwargs):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(_minimal_wav_bytes())
        return tts_mod.TTSResult(audio_path=output_path, srt="", duration=0.2, actual_voice_id=None)

    for fn in ("_synthesize_doubao", "_synthesize_clone", "_synthesize_qihu", "_synthesize_edge"):
        monkeypatch.setattr(tts_mod, fn, _fake)

    def _tripwire(*args, **kwargs):
        raise AssertionError("真实 TTS 外呼未拦截干净（hermetic_tts tripwire）——检查新增合成入口是否绕过 orchestration.tts 四路径")

    monkeypatch.setattr("edge_tts.Communicate", _tripwire)
    from app.gateway.voice_twin import qihu_tts_client as _qtc

    monkeypatch.setattr(_qtc, "QihuTTSClient", _tripwire)
    return tts_mod
