"""PPT Master 生图 trainerai 适配器测试（恢复重写；ARK 优先对齐字节接入）。"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/image_backends/backend_trainerai.py"

ALL_KEYS = ("ARK_API_KEY", "DASHSCOPE_API_KEY", "ZHIPU_API_KEY", "VOLCENGINE_API_KEY")


@pytest.fixture()
def load(monkeypatch):
    def _load(env: dict[str, str]):
        for key in ALL_KEYS:
            monkeypatch.delenv(key, raising=False)
        for key, val in env.items():
            monkeypatch.setenv(key, val)
        spec = importlib.util.spec_from_file_location("backend_trainerai_image", MOD_PATH)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return m

    return _load


def test_resolve_prefers_ark(load):
    m = load({"ARK_API_KEY": "k1", "DASHSCOPE_API_KEY": "k2"})
    backend, module, label = m.resolve_backend()
    assert backend == "volcengine"
    assert module == "backend_volcengine"


def test_resolve_prefers_dashscope_when_no_ark(load):
    m = load({"DASHSCOPE_API_KEY": "k2", "ZHIPU_API_KEY": "k3"})
    backend, module, _ = m.resolve_backend()
    assert backend == "qwen"
    assert module == "backend_qwen"


def test_resolve_zhipu_when_only_zhipu(load):
    m = load({"ZHIPU_API_KEY": "k3"})
    assert m.resolve_backend()[0] == "zhipu"


def test_resolve_raises_when_no_key(load):
    m = load({})
    with pytest.raises(RuntimeError, match="No TrainerAI image provider key"):
        m.resolve_backend()
