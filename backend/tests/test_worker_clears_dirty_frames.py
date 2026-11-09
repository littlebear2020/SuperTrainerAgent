"""Worker process_task regression tests.

Step 6b (P0): process_task success branch must not raise NameError —
worker.py:269 referenced `result` (undefined; should be `prior`), crashing
every successful task after db_update committed status="ready".

Later (Task 6 R5): this file gains dirty_frames-clearing assertions.

worker.py lives in docker/production-worker/ (NOT on backend's sys.path) and
does a local `from pipelines.registry import resolve_pipeline` inside
process_task. So we stub `pipelines` + `pipelines.registry` in sys.modules
and load worker.py via importlib — same pattern as test_worker_incremental_manifest.py.
"""

import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

WORKER_PATH = Path(__file__).resolve().parents[2] / "docker/production-worker/worker.py"

# Per-test pipeline fn holder (resolve_pipeline reads from here at call time).
_resolve_holder: dict = {"fn": None}

# We stub sys.modules["pipelines"] + ["pipelines.registry"] so worker.py can be
# loaded without the real production-worker package on the path. But this stub
# is GLOBAL and would leak into other test files that import the real
# pipelines.registry (e.g. test_ai_visual_providers). So we set the stubs up
# per-test (autouse fixture), load worker fresh, and restore sys.modules at
# teardown.
worker = None  # populated by the autouse fixture each test


@pytest.fixture(autouse=True)
def _load_worker_and_restore_sys_modules():
    """Stub pipelines, load worker.py, yield, then restore sys.modules so the
    stub doesn't leak into subsequent test files in the same session."""
    saved = {}
    for key in ("pipelines", "pipelines.registry", "pipelines._qc_exception", "worker_under_test"):
        if key in sys.modules:
            saved[key] = sys.modules.pop(key)
    global worker
    worker = _load_worker_with_stubs()
    yield
    for key in ("pipelines", "pipelines.registry", "pipelines._qc_exception", "worker_under_test"):
        if key in sys.modules:
            del sys.modules[key]
    sys.modules.update(saved)


def _write_minimal_video(path):
    """造一个最小的真实 mp4（1 帧 320x240），供交付闸 ffprobe 校验通过。

    ffmpeg 不可用时退回写字节——此时 _validate_deliverable 的 ffprobe 分支会
    因取不到工具而降级为「只查大小」，同样能跑通。
    """
    import subprocess

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=1", "-t", "1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        path.write_bytes(b"\x00\x00\x00\x20ftyp")


def _load_worker_with_stubs():
    """Load worker.py with pipelines.* stubbed so process_task can import."""
    pipelines_pkg = types.ModuleType("pipelines")
    pipelines_pkg.__path__ = []
    sys.modules["pipelines"] = pipelines_pkg

    registry = types.ModuleType("pipelines.registry")

    class UnsupportedTaskType(Exception):
        pass

    registry.UnsupportedTaskType = UnsupportedTaskType
    registry.resolve_pipeline = lambda *a, **k: _resolve_holder["fn"]
    sys.modules["pipelines.registry"] = registry

    # worker.py 模块级 from pipelines._qc_exception import QCRepairNeeded
    # （T12 共享异常模块）——stub 缺此模块时 exec_module 即 ModuleNotFoundError
    # （09-08 遗留 5 红的根因，2026-09-09 feedback-round1 补）。
    qc_mod = types.ModuleType("pipelines._qc_exception")

    class QCRepairNeeded(Exception):
        def __init__(self, issues, *args):
            super().__init__(*args or (str(issues),))
            self.issues = issues

    qc_mod.QCRepairNeeded = QCRepairNeeded
    sys.modules["pipelines._qc_exception"] = qc_mod

    spec = importlib.util.spec_from_file_location("worker_under_test", WORKER_PATH)
    worker = importlib.util.module_from_spec(spec)
    sys.modules["worker_under_test"] = worker
    spec.loader.exec_module(worker)
    return worker


def _setup(monkeypatch, tmp_path, *, payload, result_json="{}"):
    """Stub db + metrics + task_dir; return (db_updates, task)."""
    db_updates = []

    def _db_update(task_id, **kwargs):
        db_updates.append({"task_id": task_id, **kwargs})

    class _Row:
        def fetchone(self):
            # lease_expires_at：租约查询列（worker.py:274 读该键；None → 租约查询
            # 早退，回落旧行为 300s 超时。2026-09-14 基线债：桩缺键 KeyError。
            return {"result": result_json, "lease_expires_at": None}

    class _Conn:
        def execute(self, q, p=()):
            return _Row()

        def close(self):
            pass

    monkeypatch.setattr(worker, "db_connect", lambda: _Conn())
    monkeypatch.setattr(worker, "db_update", _db_update)
    # P1-1 (Task 9): 成功回写改用 db_update_if_owned，须同时 stub
    monkeypatch.setattr(
        worker,
        "db_update_if_owned",
        lambda task_id, claimed_by, **kw: (
            _db_update(task_id, **kw),
            1,
        )[1],
    )
    monkeypatch.setattr(worker, "_metrics_inc", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_metrics_observe", lambda *a, **k: None)
    # process_task derives: task_dir = _DOMAIN_STORAGE[domain] / user_id / "tasks" / task_id
    monkeypatch.setattr(worker, "_DOMAIN_STORAGE", {"video_gen": str(tmp_path)})

    task_dir = tmp_path / "u1" / "tasks" / "t1"
    task_dir.mkdir(parents=True, exist_ok=True)
    out_path = task_dir / "output.mp4"
    # Task 11: worker 成功路径现在会用 ffprobe 校验交付物（0 字节/无视频轨 → failed）。
    # 本文件测的是 DB 记账，产物内容无关——但必须是**真视频**，否则会被交付闸正确拦下。
    _write_minimal_video(out_path)

    def _fake_pipeline_fn(task_dir, payload, options, progress_cb, **kw):
        return out_path

    _resolve_holder["fn"] = _fake_pipeline_fn

    task = {
        "id": "t1",
        "user_id": "u1",
        "domain": "video_gen",
        "task_type": "ai_visual",
        "payload": json.dumps(payload),
    }
    return db_updates, task


def test_worker_process_task_success_does_not_raise_nameerror(monkeypatch, tmp_path):
    """Step 6b regression: process_task success branch must not raise NameError.

    worker.py:269 referenced `result` (only a kwarg name in the db_update call,
    not a variable) — should be `prior`. Every successful task crashed post-
    commit with NameError, masked by the except branch. This test calls
    process_task end-to-end with stubs and asserts it completes + commits
    status="ready".
    """
    db_updates, task = _setup(monkeypatch, tmp_path, payload={"recompose_only": True})
    # Must not raise — guards the NameError fix at worker.py:269.
    worker.process_task(task)

    success = [u for u in db_updates if u.get("status") == "ready"]
    assert success, "expected a status=ready db_update"


def test_worker_clears_dirty_frames_on_recompose_success(monkeypatch, tmp_path):
    """R5: when payload has recompose_only=True, the success db_update must
    include dirty_frames='[]'. recompose_task no longer clears it pre-dispatch
    (so failed/retried recomposes keep the count); Worker clears on success."""
    db_updates, task = _setup(monkeypatch, tmp_path, payload={"recompose_only": True})
    worker.process_task(task)  # must not raise

    success = [u for u in db_updates if u.get("status") == "ready"]
    assert success, "expected a status=ready db_update"
    assert success[-1].get("dirty_frames") == "[]", "recompose success must clear dirty_frames"


def test_worker_does_not_touch_dirty_frames_on_non_recompose(monkeypatch, tmp_path):
    """Non-recompose tasks (e.g. initial render) must not touch dirty_frames —
    it is already '[]' for those (no-op), and clearing would be a surprising
    side effect on unrelated task types."""
    db_updates, task = _setup(monkeypatch, tmp_path, payload={"topic": "hello"})
    worker.process_task(task)  # must not raise

    success = [u for u in db_updates if u.get("status") == "ready"]
    assert success
    assert "dirty_frames" not in success[-1], "non-recompose tasks must not touch dirty_frames"
