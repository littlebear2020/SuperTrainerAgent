"""Admin endpoints for per-model pricing (B4).

Lets an operator set the prices that drive every spend figure on the console,
without editing ``config.yaml`` (mounted read-only in the deployment) and
without a restart (Gateway dependencies read a startup config snapshot).

Scope is deliberately narrow — **prices only**. Model add/edit/delete,
``display_name``, ``api_key``/``api_base`` all stay in ``config.yaml`` so this
does not grow into a second, diverging model registry.

Authorization uses the shared ``require_admin`` dependency. It cannot be
built on ``@require_permission``: ``authz._authenticate`` grants every
authenticated user the full ``_ALL_PERMISSIONS`` list and that list contains
no admin entry, so a permission check is always true here.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select

from app.gateway.admin_auth import require_admin
from app.gateway.pricing_schema import get_yaml_pricing_entries, validate_pricing_payload
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.model_pricing.model import ModelPricingRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class ModelPricingEntry(BaseModel):
    """One model's stored prices, as returned to the admin UI."""

    currency: str = Field(..., description="Currency code; one per deployment")
    input_per_million: float = Field(..., description="Price per million cache-miss input tokens")
    output_per_million: float = Field(..., description="Price per million output tokens")
    input_cache_hit_per_million: float | None = Field(
        default=None,
        description="Price per million cache-hit input tokens; null bills hits at the miss price",
    )
    updated_at: datetime | None = Field(default=None, description="When this price was last changed")
    updated_by: str = Field(default="", description="Id of the admin who last changed it")
    source: str = Field(default="db", description='"db" = operator-set via this endpoint; "yaml" = fallback from config.yaml annotation')


class ModelPricingResponse(BaseModel):
    pricing: dict[str, ModelPricingEntry] = Field(default_factory=dict, description="Map of model name to its stored prices")


class ModelPricingUpdateRequest(BaseModel):
    pricing: dict[str, dict] = Field(..., description="Map of model name to its prices; validated per pricing_schema")


def _session_factory_or_503():
    """Pricing lives in SQL; the memory backend has nowhere to put it."""
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(status_code=503, detail="Model pricing requires a SQL database backend.")
    return sf


def _to_entry(row: ModelPricingRow) -> ModelPricingEntry:
    return ModelPricingEntry(
        currency=row.currency,
        input_per_million=row.input_per_million,
        # ``is not None``, never truthiness: 0.0 is a real price ("hits are
        # free") and must not read as null ("bill hits at the miss price").
        input_cache_hit_per_million=(row.input_cache_hit_per_million if row.input_cache_hit_per_million is not None else None),
        output_per_million=row.output_per_million,
        updated_at=row.updated_at,
        updated_by=row.updated_by,
    )


@router.get(
    "/model-pricing",
    response_model=ModelPricingResponse,
    summary="Get Model Pricing",
    description="Admin-only: the per-model prices currently stored in the database.",
)
async def get_model_pricing() -> ModelPricingResponse:
    """Effective pricing: DB rows (``source="db"``) overlaid on yaml
    fallbacks (``source="yaml"``).

    This mirrors the read-side merge in ``console._build_pricing_map`` so
    the panel shows the *same* effective prices the console uses for spend
    calculation.  Without this, an admin would see a model with a yaml
    annotation but no DB row as "unpriced" even though the console is
    billing it at the yaml price.
    """
    sf = _session_factory_or_503()
    async with sf() as session:
        rows = (await session.execute(select(ModelPricingRow))).scalars().all()

    pricing: dict[str, ModelPricingEntry] = {}

    # yaml fallback layer
    for name, raw in get_yaml_pricing_entries().items():
        pricing[name] = ModelPricingEntry(
            currency=raw["currency"],
            input_per_million=raw["input_per_million"],
            output_per_million=raw["output_per_million"],
            input_cache_hit_per_million=raw["input_cache_hit_per_million"],
            source="yaml",
        )

    # DB layer overrides yaml
    for row in rows:
        pricing[row.model_name] = _to_entry(row)

    return ModelPricingResponse(pricing=pricing)


@router.put(
    "/model-pricing",
    response_model=ModelPricingResponse,
    summary="Update Model Pricing",
    description="Admin-only: replace the stored per-model prices. Rejects unknown keys, negative prices, and mixed currencies with 422.",
)
async def update_model_pricing(request: Request, body: ModelPricingUpdateRequest) -> ModelPricingResponse:
    """Replace the stored pricing table with *body*.

    Validation runs over the **whole** payload before anything is written, so
    a malformed entry cannot half-apply a batch and leave the operator with a
    mix of old and new prices they did not choose.
    """
    sf = _session_factory_or_503()

    # C7（2026-08-27 成本可信化）：换币死锁修复。PUT 是全量替换--payload 就是
    # 期望的完整终态，换币（CNY -> USD 全表同换）是 docstring 一直承诺的合法
    # 操作；修前把存量币种传入 ``existing_currency`` 校验，导致换币请求 100%
    # 被 422 拒死（"Update every model in one request" 照做也过不去）。
    # 跨行混币仍由 ``validate_pricing_payload`` 在 payload 内部拒绝。
    try:
        validated = validate_pricing_payload(body.pricing, existing_currency=None)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    user = getattr(getattr(request.state, "auth", None), "user", None)
    actor = str(getattr(user, "id", "") or "")
    now = datetime.now(UTC)

    async with sf() as session:
        # Full replace: the payload is the complete desired state, so a model
        # dropped from it becomes unpriced (renders "—") rather than silently
        # keeping a stale price nobody can see in the UI any more.
        await session.execute(delete(ModelPricingRow))
        for name, entry in validated.items():
            session.add(
                ModelPricingRow(
                    model_name=name,
                    currency=entry.currency,
                    input_per_million=entry.input_per_million,
                    output_per_million=entry.output_per_million,
                    input_cache_hit_per_million=entry.input_cache_hit_per_million,
                    updated_at=now,
                    updated_by=actor,
                )
            )
        await session.commit()
        rows = (await session.execute(select(ModelPricingRow))).scalars().all()

    logger.info("model pricing updated by %s: %d model(s)", actor or "<unknown>", len(validated))

    # Same merge as GET: yaml fallback + DB overlay so the panel stays
    # consistent after a save.
    pricing: dict[str, ModelPricingEntry] = {}
    for name, raw in get_yaml_pricing_entries().items():
        pricing[name] = ModelPricingEntry(
            currency=raw["currency"],
            input_per_million=raw["input_per_million"],
            output_per_million=raw["output_per_million"],
            input_cache_hit_per_million=raw["input_cache_hit_per_million"],
            source="yaml",
        )
    for row in rows:
        pricing[row.model_name] = _to_entry(row)
    return ModelPricingResponse(pricing=pricing)
