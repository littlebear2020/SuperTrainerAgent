"""Cross-path channel start dedup (附项 2026-09-29, 修订版 a 案).

The lark SDK keeps a module-level event loop, so two live instances of one
channel type in the same process stomp each other (the 09-07/08-28/09-28
double "Feishu channel started" -> "This event loop is already running"
crashes: ChannelService from config.yaml + UserChannelRegistry from DB both
starting, zero dedup). The two paths now cross-check each other by identity
(feishu app_id):

- same identity -> double start of one channel -> skip the latter, warn
- different identity (or unreadable) -> cannot coexist -> explicit refusal
"""

import logging
from types import SimpleNamespace

import pytest

import app.channels.service as service_module
import app.channels.user_registry as registry_module
from app.channels.service import ChannelService, channel_identity, classify_channel_conflict
from app.channels.user_registry import UserChannelRegistry


class _FakeChannel:
    """Minimal Channel stand-in: records config, flips is_running on start()."""

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


def _patch_global_service(monkeypatch, running_identity):
    service = SimpleNamespace(get_running_identity=lambda channel_type: running_identity)
    monkeypatch.setattr(service_module, "get_channel_service", lambda: service)


# ---------------------------------------------------------------------------
# Identity + classification helpers
# ---------------------------------------------------------------------------


class TestChannelIdentity:
    def test_feishu_identity_is_app_id(self):
        assert channel_identity("feishu", {"app_id": "cli_A", "app_secret": "s"}) == "cli_A"

    def test_missing_credentials_yield_none(self):
        assert channel_identity("feishu", {}) is None
        assert channel_identity("feishu", None) is None
        assert channel_identity("feishu", {"app_id": "   "}) is None

    def test_boolean_values_are_not_identities(self):
        assert channel_identity("feishu", {"app_id": True, "app_secret": "s"}) == "s"


class TestClassifyChannelConflict:
    def test_same_identity_is_duplicate(self):
        assert classify_channel_conflict("feishu", "cli_A", "cli_A") == "duplicate"

    def test_different_identity_is_conflict(self):
        assert classify_channel_conflict("feishu", "cli_B", "cli_A") == "conflict"

    def test_no_running_channel_is_none(self):
        assert classify_channel_conflict("feishu", "cli_A", None) is None

    def test_unreadable_identity_degrades_to_conflict(self):
        """Cannot prove sameness -> explicit refusal, not a silent stomp."""
        assert classify_channel_conflict("feishu", None, "cli_A") == "conflict"


# ---------------------------------------------------------------------------
# Registry side: create_or_update vs the global ChannelService
# ---------------------------------------------------------------------------


def _make_registry() -> UserChannelRegistry:
    return UserChannelRegistry(session_factory=None, encryption_service=None)


class TestRegistryCreateOrUpdateGuard:
    @pytest.mark.anyio
    async def test_duplicate_of_global_channel_is_refused(self, monkeypatch):
        _patch_global_service(monkeypatch, "cli_A")
        registry = _make_registry()

        with pytest.raises(ValueError, match="already running via the global config.yaml"):
            await registry.create_or_update(
                user_id="u1",
                channel_type="feishu",
                credentials={"app_id": "cli_A", "app_secret": "s"},
                config={},
            )
        assert registry._instances == {}

    @pytest.mark.anyio
    async def test_conflicting_global_channel_is_refused(self, monkeypatch):
        _patch_global_service(monkeypatch, "cli_A")
        registry = _make_registry()

        with pytest.raises(ValueError, match="cannot safely host two credentials"):
            await registry.create_or_update(
                user_id="u1",
                channel_type="feishu",
                credentials={"app_id": "cli_B", "app_secret": "s"},
                config={},
            )
        assert registry._instances == {}

    @pytest.mark.anyio
    async def test_proceeds_when_global_channel_not_running(self, monkeypatch):
        _patch_global_service(monkeypatch, None)
        registry = _make_registry()
        monkeypatch.setattr(registry, "_get_channel_class", lambda channel_type: _FakeChannel)

        instance = await registry.create_or_update(
            user_id="u1",
            channel_type="feishu",
            credentials={"app_id": "cli_B", "app_secret": "s"},
            config={},
        )

        assert instance.channel.is_running
        # And the registry exposes the identity for the service-side check.
        assert registry.get_running_identities("feishu") == [("u1:feishu", "cli_B")]

        await instance.manager.stop()
        registry._instances.clear()

    @pytest.mark.anyio
    async def test_proceeds_without_global_service(self, monkeypatch):
        monkeypatch.setattr(service_module, "get_channel_service", lambda: None)
        registry = _make_registry()
        monkeypatch.setattr(registry, "_get_channel_class", lambda channel_type: _FakeChannel)

        instance = await registry.create_or_update(
            user_id="u2",
            channel_type="feishu",
            credentials={"app_id": "cli_C", "app_secret": "s"},
            config={},
        )

        assert instance.channel.is_running
        await instance.manager.stop()
        registry._instances.clear()


# ---------------------------------------------------------------------------
# Registry side: startup replay skips duplicates with a warning
# ---------------------------------------------------------------------------


class TestStartupReplaySkip:
    @pytest.mark.anyio
    async def test_same_identity_replay_skips_with_warning(self, monkeypatch, caplog):
        _patch_global_service(monkeypatch, "cli_A")
        registry = _make_registry()

        # If the guard fails to skip, create_or_update must not run.
        async def _fail_create_or_update(**kwargs):
            raise AssertionError("create_or_update must not be called for a duplicate replay")

        monkeypatch.setattr(registry, "create_or_update", _fail_create_or_update)

        cfg = SimpleNamespace(
            user_id="u1",
            channel_type="feishu",
            encrypted_credentials='{"app_id": "cli_A", "app_secret": "s"}',
            config_json="{}",
        )
        registry._encryption = SimpleNamespace(decrypt=lambda blob: blob)

        with caplog.at_level(logging.WARNING, logger="app.channels.user_registry"):
            await registry._start_one_from_db(cfg)

        assert "duplicates the globally configured channel" in caplog.text
        assert "cli_A" in caplog.text

    @pytest.mark.anyio
    async def test_conflicting_replay_surfaces_explicit_error(self, monkeypatch, caplog):
        _patch_global_service(monkeypatch, "cli_A")
        registry = _make_registry()
        monkeypatch.setattr(registry, "_get_channel_class", lambda channel_type: _FakeChannel)

        cfg = SimpleNamespace(
            user_id="u1",
            channel_type="feishu",
            encrypted_credentials='{"app_id": "cli_B", "app_secret": "s"}',
            config_json="{}",
        )
        registry._encryption = SimpleNamespace(decrypt=lambda blob: blob)

        with caplog.at_level(logging.ERROR, logger="app.channels.user_registry"):
            await registry._start_one_from_db(cfg)

        assert "cannot safely host two credentials" in caplog.text
        assert registry._instances == {}


# ---------------------------------------------------------------------------
# Service side: _start_channel vs the user registry
# ---------------------------------------------------------------------------


def _bare_service() -> ChannelService:
    service = object.__new__(ChannelService)
    service._channels = {}
    service.bus = None
    return service


def _patch_registry(monkeypatch, running):
    fake_registry = SimpleNamespace(get_running_identities=lambda channel_type: running if channel_type == "feishu" else [])
    monkeypatch.setattr(registry_module, "get_user_channel_registry", lambda: fake_registry)


class TestServiceStartChannelGuard:
    @pytest.mark.anyio
    async def test_duplicate_user_instance_skips_global_start(self, monkeypatch, caplog):
        _patch_registry(monkeypatch, [("u1:feishu", "cli_A")])
        service = _bare_service()

        with caplog.at_level(logging.WARNING, logger="app.channels.service"):
            started = await service._start_channel("feishu", {"enabled": True, "app_id": "cli_A", "app_secret": "s"})

        assert started is False
        assert service._channels == {}
        assert "already running as user instance" in caplog.text

    @pytest.mark.anyio
    async def test_conflicting_user_instance_refuses_global_start(self, monkeypatch, caplog):
        _patch_registry(monkeypatch, [("u1:feishu", "cli_A")])
        service = _bare_service()

        with caplog.at_level(logging.ERROR, logger="app.channels.service"):
            started = await service._start_channel("feishu", {"enabled": True, "app_id": "cli_B", "app_secret": "s"})

        assert started is False
        assert service._channels == {}
        assert "Refusing to start config.yaml channel feishu" in caplog.text

    @pytest.mark.anyio
    async def test_starts_normally_without_running_instances(self, monkeypatch):
        _patch_registry(monkeypatch, [])
        service = _bare_service()
        monkeypatch.setitem(
            __import__("sys").modules,
            "app.channels.feishu",
            SimpleNamespace(FeishuChannel=_FakeChannel),
        )

        started = await service._start_channel("feishu", {"enabled": True, "app_id": "cli_D", "app_secret": "s"})

        assert started is True
        assert "feishu" in service._channels
