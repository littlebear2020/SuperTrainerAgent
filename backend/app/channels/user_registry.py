"""UserChannelRegistry — manages per-user channel instances."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from app.channels.base import Channel
from app.channels.message_bus import MessageBus
from app.channels.user_store import UserChannelStore

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SEC-H13 (批次 2 项 2.5): per-channel allowed config keys
# ---------------------------------------------------------------------------
# ``POST /api/user-channels`` used to accept a free-form ``config`` dict that
# was merged verbatim into the channel's config — letting a user set feishu
# ``domain`` (SSRF with app credentials attached), wechat ``base_url`` /
# ``cdn_base_url``, or an arbitrary ``state_dir`` (write-anywhere). The tables
# below were hand-inventoried from each channel implementation's actual
# ``config.get(...)`` reads (2026-09-04). Unknown keys are STRIPPED with a
# warning — never 422-rejected — so existing DB ``channel_config`` rows with
# out-of-table fields still load at startup init (compat 铁律).
# NOTE: wechat ``state_dir`` stays allowed (consumed key); confining it to a
# base-dir sandbox is deferred to batch 3 (seccomp / 最小化供给).
_CHANNEL_ALLOWED_CONFIG_KEYS: dict[str, frozenset[str]] = {
    "feishu": frozenset({"app_id", "app_secret", "domain", "allowed_users"}),
    "wecom": frozenset({"bot_id", "bot_secret", "working_message", "allowed_users"}),
    "slack": frozenset({"bot_token", "app_token", "allowed_users"}),
    "telegram": frozenset({"bot_token", "allowed_users"}),
    "dingtalk": frozenset({"client_id", "client_secret", "card_template_id", "allowed_users"}),
    "discord": frozenset({"bot_token", "allowed_guilds"}),
    "wechat": frozenset(
        {
            "bot_token",
            "ilink_app_id",
            "ilink_bot_id",
            "base_url",
            "cdn_base_url",
            "channel_version",
            "polling_timeout",
            "polling_retry_delay",
            "qrcode_poll_interval",
            "qrcode_poll_timeout",
            "qrcode_login_enabled",
            "qrcode_bot_type",
            "route_tag",
            "respect_server_longpoll_timeout",
            "max_inbound_image_bytes",
            "max_outbound_image_bytes",
            "max_inbound_file_bytes",
            "max_outbound_file_bytes",
            "allowed_file_extensions",
            "allowed_users",
            "state_dir",
        }
    ),
}

# feishu ``domain``: kept in the table (私有化飞书 / Lark 国际版) but validated
# for structure + official host suffix — otherwise a user could point the
# OpenAPI client (which carries app credentials) at an internal host.
_FEISHU_DOMAIN_ALLOWED_SUFFIXES = ("feishu.cn", "larksuite.com")


def _validate_feishu_domain(raw: Any) -> Any:
    """Return the domain if it passes scheme/host-suffix checks, else None."""
    from urllib.parse import urlparse

    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = urlparse(raw.strip())
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in ("http", "https") or not host or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        return None
    if not any(host == s or host.endswith("." + s) for s in _FEISHU_DOMAIN_ALLOWED_SUFFIXES):
        return None
    return raw


def _sanitize_channel_inputs(channel_type: str, config: dict[str, Any], credentials: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Strip out-of-table keys (warning, not reject) and validate feishu domain.

    Single funnel for both the HTTP create path and the startup init replay —
    ``create_or_update`` is the only entry that builds channel configs.
    """
    allowed = _CHANNEL_ALLOWED_CONFIG_KEYS.get(channel_type)
    if allowed is None:
        # Unknown channel type: let _get_channel_class raise its own error.
        return config, credentials

    def _filter(source: dict[str, Any], label: str) -> dict[str, Any]:
        kept = {k: v for k, v in source.items() if k in allowed}
        dropped = sorted(set(source) - set(kept))
        if dropped:
            logger.warning(
                "SEC-H13: dropped out-of-table %s keys for %s channel: %s (allowed: %s)",
                label,
                channel_type,
                dropped,
                sorted(allowed),
            )
        return kept

    config = _filter(config, "config")
    credentials = _filter(credentials, "credentials")

    if channel_type == "feishu" and "domain" in config:
        if _validate_feishu_domain(config["domain"]) is None:
            logger.warning(
                "SEC-H13: feishu domain %r failed scheme/host-suffix validation (allowed suffixes: %s); reverting to official default",
                config["domain"],
                _FEISHU_DOMAIN_ALLOWED_SUFFIXES,
            )
            config.pop("domain", None)

    return config, credentials


@dataclass
class ChannelInstance:
    """Represents a running channel instance for a specific user."""

    user_id: str
    channel_type: str
    channel: Channel
    bus: MessageBus
    store: UserChannelStore  # NOT USED in current message path — reserved for future migration from ChannelStore (JSON) to UserChannelStore (SQL)
    config: dict[str, Any]
    manager: Any

    @property
    def key(self) -> str:
        """Unique key for this instance."""
        return f"{self.user_id}:{self.channel_type}"


class UserChannelRegistry:
    """Manages all user-configured channel instances.

    Each user gets isolated channel instances with their own:
    - MessageBus (for message routing)
    - UserChannelStore (for thread mappings)
    - Channel configuration (encrypted credentials)

    Lifecycle:
    - create_or_update: Start/restart a user's channel
    - stop: Stop a user's channel
    - init: Load all enabled configs from DB and start them
    - shutdown: Stop all running instances
    """

    # Channel type -> import path mapping
    _CHANNEL_REGISTRY = {
        "feishu": "app.channels.feishu:FeishuChannel",
        "wechat": "app.channels.wechat:WechatChannel",
    }

    # Maximum instances per channel type (global limit)
    _MAX_INSTANCES_PER_TYPE = 100

    def __init__(
        self,
        session_factory: Any | None = None,
        encryption_service: Any | None = None,
    ):
        """Initialize registry.

        Args:
            session_factory: SQLAlchemy async session factory for UserChannelStore
            encryption_service: CredentialEncryption instance for decrypting credentials
        """
        self._instances: dict[str, ChannelInstance] = {}  # key -> instance
        self._lock = asyncio.Lock()
        self._session_factory = session_factory
        self._encryption = encryption_service
        self._running = False

        # Lazy-loaded channel classes
        self._channel_classes: dict[str, Any] = {}

    def _get_channel_class(self, channel_type: str) -> type:
        """Load and cache channel class."""
        if channel_type in self._channel_classes:
            return self._channel_classes[channel_type]

        import_path = self._CHANNEL_REGISTRY.get(channel_type)
        if not import_path:
            raise ValueError(f"Unknown channel type: {channel_type}")

        from deerflow.reflection import resolve_class

        cls = resolve_class(import_path, base_class=Channel)
        self._channel_classes[channel_type] = cls
        return cls

    async def create_or_update(
        self,
        user_id: str,
        channel_type: str,
        credentials: dict[str, Any],
        config: dict[str, Any],
    ) -> ChannelInstance:
        """Create or update a user's channel instance.

        Stops existing instance if running, then creates new one.

        Args:
            user_id: User identifier
            channel_type: Channel type (feishu, wechat, etc.)
            credentials: Decrypted credentials dict (app_id, app_secret, etc.)
            config: Additional configuration

        Returns:
            Running ChannelInstance

        Raises:
            ValueError: If channel type is unknown or instance limit reached
        """
        # SEC-H13 (批次 2 项 2.5): strip unknown keys + validate feishu domain
        # BEFORE anything consumes or persists the merged config. Covers both
        # the HTTP create path and the startup init replay (single funnel).
        config, credentials = _sanitize_channel_inputs(channel_type, config, credentials)

        key = f"{user_id}:{channel_type}"

        async with self._lock:
            # 附项 (2026-09-29, 修订版 a 案): the lark SDK keeps a module-level
            # event loop, so two live instances of one channel type in this
            # process stomp each other. Cross-check the global ChannelService
            # (config.yaml channels) BEFORE touching the existing instance:
            # same identity = double start of one channel, refuse with a skip
            # explanation; different identity = cannot coexist, refuse with
            # ops guidance. The startup replay path pre-checks duplicates and
            # skips with a plain warning instead (see _start_one_from_db).
            try:
                from app.channels.service import channel_identity, classify_channel_conflict, get_channel_service

                svc = get_channel_service()
                if svc is not None:
                    pending_identity = channel_identity(channel_type, {**config, **credentials})
                    running_identity = svc.get_running_identity(channel_type)
                    conflict = classify_channel_conflict(channel_type, pending_identity, running_identity)
                    if conflict == "duplicate":
                        raise ValueError(
                            f"Channel {channel_type} (identity {running_identity}) is already running via the "
                            f"global config.yaml channels.{channel_type} entry; not starting a duplicate user "
                            f"instance for {user_id}. Remove one side if you intended a switch."
                        )
                    if conflict == "conflict":
                        raise ValueError(
                            f"Cannot start {channel_type} for user {user_id}: a different {channel_type} "
                            f"(identity {running_identity}) is already running via config.yaml, and the "
                            f"{channel_type} SDK cannot safely host two credentials in one process. "
                            f"Disable one side: remove channels.{channel_type} from config.yaml, or stop this user instance."
                        )
            except ValueError:
                raise
            except Exception:
                logger.debug("Cross-path dedup check failed for user=%s channel=%s", user_id, channel_type, exc_info=True)

            # Stop existing instance
            if key in self._instances:
                old_instance = self._instances[key]
                try:
                    await old_instance.manager.stop()
                except Exception:
                    logger.exception("Error stopping old channel manager %s", key)
                try:
                    await old_instance.channel.stop()
                except Exception:
                    logger.exception("Error stopping old channel instance %s", key)
                del self._instances[key]

            # Check instance limit
            type_count = sum(1 for inst in self._instances.values() if inst.channel_type == channel_type)
            if type_count >= self._MAX_INSTANCES_PER_TYPE:
                raise ValueError(f"Maximum {channel_type} instances ({self._MAX_INSTANCES_PER_TYPE}) reached. Cannot create new instance for user {user_id}.")

            # Create isolated components
            bus = MessageBus()
            store = UserChannelStore(user_id=user_id, session_factory=self._session_factory)

            # Merge config + credentials
            merged_config = {**config, **credentials, "enabled": True}

            # Create per-user thread store and channel manager
            from pathlib import Path

            from app.channels.manager import ChannelManager
            from app.channels.store import ChannelStore
            from deerflow.config.paths import get_paths

            paths = get_paths()
            store_path = Path(paths.base_dir) / "channels" / "users" / str(user_id) / channel_type / "store.json"
            thread_store = ChannelStore(path=store_path)
            manager = ChannelManager(bus=bus, store=thread_store, user_id=str(user_id))
            await manager.start()

            # Create channel instance
            channel_cls = self._get_channel_class(channel_type)
            channel = channel_cls(bus=bus, config=merged_config)

            instance = ChannelInstance(
                user_id=user_id,
                channel_type=channel_type,
                channel=channel,
                bus=bus,
                store=store,
                config=merged_config,
                manager=manager,
            )

            # Start channel
            try:
                await channel.start()
                if not channel.is_running:
                    raise RuntimeError(f"Channel {channel_type} did not enter running state")
            except Exception:
                logger.exception("Failed to start channel %s for user %s", channel_type, user_id)
                await manager.stop()
                try:
                    await channel.stop()
                except Exception:
                    logger.exception("Error stopping channel %s after failed start", channel_type)
                raise

            self._instances[key] = instance
            logger.info("Channel instance started: %s", key)
            return instance

    def get_running_identities(self, channel_type: str) -> list[tuple[str, str | None]]:
        """Running ``(instance key, channel identity)`` pairs for a channel type.

        Feeds the cross-path dedup check (附项 2026-09-29): the global
        ChannelService refuses to double-start a channel a user instance
        already runs. Identity is None when the instance config carries no
        readable credential — callers treat that as unidentifiable.
        """
        from app.channels.service import channel_identity

        results: list[tuple[str, str | None]] = []
        for key, instance in self._instances.items():
            if instance.channel_type != channel_type:
                continue
            if not getattr(instance.channel, "is_running", False):
                continue
            results.append((key, channel_identity(channel_type, instance.config)))
        return results

    async def stop(self, user_id: str, channel_type: str) -> bool:
        """Stop a user's channel instance.

        Args:
            user_id: User identifier
            channel_type: Channel type

        Returns:
            True if instance was stopped, False if not found
        """
        key = f"{user_id}:{channel_type}"

        async with self._lock:
            if key not in self._instances:
                return False

            instance = self._instances[key]
            try:
                await instance.manager.stop()
            except Exception:
                logger.exception("Error stopping channel manager %s", key)
            try:
                await instance.channel.stop()
            except Exception:
                logger.exception("Error stopping channel instance %s", key)

            del self._instances[key]
            logger.info("Channel instance stopped: %s", key)
            return True

    def get(self, user_id: str, channel_type: str) -> ChannelInstance | None:
        """Get a running channel instance.

        Args:
            user_id: User identifier
            channel_type: Channel type

        Returns:
            ChannelInstance or None if not running
        """
        key = f"{user_id}:{channel_type}"
        return self._instances.get(key)

    def get_status(self) -> dict[str, Any]:
        """Get status of all running instances.

        Returns:
            Dict with instance counts and details
        """
        instances = []
        for key, inst in self._instances.items():
            instances.append(
                {
                    "key": key,
                    "user_id": inst.user_id,
                    "channel_type": inst.channel_type,
                    "running": inst.channel.is_running,
                }
            )

        return {
            "total_instances": len(self._instances),
            "instances": instances,
        }

    async def _start_one_from_db(self, cfg: Any) -> None:
        """Start a single channel from a DB config row during init().

        Decrypts credentials and calls create_or_update. Errors are
        logged and swallowed so that one failure does not block the
        remaining instances.
        """
        import json

        try:
            creds_json = self._encryption.decrypt(cfg.encrypted_credentials)
            credentials = json.loads(creds_json)
        except Exception:
            logger.exception(
                "Failed to decrypt credentials for user=%s channel=%s; skipping",
                cfg.user_id,
                cfg.channel_type,
            )
            return

        try:
            config = json.loads(cfg.config_json) if cfg.config_json else {}
        except Exception:
            config = {}

        # 附项 (2026-09-29): during startup replay, a same-identity duplicate of
        # the globally configured channel is tolerated — the global path owns
        # that channel, so warn and skip instead of surfacing an exception.
        try:
            from app.channels.service import channel_identity, classify_channel_conflict, get_channel_service

            svc = get_channel_service()
            if svc is not None:
                running_identity = svc.get_running_identity(cfg.channel_type)
                conflict = classify_channel_conflict(cfg.channel_type, channel_identity(cfg.channel_type, credentials), running_identity)
                if conflict == "duplicate":
                    logger.warning(
                        "UserChannelRegistry: channel %s for user=%s duplicates the globally configured channel (identity %s); skipping user instance (already running via config.yaml)",
                        cfg.channel_type,
                        cfg.user_id,
                        running_identity,
                    )
                    return
        except Exception:
            logger.debug("Startup dedup pre-check failed for user=%s channel=%s", cfg.user_id, cfg.channel_type, exc_info=True)

        try:
            await self.create_or_update(
                user_id=cfg.user_id,
                channel_type=cfg.channel_type,
                credentials=credentials,
                config=config,
            )
        except Exception:
            logger.exception(
                "Failed to start channel instance for user=%s channel=%s during init",
                cfg.user_id,
                cfg.channel_type,
            )

    async def init(self) -> None:
        """Initialize registry: load all enabled configs from DB and start instances.

        Called during Gateway lifespan startup. Reads all enabled channel
        configurations from the database, decrypts credentials, and starts
        a channel instance for each one so that IM bots come back online
        automatically after a Gateway restart.

        Starts all instances concurrently with a 30-second total timeout
        so that a single slow channel or a large number of configs cannot
        block Gateway startup indefinitely.
        """
        if self._running:
            return

        self._running = True

        if not self._session_factory:
            logger.warning("UserChannelRegistry initialized without session factory, skipping init")
            return

        if not self._encryption:
            logger.warning("UserChannelRegistry initialized without encryption service, cannot decrypt credentials; skipping DB load")
            return

        # Load all enabled configs from database and start instances.
        from app.gateway.models.channel_config import ChannelConfig

        async with self._session_factory() as session:
            from sqlalchemy import select

            stmt = select(ChannelConfig).where(ChannelConfig.enabled.is_(True))
            result = await session.execute(stmt)
            enabled_configs = result.scalars().all()

        if not enabled_configs:
            logger.info("UserChannelRegistry: no enabled channel configs in DB")
            return

        # Start all instances concurrently with a total timeout.
        # Use self._instances as the source of truth for counting successes
        # so that the timeout path does not depend on gather's return value.
        existing = len(self._instances)
        tasks = [self._start_one_from_db(cfg) for cfg in enabled_configs]
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=30.0,
            )
        except TimeoutError:
            logger.warning(
                "UserChannelRegistry init: timeout after 30s, some channels may not be started",
            )

        started = len(self._instances) - existing
        logger.info(
            "UserChannelRegistry: started %d/%d enabled channel instances from DB",
            started,
            len(enabled_configs),
        )

    async def shutdown(self) -> None:
        """Stop all running instances.

        Called during Gateway lifespan shutdown.
        """
        if not self._running:
            return

        async with self._lock:
            for key in list(self._instances.keys()):
                try:
                    instance = self._instances[key]
                    await instance.manager.stop()
                except Exception:
                    logger.exception("Error stopping channel manager %s during shutdown", key)
                try:
                    await instance.channel.stop()
                except Exception:
                    logger.exception("Error stopping channel instance %s during shutdown", key)

            self._instances.clear()

        self._running = False
        logger.info("UserChannelRegistry shut down")


# Singleton instance
_registry: UserChannelRegistry | None = None


def get_user_channel_registry() -> UserChannelRegistry | None:
    """Get the singleton UserChannelRegistry instance."""
    return _registry


async def init_user_channel_registry(
    session_factory: Any | None = None,
    encryption_service: Any | None = None,
) -> UserChannelRegistry:
    """Initialize and start the global UserChannelRegistry.

    Called from Gateway lifespan.
    """
    global _registry
    if _registry is not None:
        return _registry

    _registry = UserChannelRegistry(
        session_factory=session_factory,
        encryption_service=encryption_service,
    )
    await _registry.init()
    return _registry


async def shutdown_user_channel_registry() -> None:
    """Shutdown the global UserChannelRegistry.

    Called from Gateway lifespan.
    """
    global _registry
    if _registry is not None:
        await _registry.shutdown()
        _registry = None
