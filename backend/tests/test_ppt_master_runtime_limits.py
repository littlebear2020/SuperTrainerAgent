"""PPT Master runtime_limits 测试（恢复重写）。"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/runtime_limits.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("runtime_limits", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_buffer_keeps_tail_when_overflow(mod):
    buf = mod.BoundedTextBuffer(max_chars=10)
    buf.append("0123456789")
    buf.append("ABCDEFGHIJ")
    assert buf.getvalue() == "ABCDEFGHIJ"
    assert buf.truncated_chars == 10
    assert len(buf) == 10


def test_buffer_no_truncation_under_limit(mod):
    buf = mod.BoundedTextBuffer(max_chars=100)
    buf.append("hello")
    assert buf.getvalue() == "hello"
    assert buf.truncated_chars == 0


def test_buffer_rejects_nonpositive_max(mod):
    with pytest.raises(ValueError):
        mod.BoundedTextBuffer(max_chars=0)


def test_rss_mb_positive(mod):
    assert mod.rss_mb() > 0
