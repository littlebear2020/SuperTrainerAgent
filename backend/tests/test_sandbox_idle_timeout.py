from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.community.aio_sandbox import aio_sandbox_provider as provider_mod
from deerflow.config.sandbox_config import SandboxConfig


def _load_provider_config(monkeypatch: pytest.MonkeyPatch, idle_timeout: int | None = None) -> dict:
    sandbox = SandboxConfig(use="deerflow.sandbox.local:LocalSandboxProvider", idle_timeout=idle_timeout)
    monkeypatch.setattr(provider_mod, "get_app_config", lambda: SimpleNamespace(sandbox=sandbox))
    provider = provider_mod.AioSandboxProvider.__new__(provider_mod.AioSandboxProvider)
    return provider._load_config()


def test_local_provider_defaults_idle_timeout_to_7200(monkeypatch: pytest.MonkeyPatch):
    assert _load_provider_config(monkeypatch)["idle_timeout"] == 7200


@pytest.mark.parametrize("idle_timeout", [30, 0])
def test_local_provider_preserves_explicit_idle_timeout(monkeypatch: pytest.MonkeyPatch, idle_timeout: int):
    assert _load_provider_config(monkeypatch, idle_timeout)["idle_timeout"] == idle_timeout


def test_default_warm_pool_does_not_reap_one_hour_idle_entry():
    provider = provider_mod.AioSandboxProvider.__new__(provider_mod.AioSandboxProvider)
    provider._config = {}
    provider._lock = threading.Lock()
    entry = MagicMock()
    released_at = time.time() - 3600
    provider._warm_pool = {"sandbox-old": (entry, released_at)}
    provider._destroy_warm_entry = MagicMock()

    provider._reap_expired_warm()

    assert provider._warm_pool == {"sandbox-old": (entry, released_at)}
    provider._destroy_warm_entry.assert_not_called()
