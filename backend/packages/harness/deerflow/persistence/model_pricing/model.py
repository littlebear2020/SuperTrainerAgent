"""ORM model for the ``model_pricing`` table.

One row per model, keyed by the name the usage buckets report. Holds the
operator-editable prices that back the console's spend figures.

Why a table rather than ``models[*].pricing`` in ``config.yaml``: the
deployment mounts ``config.yaml`` read-only (``:ro``), so a yaml-backed
pricing admin would need a compose change on every price edit. The yaml
stays as the offline/bootstrap fallback; the DB overrides it.

``input_cache_hit_per_million`` is **deliberately nullable** and ``null``
must not be normalized to ``0``:

* ``null`` — no hit price configured → cache-hit input tokens bill at the
  full miss price (``console._token_cost``'s conservative default).
* ``0``    — hits are genuinely free.

Storing 0 for "unconfigured" would silently under-report spend.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class ModelPricingRow(Base):
    __tablename__ = "model_pricing"

    # The model identifier as it appears in ``runs.token_usage_by_model``
    # buckets / ``runs.model_name`` — that is what the console looks up.
    model_name: Mapped[str] = mapped_column(String(200), primary_key=True)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    input_per_million: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    output_per_million: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    # Nullable on purpose — see the module docstring. None != 0.0.
    input_cache_hit_per_million: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    # Audit trail: prices are money, so record which admin last touched them.
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
