"""Admin endpoints for media pricing (C2; model-level keys + C7 hardening, 2026-08-27 成本可信化批次).

Mirrors :mod:`routers.model_pricing` but for media providers (image/video/TTS).
Pricing key = (provider, model_id, media_type): ``model_id`` null marks the
**provider-level default row**; a set ``model_id`` overrides it for one model
(matched against ``external_api_calls.metadata_json.model`` after alias
normalization - see ``_shared/media_constants.PROVIDER_MODEL_ALIASES``).

C7 三防线（A6/A12 用户拍板）：
- 幅度告警：已有键 ``max(新,旧)/min(新,旧) > 5`` 需 ``confirm=true``（新键放行）；
- ``(provider, model_id, media_type)`` 重复（payload 内 / 部分唯一索引）-> 422 非 500；
- 跨表币种一致性：与 LLM ``model_pricing`` 已存币种冲突 -> 422。
单项 PATCH/DELETE 与 PUT 一样调 ``invalidate_media_pricing_cache()``。

Default seed prices are baked in as fallback when no DB row exists, so the
console shows a reasonable estimate before the operator configures anything.
Seed 的唯一权威定义在 ``_shared/media_pricing_resolver.DEFAULT_MEDIA_PRICING``
（C7 单点产出，估算逻辑的邻居）；本模块 re-export 供既有 import 兼容。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, select

from app.gateway._shared.media_pricing_resolver import DEFAULT_MEDIA_PRICING  # noqa: F401 - re-export (backwards compat)
from app.gateway.admin_auth import require_admin
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.media_pricing.model import MediaPricingRow
from deerflow.persistence.model_pricing.model import ModelPricingRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

VALID_UNITS = {"per_image", "per_kilo_token", "per_second", "per_kilo_char"}
VALID_MEDIA_TYPES = {"image", "video", "tts"}


class MediaPricingInput(BaseModel):
    """One media pricing entry, as supplied by an admin."""

    model_config = ConfigDict(extra="forbid")

    unit: str = Field(..., description="Billing unit: per_image / per_kilo_token / per_second / per_kilo_char")
    unit_price: float = Field(..., ge=0, description="Price per unit")
    currency: str = Field(default="CNY", max_length=8)


class MediaPricingEntry(MediaPricingInput):
    """One media pricing entry, as returned to the admin UI."""

    provider: str
    model_id: str | None = Field(default=None, max_length=64, description="Model id this row prices; null = provider-level default row")
    media_type: str
    updated_at: datetime | None = None
    updated_by: str = ""
    source: str = "db"


class MediaPricingResponse(BaseModel):
    pricing: list[MediaPricingEntry] = Field(default_factory=list)


class MediaPricingUpdateEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(..., max_length=32)
    model_id: str | None = Field(default=None, max_length=64)
    media_type: str = Field(..., max_length=16)
    unit: str = Field(...)
    unit_price: float = Field(..., ge=0)
    currency: str = Field(default="CNY", max_length=8)


class MediaPricingReplaceRequest(BaseModel):
    pricing: list[MediaPricingUpdateEntry]
    # C7（A12 用户拍板）：幅度 >5× 的已有键更新需显式二次确认；新键直接放行。
    confirm: bool = Field(default=False, description="Acknowledge >5x unit_price changes on existing keys")


class MediaPricingPatchRequest(BaseModel):
    """C7 单项 upsert：一条价目行（含 model_id，null=provider 级默认行）。"""

    entry: MediaPricingUpdateEntry
    confirm: bool = Field(default=False)


#: A12：幅度告警阈值--max(新,旧)/min(新,旧) > 5 需二次确认（40 倍错价拦不住的教训）。
_MAGNITUDE_RATIO = 5.0


def _entry_key(e: MediaPricingUpdateEntry) -> tuple[str, str | None, str]:
    return (e.provider, e.model_id or None, e.media_type)


def _session_factory_or_503():
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Media pricing requires a SQL database backend.")
    return sf


def _invalidate_cache() -> None:
    """C7：PUT/PATCH/DELETE 共用的缓存失效义务（漏了最长 60s 旧价）。"""
    from app.gateway.routers.console import invalidate_media_pricing_cache

    invalidate_media_pricing_cache()


def _rows_to_response(rows) -> MediaPricingResponse:
    return MediaPricingResponse(
        pricing=[
            MediaPricingEntry(
                provider=r.provider,
                model_id=r.model_id,
                media_type=r.media_type,
                unit=r.unit,
                unit_price=r.unit_price,
                currency=r.currency,
                updated_at=r.updated_at,
                updated_by=r.updated_by,
                source="db",
            )
            for r in rows
        ]
    )


async def _magnitude_guard(session, entries: list[MediaPricingUpdateEntry], confirm: bool) -> None:
    """已有键的 unit_price 幅度告警（A12：仅对已有键生效，新键直接放行）。"""
    existing = {(r.provider, r.model_id, r.media_type): r.unit_price for r in (await session.execute(select(MediaPricingRow))).scalars().all()}
    violations: list[str] = []
    for e in entries:
        old = existing.get(_entry_key(e))
        if old is None or old <= 0 or e.unit_price <= 0:
            continue
        ratio = max(e.unit_price, old) / min(e.unit_price, old)
        if ratio > _MAGNITUDE_RATIO:
            violations.append(f"{e.provider}/{e.model_id or '*'} {e.media_type}: {old} -> {e.unit_price} ({ratio:.1f}x)")
    if violations and not confirm:
        raise HTTPException(
            status_code=422,
            detail=(f"Unit price changed by more than {_MAGNITUDE_RATIO}x on existing keys - pass confirm=true to apply: " + "; ".join(violations)),
        )


async def _cross_table_currency_guard(session, currency: str) -> None:
    """跨表币种一致性：media 价目与 LLM model_pricing 已存币种冲突 -> 422。"""
    model_currency = (await session.execute(select(ModelPricingRow.currency).limit(1))).scalar()
    if model_currency and currency.strip().upper() != model_currency.strip().upper():
        raise HTTPException(
            status_code=422,
            detail=f"Currency {currency.strip().upper()!r} conflicts with the LLM model_pricing table currency {model_currency.strip().upper()!r} - one currency per deployment.",
        )


def _validate_entry_shape(entry: MediaPricingUpdateEntry) -> None:
    if entry.unit not in VALID_UNITS:
        raise HTTPException(status_code=422, detail=f"Invalid unit '{entry.unit}'; must be one of {VALID_UNITS}")
    if entry.media_type not in VALID_MEDIA_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid media_type '{entry.media_type}'; must be one of {VALID_MEDIA_TYPES}",
        )


@router.get(
    "/media-pricing",
    response_model=MediaPricingResponse,
    summary="Get Media Pricing",
    description="Admin-only: the per-provider media unit prices stored in the database, with built-in defaults for unconfigured providers.",
)
async def get_media_pricing() -> MediaPricingResponse:
    sf = _session_factory_or_503()
    async with sf() as session:
        rows = (await session.execute(select(MediaPricingRow))).scalars().all()

    db_keys: set[tuple[str, str | None, str]] = set()
    entries: list[MediaPricingEntry] = []

    for row in rows:
        db_keys.add((row.provider, row.model_id, row.media_type))
        entries.append(
            MediaPricingEntry(
                provider=row.provider,
                model_id=row.model_id,
                media_type=row.media_type,
                unit=row.unit,
                unit_price=row.unit_price,
                currency=row.currency,
                updated_at=row.updated_at,
                updated_by=row.updated_by,
                source="db",
            )
        )

    # Default fallback layer - only for provider-level keys not in DB
    # (defaults carry no model_id by construction).
    for default in DEFAULT_MEDIA_PRICING:
        key = (default["provider"], None, default["media_type"])
        if key not in db_keys:
            entries.append(MediaPricingEntry(**default, source="default"))

    return MediaPricingResponse(pricing=entries)


@router.put(
    "/media-pricing",
    response_model=MediaPricingResponse,
    summary="Update Media Pricing",
    description="Admin-only: replace the stored media unit prices. Full-table replacement semantics. Existing-key unit_price changes beyond 5x require confirm=true.",
)
async def update_media_pricing(request: Request, body: MediaPricingReplaceRequest) -> MediaPricingResponse:
    # Validate units and media_types
    currencies: set[str] = set()
    seen_keys: set[tuple[str, str | None, str]] = set()
    for entry in body.pricing:
        _validate_entry_shape(entry)
        # C2: duplicate (provider, model_id, media_type) inside one payload would
        # hit the partial unique index mid-insert and surface as a 500 - reject
        # up front with an actionable 422 instead.
        key = _entry_key(entry)
        if key in seen_keys:
            raise HTTPException(
                status_code=422,
                detail=f"Duplicate pricing key in payload: provider={entry.provider!r} model_id={entry.model_id!r} media_type={entry.media_type!r}",
            )
        seen_keys.add(key)
        currencies.add(entry.currency.strip().upper())

    # Refuse mixed currencies (same constraint as model_pricing)
    if len(currencies) > 1:
        raise HTTPException(
            status_code=422,
            detail=f"All media pricing entries must share one currency; payload mixes {sorted(currencies)}.",
        )

    sf = _session_factory_or_503()
    user = getattr(getattr(request.state, "auth", None), "user", None)
    actor = str(getattr(user, "id", "") or "")
    now = datetime.now(UTC)

    async with sf() as session:
        # C7 三防线：跨表币种一致性 + 已有键幅度告警（A6/A12）。
        if body.pricing:
            await _cross_table_currency_guard(session, body.pricing[0].currency)
        await _magnitude_guard(session, body.pricing, body.confirm)
        await session.execute(delete(MediaPricingRow))
        for entry in body.pricing:
            session.add(
                MediaPricingRow(
                    provider=entry.provider,
                    model_id=entry.model_id or None,
                    media_type=entry.media_type,
                    unit=entry.unit,
                    unit_price=entry.unit_price,
                    currency=entry.currency,
                    updated_at=now,
                    updated_by=actor,
                )
            )
        await session.commit()
        rows = (await session.execute(select(MediaPricingRow))).scalars().all()

    logger.info("media pricing updated by %s: %d entries", actor or "<unknown>", len(body.pricing))

    # Invalidate the process-level cache so the budget gate picks up new prices
    # immediately (not after the 60s TTL).
    _invalidate_cache()

    db_keys: set[tuple[str, str | None, str]] = {(r.provider, r.model_id, r.media_type) for r in rows}
    entries = [
        MediaPricingEntry(
            provider=r.provider,
            model_id=r.model_id,
            media_type=r.media_type,
            unit=r.unit,
            unit_price=r.unit_price,
            currency=r.currency,
            updated_at=r.updated_at,
            updated_by=r.updated_by,
            source="db",
        )
        for r in rows
    ]
    for default in DEFAULT_MEDIA_PRICING:
        key = (default["provider"], None, default["media_type"])
        if key not in db_keys:
            entries.append(MediaPricingEntry(**default, source="default"))

    return MediaPricingResponse(pricing=entries)


@router.patch(
    "/media-pricing",
    response_model=MediaPricingResponse,
    summary="Patch Media Pricing (single entry)",
    description="Admin-only: upsert ONE media pricing entry (provider + model_id + media_type key). Same magnitude/currency guards as PUT; invalidates the process cache.",
)
async def patch_media_pricing(request: Request, body: MediaPricingPatchRequest) -> MediaPricingResponse:
    """C7 单项 upsert：修前只有全量替换 PUT（改一行也要重发全表）。"""
    entry = body.entry
    _validate_entry_shape(entry)

    sf = _session_factory_or_503()
    user = getattr(getattr(request.state, "auth", None), "user", None)
    actor = str(getattr(user, "id", "") or "")
    now = datetime.now(UTC)
    key = _entry_key(entry)

    async with sf() as session:
        await _cross_table_currency_guard(session, entry.currency)
        await _magnitude_guard(session, [entry], body.confirm)
        # Upsert: delete the exact key then insert (model_id NULL vs set are
        # distinct rows - the partial unique index keys on all three columns).
        await session.execute(
            delete(MediaPricingRow).where(
                MediaPricingRow.provider == key[0],
                MediaPricingRow.media_type == key[2],
                (MediaPricingRow.model_id.is_(None) if key[1] is None else MediaPricingRow.model_id == key[1]),
            )
        )
        session.add(
            MediaPricingRow(
                provider=entry.provider,
                model_id=entry.model_id or None,
                media_type=entry.media_type,
                unit=entry.unit,
                unit_price=entry.unit_price,
                currency=entry.currency,
                updated_at=now,
                updated_by=actor,
            )
        )
        await session.commit()
        rows = (await session.execute(select(MediaPricingRow))).scalars().all()

    logger.info("media pricing patched by %s: %s/%s %s", actor or "<unknown>", entry.provider, entry.model_id or "*", entry.media_type)

    _invalidate_cache()
    return _rows_to_response(rows)


@router.delete(
    "/media-pricing",
    response_model=MediaPricingResponse,
    summary="Delete Media Pricing (single entry)",
    description="Admin-only: delete ONE media pricing entry by key. Omit model_id for the provider-level default row. Invalidates the process cache.",
)
async def delete_media_pricing(
    request: Request,
    provider: str = Query(..., max_length=32),
    media_type: str = Query(..., max_length=16),
    model_id: str | None = Query(default=None, max_length=64),
) -> MediaPricingResponse:
    """C7 单项删除：修前删一行=全表 PUT（易误清整表）。"""
    sf = _session_factory_or_503()
    user = getattr(getattr(request.state, "auth", None), "user", None)
    actor = str(getattr(user, "id", "") or "")

    async with sf() as session:
        stmt = select(MediaPricingRow).where(
            MediaPricingRow.provider == provider,
            MediaPricingRow.media_type == media_type,
            (MediaPricingRow.model_id.is_(None) if model_id is None else MediaPricingRow.model_id == model_id),
        )
        row = (await session.execute(stmt)).scalars().first()
        if row is None:
            raise HTTPException(status_code=404, detail=f"No media pricing row for provider={provider!r} model_id={model_id!r} media_type={media_type!r}")
        await session.execute(
            delete(MediaPricingRow).where(
                MediaPricingRow.provider == provider,
                MediaPricingRow.media_type == media_type,
                (MediaPricingRow.model_id.is_(None) if model_id is None else MediaPricingRow.model_id == model_id),
            )
        )
        await session.commit()
        rows = (await session.execute(select(MediaPricingRow))).scalars().all()

    logger.info("media pricing deleted by %s: %s/%s %s", actor or "<unknown>", provider, model_id or "*", media_type)

    _invalidate_cache()
    return _rows_to_response(rows)
