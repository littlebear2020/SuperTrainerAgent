"""User channel configuration API.

Provides CRUD endpoints for users to manage their own IM channel
configurations (Feishu, WeChat, etc.) with encrypted credentials.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.gateway.auth.crypto import CredentialEncryption
from app.gateway.authz import require_auth
from app.gateway.models.channel_config import ChannelConfig

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/user-channels", tags=["user-channels"])


class ChannelCredentials(BaseModel):
    """Credentials for a channel (varies by channel type)."""

    # Feishu
    app_id: str | None = None
    app_secret: str | None = None

    # WeChat / Telegram
    bot_token: str | None = None

    # Allow extra fields for future channel types
    model_config = {"extra": "allow"}


class ChannelConfigUpdate(BaseModel):
    """Request body for creating/updating a channel config."""

    enabled: bool = False
    credentials: ChannelCredentials
    config: dict[str, Any] = Field(default_factory=dict)


class ChannelConfigResponse(BaseModel):
    """Response for a channel config (credentials masked)."""

    channel_type: str
    enabled: bool
    credentials: dict[str, str]  # Masked
    config: dict[str, Any]
    created_at: str
    updated_at: str


class ChannelConfigListResponse(BaseModel):
    """Response for listing all channel configs."""

    configs: list[ChannelConfigResponse]


def _mask_credential(value: str) -> str:
    """Mask a credential value for display."""
    if not value or len(value) < 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def _get_session_factory():
    """Get the async session factory from the persistence engine."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    if sf is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=503, detail="Database not initialized")
    return sf


def _build_masked_response(cfg: ChannelConfig, encryption: CredentialEncryption) -> ChannelConfigResponse:
    """Build a masked response from a ChannelConfig ORM object."""
    # Decrypt credentials for masking
    try:
        creds_json = encryption.decrypt(cfg.encrypted_credentials)
        creds = json.loads(creds_json)
    except Exception:
        creds = {}

    masked = {k: _mask_credential(str(v)) for k, v in creds.items()}

    # Parse config_json
    try:
        config = json.loads(cfg.config_json) if cfg.config_json else {}
    except Exception:
        config = {}

    return ChannelConfigResponse(
        channel_type=cfg.channel_type,
        enabled=cfg.enabled,
        credentials=masked,
        config=config,
        created_at=cfg.created_at.isoformat() if cfg.created_at else "",
        updated_at=cfg.updated_at.isoformat() if cfg.updated_at else "",
    )


@router.get("/", response_model=ChannelConfigListResponse)
@require_auth
async def list_user_channel_configs(request: Request) -> ChannelConfigListResponse:
    """List all channel configs for the current user."""
    auth = request.state.auth
    user_id = str(auth.user.id)

    sf = _get_session_factory()

    try:
        encryption = CredentialEncryption()
    except ValueError:
        logger.warning("CREDENTIAL_ENCRYPTION_KEY not set; returning empty channel configs")
        return ChannelConfigListResponse(configs=[])

    async with sf() as session:
        stmt = select(ChannelConfig).where(ChannelConfig.user_id == user_id)
        result = await session.execute(stmt)
        configs = result.scalars().all()

    return ChannelConfigListResponse(configs=[_build_masked_response(cfg, encryption) for cfg in configs])


@router.get("/{channel_type}", response_model=ChannelConfigResponse)
@require_auth
async def get_user_channel_config(channel_type: str, request: Request) -> ChannelConfigResponse:
    """Get a specific channel config for the current user."""
    from fastapi import HTTPException

    auth = request.state.auth
    user_id = str(auth.user.id)

    sf = _get_session_factory()

    try:
        encryption = CredentialEncryption()
    except ValueError:
        raise HTTPException(status_code=503, detail="Channel credentials encryption not configured (CREDENTIAL_ENCRYPTION_KEY not set)")

    async with sf() as session:
        stmt = select(ChannelConfig).where(
            ChannelConfig.user_id == user_id,
            ChannelConfig.channel_type == channel_type,
        )
        result = await session.execute(stmt)
        cfg = result.scalar_one_or_none()

    if not cfg:
        raise HTTPException(status_code=404, detail=f"Channel config {channel_type} not found")

    return _build_masked_response(cfg, encryption)


@router.put("/{channel_type}", response_model=ChannelConfigResponse)
@require_auth
async def update_user_channel_config(
    channel_type: str,
    update: ChannelConfigUpdate,
    request: Request,
) -> ChannelConfigResponse:
    """Create or update a channel config for the current user.

    Also starts/restarts the channel instance if enabled.
    """
    auth = request.state.auth
    user_id = str(auth.user.id)

    try:
        encryption = CredentialEncryption()
    except ValueError:
        raise HTTPException(status_code=503, detail="Channel credentials encryption not configured (CREDENTIAL_ENCRYPTION_KEY not set)")

    # Get incoming credentials (non-None only)
    incoming_creds = update.credentials.model_dump(exclude_none=True)

    # Filter out mask values — masks contain "****" and should not overwrite real credentials
    new_creds = {k: v for k, v in incoming_creds.items() if "****" not in str(v)}

    # Merge with existing credentials if there's an existing config
    sf = _get_session_factory()

    async with sf() as session:
        stmt = select(ChannelConfig).where(
            ChannelConfig.user_id == user_id,
            ChannelConfig.channel_type == channel_type,
        )
        result = await session.execute(stmt)
        existing_cfg = result.scalar_one_or_none()

    if existing_cfg and existing_cfg.encrypted_credentials:
        try:
            existing_json = encryption.decrypt(existing_cfg.encrypted_credentials)
            existing_creds = json.loads(existing_json)
            # Merge: new values override existing, but mask values are skipped
            merged_creds = {**existing_creds, **new_creds}
        except Exception:
            logger.warning("Failed to decrypt existing credentials for %s, using incoming only", channel_type)
            merged_creds = new_creds
    else:
        merged_creds = new_creds

    # Encrypt merged credentials
    creds_json = json.dumps(merged_creds)
    encrypted = encryption.encrypt(creds_json)

    # Serialize config
    config_json = json.dumps(update.config)

    async with sf() as session:
        # Upsert
        stmt = select(ChannelConfig).where(
            ChannelConfig.user_id == user_id,
            ChannelConfig.channel_type == channel_type,
        )
        result = await session.execute(stmt)
        cfg = result.scalar_one_or_none()

        if cfg:
            cfg.enabled = update.enabled
            cfg.encrypted_credentials = encrypted
            cfg.config_json = config_json
        else:
            cfg = ChannelConfig(
                user_id=user_id,
                channel_type=channel_type,
                enabled=update.enabled,
                encrypted_credentials=encrypted,
                config_json=config_json,
            )
            session.add(cfg)

        await session.commit()
        await session.refresh(cfg)

    # Start/restart channel instance if enabled
    if update.enabled:
        from app.channels.user_registry import get_user_channel_registry

        registry = get_user_channel_registry()
        if registry:
            try:
                await registry.create_or_update(
                    user_id=user_id,
                    channel_type=channel_type,
                    credentials=merged_creds,
                    config=update.config,
                )
            except Exception:
                logger.exception("Failed to start channel %s for user %s", channel_type, user_id)
    else:
        # Stop if disabled
        from app.channels.user_registry import get_user_channel_registry

        registry = get_user_channel_registry()
        if registry:
            await registry.stop(user_id, channel_type)

    # Build masked response
    masked = {k: _mask_credential(str(v)) for k, v in merged_creds.items()}

    return ChannelConfigResponse(
        channel_type=cfg.channel_type,
        enabled=cfg.enabled,
        credentials=masked,
        config=update.config,
        created_at=cfg.created_at.isoformat() if cfg.created_at else "",
        updated_at=cfg.updated_at.isoformat() if cfg.updated_at else "",
    )


@router.delete("/{channel_type}")
@require_auth
async def delete_user_channel_config(
    channel_type: str,
    request: Request,
) -> dict[str, str]:
    """Delete a channel config for the current user.

    Also stops the channel instance if running.
    """
    from fastapi import HTTPException

    auth = request.state.auth
    user_id = str(auth.user.id)

    sf = _get_session_factory()

    async with sf() as session:
        stmt = select(ChannelConfig).where(
            ChannelConfig.user_id == user_id,
            ChannelConfig.channel_type == channel_type,
        )
        result = await session.execute(stmt)
        cfg = result.scalar_one_or_none()

        if not cfg:
            raise HTTPException(status_code=404, detail=f"Channel config {channel_type} not found")

        # Delete from DB
        await session.delete(cfg)
        await session.commit()

    # Stop instance if running
    from app.channels.user_registry import get_user_channel_registry

    registry = get_user_channel_registry()
    if registry:
        await registry.stop(user_id, channel_type)

    return {"message": f"Channel config {channel_type} deleted"}
