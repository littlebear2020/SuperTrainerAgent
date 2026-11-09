"""Smoke test: the strict Blockbuster gate is wired up and actively catching.

Independent of any specific production code path, asserts that calling a
known blocking IO function directly from an ``async def`` (without an
``asyncio.to_thread`` wrapper) raises ``BlockingError`` when the caller
stack passes through ``deerflow.*``. If this test ever stops raising, the
gate machinery itself is broken — typical causes are ``scanned_modules``
misconfiguration, accidental removal of the Blockbuster dev dependency, or
the conftest hookwrapper no longer firing.

This is the meta-test that protects every other test in this directory
from silent regressions (a green gate that no longer catches anything is
worse than no gate at all).
"""

from __future__ import annotations

import asyncio
import inspect
import os
import sys
import types
from pathlib import Path

import pytest
from blockbuster import BlockingError
from support.detectors.blocking_io_runtime import _mask_pathological_module_file_attrs, detect_blocking_io_strict

pytestmark = pytest.mark.anyio


async def test_gate_catches_unoffloaded_blocking_io_in_deerflow_module(tmp_path: Path) -> None:
    """Positive control: a blocking call reached via a deerflow.* caller stack
    must raise BlockingError. (Was xfail'd due to sys.modules stub pollution
    from worker-pipeline test modules breaking inspect.getsourcefile; the
    detector now masks pathological __file__ attrs while the gate is active.)"""
    from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

    db_file = tmp_path / "subdir" / "store.db"

    with pytest.raises(BlockingError):
        ensure_sqlite_parent_dir(str(db_file))


async def test_gate_walk_survives_stub_replacing_real_module_registration(tmp_path: Path) -> None:
    """Regression: inspect._filesbymodname cache poisoning across module swaps.

    Real-suite timeline (parallel workers import every test module, then run):
    a worker-pipeline test registers the REAL ``pipelines._ffmpeg_helpers``
    (str ``__file__``) and a frame walk caches it in ``inspect._filesbymodname``;
    later a fabricating stub (module-level ``__getattr__`` answers every missing
    attr, including ``__file__``) takes over the same name. The gate mask then
    neutralizes the fabricated ``__file__`` for the next walk. Masking to None
    made ``inspect.getmodule``'s sys.modules loop compare None against the stale
    truthy cache entry and dive into ``inspect.getabsfile`` → ``inspect.getfile``,
    which raises ``TypeError: <module ... from None> is a built-in module`` —
    the walk crashed instead of raising BlockingError. The mask must use a
    truthy str so both failure modes (AttributeError on fabricated values,
    TypeError on falsy values) stay dead.
    """
    from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

    modname = "zz_gate_probe_module"
    real = types.ModuleType(modname)
    real.__file__ = str(tmp_path / "real_probe.py")
    stub = types.ModuleType(modname)

    def _fabricate(name: str):  # same shape as the worker-pipeline test stubs
        def _stub(*a, **k):
            return None

        _stub.__name__ = name
        return _stub

    stub.__getattr__ = _fabricate

    def _make_trigger(tag: str):
        # A unique co_filename per walk forces getmodule's full sys.modules
        # scan (cache-hit frames never reach it) — the poisoning surface.
        src = "def _t(db):\n    ensure_sqlite_parent_dir(db)\n"
        ns = {"ensure_sqlite_parent_dir": ensure_sqlite_parent_dir}
        exec(compile(src, str(tmp_path / f"probe_callsite_{tag}.py"), "exec"), ns)
        return ns["_t"]

    saved = sys.modules.get(modname)
    try:
        sys.modules[modname] = real
        with pytest.raises(BlockingError):
            _make_trigger("walk1")(str(tmp_path / "a" / "x.db"))
        assert inspect._filesbymodname.get(modname) == real.__file__, "前提：真实路径已被帧走查缓存"

        sys.modules[modname] = stub
        # 与 gate 窗口起点同款掩码（stub 在窗口外注册，故这里显式补一道）。
        with _mask_pathological_module_file_attrs(), pytest.raises(BlockingError):
            _make_trigger("walk2")(str(tmp_path / "b" / "y.db"))
    finally:
        if saved is None:
            sys.modules.pop(modname, None)
        else:
            sys.modules[modname] = saved
        inspect._filesbymodname.pop(modname, None)
        for key in [k for k, v in inspect.modulesbyfile.items() if v == modname]:
            inspect.modulesbyfile.pop(key, None)


async def test_gate_allows_blocking_io_offloaded_to_thread(tmp_path: Path) -> None:
    """The same blocking call is fine when offloaded off the event loop."""
    from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

    db_file = tmp_path / "subdir" / "store.db"

    await asyncio.to_thread(ensure_sqlite_parent_dir, str(db_file))

    assert db_file.parent.exists()


async def test_gate_restores_blockbuster_patches_after_exceptions() -> None:
    original_stat = os.stat

    with pytest.raises(RuntimeError, match="boom"):
        with detect_blocking_io_strict():
            raise RuntimeError("boom")

    assert os.stat is original_stat


@pytest.mark.allow_blocking_io
async def test_allow_blocking_io_marker_opts_out_of_gate(tmp_path: Path) -> None:
    """Verify the @pytest.mark.allow_blocking_io opt-out actually disables the gate."""
    from deerflow.runtime.store._sqlite_utils import ensure_sqlite_parent_dir

    db_file = tmp_path / "subdir" / "store.db"

    ensure_sqlite_parent_dir(str(db_file))

    assert db_file.parent.exists()
