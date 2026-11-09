"""Write-time validation for model pricing (B2).

Pricing was previously an undeclared extra field on ``ModelConfig``
(``ConfigDict(extra="allow")``) with **zero** validation, so every way of
getting it wrong failed silently and produced a number that looked right.
This module pins the four rejections. Each test names the silent failure it
prevents, because "422 on bad input" is not the point — "no plausible-looking
wrong number" is.

The three silent modes actually present in ``_build_pricing_map`` before this
change:

1. ``raw.get("input_per_million") or 0`` — a typo'd key (``input_per_milion``)
   reads as 0.
2. The skip condition is ``input_price <= 0 **and** output_price <= 0``, so an
   entry with a typo'd *input* key but a valid output price **survives** with
   an input price of 0. That is not "unpriced" (which shows as ``—``), it is
   *under-billed while looking priced* — the worst of the three.
3. A currency mismatch makes the whole function ``return {}``, zeroing spend
   **platform-wide**, not just for the offending model.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.gateway.pricing_schema import ModelPricingInput, validate_pricing_payload


class TestFieldNameWhitelist:
    def test_accepts_the_documented_fields(self) -> None:
        entry = ModelPricingInput(
            currency="CNY",
            input_per_million=8.0,
            output_per_million=32.0,
            input_cache_hit_per_million=0.8,
        )
        assert entry.input_per_million == 8.0

    def test_rejects_a_misspelled_price_key(self) -> None:
        """``input_per_milion`` used to read as 0 and under-bill silently."""
        with pytest.raises(ValidationError) as exc:
            ModelPricingInput(
                currency="CNY",
                input_per_milion=8.0,  # typo: one "l"
                output_per_million=32.0,
            )
        assert "input_per_milion" in str(exc.value)

    def test_a_typo_does_not_survive_as_a_zero_priced_entry(self) -> None:
        """The nastiest mode: typo'd input + valid output = silent under-billing.

        The old skip condition (``input <= 0 and output <= 0``) let this
        through, so the model appeared priced while its input tokens were
        billed at zero.
        """
        with pytest.raises(ValidationError):
            ModelPricingInput(input_per_milion=8.0, output_per_million=32.0, currency="CNY")

    def test_rejects_unknown_keys_generally(self) -> None:
        with pytest.raises(ValidationError):
            ModelPricingInput(currency="CNY", input_per_million=1.0, output_per_million=1.0, discount=0.5)


class TestNonNegative:
    @pytest.mark.parametrize("field", ["input_per_million", "output_per_million", "input_cache_hit_per_million"])
    def test_rejects_negative_prices(self, field: str) -> None:
        payload = {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0, field: -1.0}
        with pytest.raises(ValidationError):
            ModelPricingInput(**payload)

    def test_zero_is_allowed(self) -> None:
        """0 is a legitimate price (free tier), distinct from "unset"."""
        entry = ModelPricingInput(currency="CNY", input_per_million=0.0, output_per_million=0.0)
        assert entry.input_per_million == 0.0


class TestCacheHitNullSemantics:
    def test_omitting_the_hit_price_yields_none_not_zero(self) -> None:
        """None means "bill hits at the miss price"; 0 means "hits are free"."""
        entry = ModelPricingInput(currency="CNY", input_per_million=8.0, output_per_million=32.0)
        assert entry.input_cache_hit_per_million is None

    def test_explicit_null_is_accepted(self) -> None:
        entry = ModelPricingInput(
            currency="CNY",
            input_per_million=8.0,
            output_per_million=32.0,
            input_cache_hit_per_million=None,
        )
        assert entry.input_cache_hit_per_million is None

    def test_explicit_zero_is_kept_distinct_from_null(self) -> None:
        entry = ModelPricingInput(
            currency="CNY",
            input_per_million=8.0,
            output_per_million=32.0,
            input_cache_hit_per_million=0.0,
        )
        assert entry.input_cache_hit_per_million == 0.0
        assert entry.input_cache_hit_per_million is not None


class TestCurrencyConsistency:
    """One currency per deployment.

    ``_build_pricing_map`` responds to a mixed-currency config by returning an
    **empty map** — every model becomes unpriced and platform-wide spend reads
    as ``—``. One bad edit silently zeroes the whole console, so the write
    path has to refuse it up front.
    """

    def test_rejects_a_payload_whose_entries_disagree(self) -> None:
        payload = {
            "model-a": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0},
            "model-b": {"currency": "USD", "input_per_million": 1.0, "output_per_million": 4.0},
        }
        with pytest.raises(ValueError) as exc:
            validate_pricing_payload(payload, existing_currency=None)
        assert "currency" in str(exc.value).lower()

    def test_rejects_a_payload_that_disagrees_with_rows_already_stored(self) -> None:
        """The conflict can also be against what is already in the table."""
        payload = {"model-b": {"currency": "USD", "input_per_million": 1.0, "output_per_million": 4.0}}
        with pytest.raises(ValueError) as exc:
            validate_pricing_payload(payload, existing_currency="CNY")
        assert "CNY" in str(exc.value)

    def test_accepts_a_consistent_payload(self) -> None:
        payload = {
            "model-a": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0},
            "model-b": {"currency": "CNY", "input_per_million": 1.0, "output_per_million": 4.0},
        }
        validated = validate_pricing_payload(payload, existing_currency="CNY")
        assert set(validated) == {"model-a", "model-b"}

    def test_currency_comparison_ignores_case_and_padding(self) -> None:
        """`` cny `` and ``CNY`` are the same currency, not a conflict."""
        payload = {"model-a": {"currency": " cny ", "input_per_million": 8.0, "output_per_million": 32.0}}
        validated = validate_pricing_payload(payload, existing_currency="CNY")
        assert validated["model-a"].currency == "CNY"

    def test_replacing_every_row_may_change_the_currency(self) -> None:
        """A full switch-over is legitimate; only *mixing* is refused."""
        payload = {
            "model-a": {"currency": "USD", "input_per_million": 1.0, "output_per_million": 4.0},
            "model-b": {"currency": "USD", "input_per_million": 2.0, "output_per_million": 8.0},
        }
        validated = validate_pricing_payload(payload, existing_currency=None)
        assert {e.currency for e in validated.values()} == {"USD"}


class TestAllZeroSkip:
    """An all-zero row (input ≤ 0 and output ≤ 0) is silently dropped from
    the payload so the DB never stores a 0-valued price — which would make
    spend read "¥0" instead of the correct "—" (unpriced).

    Only skip when BOTH sides are ≤ 0; a model with a real price on one
    side and 0 on the other is legitimate (e.g. free output).
    """

    def test_all_zero_row_is_silently_dropped(self) -> None:
        payload = {
            "model-a": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0},
            "model-b": {"currency": "CNY", "input_per_million": 0.0, "output_per_million": 0.0},
        }
        validated = validate_pricing_payload(payload, existing_currency=None)
        assert set(validated) == {"model-a"}

    def test_mixed_payload_skips_zero_keeps_rest(self) -> None:
        payload = {
            "priced": {"currency": "CNY", "input_per_million": 10.0, "output_per_million": 20.0},
            "unpriced": {"currency": "CNY", "input_per_million": 0.0, "output_per_million": 0.0},
            "also-priced": {"currency": "CNY", "input_per_million": 5.0, "output_per_million": 15.0},
        }
        validated = validate_pricing_payload(payload, existing_currency=None)
        assert set(validated) == {"priced", "also-priced"}

    def test_all_zero_payload_returns_empty_dict(self) -> None:
        """Clearing every model is valid — full-table replacement → empty table."""
        payload = {
            "model-a": {"currency": "CNY", "input_per_million": 0.0, "output_per_million": 0.0},
            "model-b": {"currency": "CNY", "input_per_million": 0.0, "output_per_million": 0.0},
        }
        validated = validate_pricing_payload(payload, existing_currency=None)
        assert validated == {}

    def test_one_sided_zero_is_kept(self) -> None:
        """0 output with positive input is legitimate (free output tier)."""
        payload = {
            "model-a": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 0.0},
        }
        validated = validate_pricing_payload(payload, existing_currency=None)
        assert "model-a" in validated
        assert validated["model-a"].output_per_million == 0.0
