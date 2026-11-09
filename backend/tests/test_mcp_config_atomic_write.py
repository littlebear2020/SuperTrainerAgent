"""Tests for atomic + serialized persistence of PUT /api/mcp/config (T6).

The previous implementation used a bare ``open(path, "w") + json.dump``, which
truncates the live config file before writing. A crash, cancellation, or
serialization error mid-write therefore left an empty/partial
``extensions_config.json`` — every configured MCP server disappeared at once.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from app.gateway.routers.mcp import _atomic_write_json, _config_write_lock


@pytest.fixture()
def config_path(tmp_path: Path) -> Path:
    path = tmp_path / "extensions_config.json"
    path.write_text(json.dumps({"mcpServers": {"metaso": {"enabled": True}}}), encoding="utf-8")
    return path


class TestAtomicWriteJson:
    def test_writes_payload(self, config_path: Path) -> None:
        _atomic_write_json(config_path, {"mcpServers": {"github": {"enabled": True}}})

        assert json.loads(config_path.read_text(encoding="utf-8")) == {"mcpServers": {"github": {"enabled": True}}}

    def test_original_survives_a_failing_write(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A crash mid-write must leave the previous config fully intact."""
        original = config_path.read_text(encoding="utf-8")

        def boom(*args, **kwargs):
            raise RuntimeError("process died mid-write")

        monkeypatch.setattr("app.gateway.routers.mcp.json.dump", boom)

        with pytest.raises(RuntimeError):
            _atomic_write_json(config_path, {"mcpServers": {}})

        assert config_path.read_text(encoding="utf-8") == original
        assert json.loads(config_path.read_text(encoding="utf-8"))["mcpServers"]["metaso"]["enabled"] is True

    def test_no_temp_files_left_behind_on_failure(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("replace failed")

        monkeypatch.setattr("app.gateway.routers.mcp.os.replace", boom)

        with pytest.raises(RuntimeError):
            _atomic_write_json(config_path, {"mcpServers": {}})

        assert [p.name for p in config_path.parent.iterdir()] == [config_path.name]

    def test_ebusy_replace_falls_back_to_inplace_write(self, config_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
        """DEF-2: single-file bind mounts deny rename-onto-target with EBUSY.

        The fallback must still persist the payload (in-place write + fsync)
        and log a warning — the 500-every-toggle failure mode is worse than a
        non-atomic write that only happens under this mount anomaly.
        """
        import errno as errno_module

        def ebusy(*args, **kwargs):
            raise OSError(errno_module.EBUSY, "Device or resource busy")

        monkeypatch.setattr("app.gateway.routers.mcp.os.replace", ebusy)

        with caplog.at_level("WARNING", logger="app.gateway.routers.mcp"):
            _atomic_write_json(config_path, {"mcpServers": {"github": {"enabled": True}}})

        assert json.loads(config_path.read_text(encoding="utf-8")) == {"mcpServers": {"github": {"enabled": True}}}
        assert any("EBUSY" in rec.message for rec in caplog.records)

    def test_non_ebusy_oserror_propagates_and_cleans_up(self, config_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Crash injection on the atomic path: only EBUSY may degrade — any
        other OSError re-raises, leaving the original intact and no temp files."""
        import errno as errno_module

        original = config_path.read_text(encoding="utf-8")

        def denied(*args, **kwargs):
            raise OSError(errno_module.EACCES, "Permission denied")

        monkeypatch.setattr("app.gateway.routers.mcp.os.replace", denied)

        with pytest.raises(OSError):
            _atomic_write_json(config_path, {"mcpServers": {}})

        assert config_path.read_text(encoding="utf-8") == original
        assert [p.name for p in config_path.parent.iterdir()] == [config_path.name]

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "extensions_config.json"

        _atomic_write_json(target, {"mcpServers": {}})

        assert json.loads(target.read_text(encoding="utf-8")) == {"mcpServers": {}}

    def test_non_ascii_names_survive_round_trip(self, config_path: Path) -> None:
        """Non-ASCII server names must reload identically (escaped or not)."""
        _atomic_write_json(config_path, {"mcpServers": {"秘塔搜索": {"enabled": True}}})

        reloaded = json.loads(config_path.read_text(encoding="utf-8"))
        assert reloaded == {"mcpServers": {"秘塔搜索": {"enabled": True}}}


class TestConfigWriteLock:
    def test_lock_is_reentrant_safe_across_threads(self) -> None:
        """The module-level lock must actually serialize concurrent writers."""
        overlaps: list[bool] = []
        active = 0
        state_lock = threading.Lock()
        barrier = threading.Barrier(2)

        def writer() -> None:
            nonlocal active
            barrier.wait()
            with _config_write_lock:
                with state_lock:
                    active += 1
                    overlaps.append(active > 1)
                with state_lock:
                    active -= 1

        threads = [threading.Thread(target=writer) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert overlaps == [False, False]
