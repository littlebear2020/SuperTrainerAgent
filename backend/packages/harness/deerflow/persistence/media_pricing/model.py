"""ORM model for the ``media_pricing`` table.

One row per pricing key — either **provider-level** (``model_id IS NULL``,
the default rate for that provider+media_type) or **model-level** (``model_id``
set, overriding the provider default for one specific model id). Keyed by the
provider name and media type that ``external_api_calls`` reports. Holds the
operator-editable unit prices that back the console's media cost estimates
and the ViMax budget gate.

C2（2026-08-27 成本可信化批次）：
- surrogate ``id`` PK replaces the old composite ``PrimaryKeyConstraint`` —
  ``model_id`` is nullable, and a composite PK over a nullable column is not
  portable (Postgres rejects NULL PKs; SQLite's NULL-PK tolerance is a quirk).
- uniqueness is enforced by **two partial unique indexes** instead:
  ``(provider, media_type) WHERE model_id IS NULL`` (one provider-level row)
  and ``(provider, model_id, media_type) WHERE model_id IS NOT NULL`` — plain
  unique indexes can't do this because SQL treats NULLs as distinct.

``unit`` is one of: ``per_image``, ``per_kilo_token``, ``per_second``,
``per_kilo_char``. The console's cost estimator uses it to decide which
metadata field to multiply the price by.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class MediaPricingRow(Base):
    __tablename__ = "media_pricing"
    __table_args__ = (
        Index(
            "uq_media_pricing_provider_level",
            "provider",
            "media_type",
            unique=True,
            sqlite_where=text("model_id IS NULL"),
            postgresql_where=text("model_id IS NULL"),
        ),
        Index(
            "uq_media_pricing_model_level",
            "provider",
            "model_id",
            "media_type",
            unique=True,
            sqlite_where=text("model_id IS NOT NULL"),
            postgresql_where=text("model_id IS NOT NULL"),
        ),
    )

    # Surrogate PK (C2) — see module docstring for why the composite PK left.
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Provider name as it appears in external_api_calls.provider
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    # Model id (null = provider-level default row). Matches metadata_json.model
    # after alias normalization (see app.gateway._shared.media_constants).
    model_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # Media type: image / video / tts (aligned with METHOD_TO_MEDIA_TYPE values)
    media_type: Mapped[str] = mapped_column(String(16), nullable=False)
    # Billing unit: per_image / per_kilo_token / per_second / per_kilo_char
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    unit_price: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    currency: Mapped[str] = mapped_column(String(8), nullable=False, default="CNY")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False, default="")
