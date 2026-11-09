"""Tests for UserChannelRegistry."""

import pytest

from app.channels.user_registry import UserChannelRegistry, get_user_channel_registry


def test_registry_init():
    registry = UserChannelRegistry()
    assert registry.get_status()["total_instances"] == 0


def test_get_status_empty():
    registry = UserChannelRegistry()
    status = registry.get_status()
    assert status["total_instances"] == 0
    assert status["instances"] == []


def test_get_nonexistent():
    registry = UserChannelRegistry()
    assert registry.get("user-1", "feishu") is None


def test_singleton_is_none_by_default(monkeypatch):
    import app.channels.user_registry as mod

    monkeypatch.setattr(mod, "_registry", None)
    assert get_user_channel_registry() is None


class _FakeChannel:
    """Minimal Channel stand-in (same shape as test_channel_start_dedup.py):
    records config, flips is_running on start()."""

    def __init__(self, bus, config):
        self.bus = bus
        self.config = config
        self._running = False

    async def start(self):
        self._running = True

    async def stop(self):
        self._running = False

    @property
    def is_running(self):
        return self._running


@pytest.mark.asyncio
async def test_create_then_get_round_trip(monkeypatch, tmp_path):
    """Positive path: create_or_update registers a running instance that get()
    finds by (user_id, channel_type); stop() removes it again."""
    import app.channels.service as service_module
    from deerflow.config.paths import Paths

    # No global ChannelService running → cross-path dedup check is a no-op.
    monkeypatch.setattr(service_module, "get_channel_service", lambda: None)
    # Keep the per-user ChannelStore JSON under tmp_path, not the real base dir.
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: Paths(tmp_path))

    registry = UserChannelRegistry()
    monkeypatch.setattr(registry, "_get_channel_class", lambda channel_type: _FakeChannel)

    instance = await registry.create_or_update(
        user_id="u1",
        channel_type="feishu",
        credentials={"app_id": "cli_x", "app_secret": "s"},
        config={},
    )

    found = registry.get("u1", "feishu")
    assert found is instance
    assert found.channel.is_running
    status = registry.get_status()
    assert status["total_instances"] == 1
    assert status["instances"][0]["key"] == "u1:feishu"
    # Other user / other channel remain unregistered.
    assert registry.get("u2", "feishu") is None
    assert registry.get("u1", "wechat") is None

    assert await registry.stop("u1", "feishu") is True
    assert registry.get("u1", "feishu") is None
    assert registry.get_status()["total_instances"] == 0


def test_registry_unknown_channel_type():
    registry = UserChannelRegistry()
    with pytest.raises(ValueError, match="Unknown channel type"):
        registry._get_channel_class("nonexistent")
