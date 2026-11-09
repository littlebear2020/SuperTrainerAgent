"""Validation for operator-supplied model pricing (B2).

Pricing used to be an undeclared extra field on ``ModelConfig``
(``ConfigDict(extra="allow")``), so nothing checked it. Every way of getting
it wrong therefore failed *silently*, and silence here does not mean "no
number" — it means "a plausible wrong number". The three modes that existed
in ``console._build_pricing_map``:

1. **Typo'd key → 0.** ``float(raw.get("input_per_million") or 0)`` turns a
   misspelled ``input_per_milion`` into a zero input price.
2. **Typo'd key that survives.** The skip condition is
   ``input_price <= 0 and output_price <= 0``, so an entry with a broken
   *input* key but a valid output price is kept — priced-looking, billed at
   zero on the input side. Not "unpriced" (which honestly renders ``—``),
   but *under-billed*.
3. **Currency mismatch → platform-wide zero.** A single disagreeing entry
   makes the whole function ``return {}``, so *every* model becomes unpriced
   and total spend reads ``—``.

The read-side defenses stay where they are as a last line, but a valid write
can no longer produce any of these states. Rejecting at the boundary is what
turns a silent miscount into a 422 the operator can see.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

# The only currencies this deployment prices in. Kept permissive in shape
# (any 3-letter code) but normalized so " cny " and "CNY" never read as two
# different currencies and trip the consistency check.
_CURRENCY_MAX_LEN = 8


def get_yaml_pricing_entries() -> dict[str, dict[str, Any]]:
    """Read ``pricing`` annotations from ``config.yaml`` models.

    Returns a map of config model **name** → raw pricing dict
    (``currency``, ``input_per_million``, ``output_per_million``,
    ``input_cache_hit_per_million``).  Only models that carry a
    ``pricing`` dict are included.

    This is the shared extraction used by the pricing-admin GET endpoint
    (A0) so the panel can show yaml-fallback prices alongside DB rows.
    ``console._build_yaml_pricing_map`` keeps its own copy because it
    keys by *both* name and provider model id and skips zero-priced
    entries — different concerns, deliberately not unified here.
    """
    try:
        from deerflow.config import get_app_config

        models = get_app_config().models
    except Exception:  # pragma: no cover - defensive: config read must not break callers
        logger.warning("pricing_schema: failed to read model pricing from config", exc_info=True)
        return {}

    entries: dict[str, dict[str, Any]] = {}
    for model_cfg in models or []:
        raw = getattr(model_cfg, "pricing", None)
        if not isinstance(raw, dict):
            continue
        try:
            entries[model_cfg.name] = {
                "currency": str(raw.get("currency") or "CNY"),
                "input_per_million": float(raw.get("input_per_million") or 0),
                "output_per_million": float(raw.get("output_per_million") or 0),
                "input_cache_hit_per_million": (float(raw["input_cache_hit_per_million"]) if raw.get("input_cache_hit_per_million") is not None else None),
            }
        except (TypeError, ValueError):
            logger.warning("pricing_schema: ignoring malformed pricing on model %s", model_cfg.name)
            continue
    return entries


class ModelPricingInput(BaseModel):
    """One model's prices, as supplied by an admin.

    ``extra="forbid"`` is the whole point of this class: it converts a typo
    from a silent zero into a 422 naming the offending key.
    """

    model_config = ConfigDict(extra="forbid")

    currency: str = Field(default="CNY", max_length=_CURRENCY_MAX_LEN, description="ISO-ish currency code; one per deployment")
    input_per_million: float = Field(default=0.0, ge=0, description="Price per million cache-miss input tokens")
    output_per_million: float = Field(default=0.0, ge=0, description="Price per million output tokens")
    # Nullable on purpose. None => bill cache hits at the full input (miss)
    # price; 0.0 => hits are free. Collapsing None into 0.0 would silently
    # under-report spend for every deployment that has not set a hit price.
    #
    # Cache write tokens are intentionally not priced here: neither 360 nor
    # Ark responses return cache-write token counts, and the real amount
    # (when 360 supplies one) already includes cache-write cost. Adding a
    # field we cannot populate from any response would be noise.
    input_cache_hit_per_million: float | None = Field(
        default=None,
        ge=0,
        description="Price per million cache-hit input tokens; null bills hits at the miss price",
    )

    def normalized_currency(self) -> str:
        return self.currency.strip().upper()


def validate_pricing_payload(
    payload: dict[str, dict],
    *,
    existing_currency: str | None,
) -> dict[str, ModelPricingInput]:
    """Validate a whole pricing write, refusing mixed currencies.

    Args:
        payload: Map of model name to raw pricing dict.
        existing_currency: The currency already stored, if any. A write that
            disagrees with it is refused; a write that *replaces* everything
            may legitimately switch currency, which is why this is a
            parameter rather than a lookup inside.

    Returns:
        The validated entries, currency-normalized.

    Raises:
        ValidationError: A single entry is malformed (unknown key, negative
            price). Propagated as-is so the field name reaches the caller.
        ValueError: The payload mixes currencies, or disagrees with
            *existing_currency*.
    """
    validated_raw = {}
    for name, raw in payload.items():
        entry = ModelPricingInput(**raw)
        # Silently skip all-zero rows: an operator leaving a model unpriced
        # must not persist a 0-valued row to DB — that would make spend
        # read "¥0" instead of the correct "—" (unpriced).  Only skip when
        # BOTH input and output are ≤ 0; a model with a real price on one
        # side and 0 on the other is legitimate (e.g. free output).
        if entry.input_per_million <= 0 and entry.output_per_million <= 0:
            logger.info("validate_pricing_payload: skipping all-zero row for %s (unpriced)", name)
            continue
        validated_raw[name] = entry

    validated = validated_raw

    # If every row was skipped the payload is empty — that's a valid
    # "clear everything" operation (full-table replacement → empty table).
    if not validated:
        return {}

    currencies = {entry.normalized_currency() for entry in validated.values()}
    if len(currencies) > 1:
        raise ValueError(f"All models must share one currency; payload mixes {sorted(currencies)}. Mixed currencies disable cost reporting platform-wide.")

    if existing_currency and currencies:
        incoming = next(iter(currencies))
        stored = existing_currency.strip().upper()
        if incoming != stored:
            raise ValueError(f"Currency {incoming} conflicts with the stored currency {stored}. Update every model in one request to switch currency.")

    for entry in validated.values():
        entry.currency = entry.normalized_currency()

    return validated
