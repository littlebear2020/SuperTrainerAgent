"""Worker mp4 短路 bug 修复：_finalize_output 无条件原子覆盖 output.mp4。

R4 修订：测试直接调用真实 _finalize_output（非自证式），用真实文件系统验证。
"""

import importlib.util
import sys
import types
from pathlib import Path

WORKER_PIPE = Path(__file__).resolve().parents[2] / "docker/production-worker/pipelines"


def _load_helpers():
    """加载 _ffmpeg_helpers（含待实现的 _finalize_output）。"""
    sys.modules.setdefault("pipelines", types.ModuleType("pipelines"))
    spec = importlib.util.spec_from_file_location("pipelines._ffmpeg_helpers", WORKER_PIPE / "_ffmpeg_helpers.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["pipelines._ffmpeg_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_finalize_overwrites_existing_output(tmp_path):
    """convert 短路(result != final_raw) + output.mp4 已存在 → 原子覆盖为新内容。"""
    helpers = _load_helpers()

    task_dir = tmp_path / "task"
    task_dir.mkdir()
    final_raw = task_dir / "output.mp4"
    final_raw.write_bytes(b"OLD-CONTENT")

    new_video = tmp_path / "with_bgm.mp4"
    new_video.write_bytes(b"NEW-CONTENT")

    result = helpers._finalize_output(new_video, final_raw)

    assert result == final_raw
    assert final_raw.read_bytes() == b"NEW-CONTENT"
    assert not new_video.exists()


def test_finalize_shortcircuit_when_convert_already_wrote(tmp_path):
    """convert 真转码时 result == final_raw → 不做任何 move（final_raw 已就位）。"""
    helpers = _load_helpers()
    final_raw = tmp_path / "output.mp4"
    final_raw.write_bytes(b"CONVERTED")
    result = helpers._finalize_output(final_raw, final_raw)
    assert result == final_raw
    assert final_raw.read_bytes() == b"CONVERTED"


def test_finalize_atomic_no_partial_window(tmp_path):
    """覆盖期间不存在 partial/空文件窗口（os.replace 原子性）。"""
    helpers = _load_helpers()
    final_raw = tmp_path / "output.mp4"
    final_raw.write_bytes(b"OLD")
    new_video = tmp_path / "new.mp4"
    new_video.write_bytes(b"NEW")
    helpers._finalize_output(new_video, final_raw)
    assert not list(tmp_path.glob("*.tmp"))
    assert final_raw.read_bytes() == b"NEW"
