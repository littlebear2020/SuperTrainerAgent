"""PPT Master model-tier resolver.

Maps the 4 model tiers (auto / top / balanced / draft) to concrete
(strategist_model, executor_model) picks, driven entirely by /api/models
metadata (capability flags + pricing). No hardcoded model ids — "strongest
long-context" is inferred from supports_thinking / supports_vision (proxy for
capability) and price (proxy for tier). Executor needs strong long-context +
code/SVG (+vision for visual-review); Strategist can use a lighter model.
"""

from __future__ import annotations

from typing import Literal

Tier = Literal["auto", "top", "balanced", "draft"]
TIERS: tuple[Tier, ...] = ("auto", "top", "balanced", "draft")


def _capability(model: dict) -> int:
    """Higher = more capable. thinking + vision are the available capability
    signals in /api/models metadata."""
    return int(bool(model.get("supports_thinking"))) + int(bool(model.get("supports_vision")))


def _price(model: dict) -> float:
    """Total per-1M-token price (input + output); unknown -> inf (treated as pricey)."""
    inp = model.get("input_price")
    outp = model.get("output_price")
    if inp is None or outp is None:
        return float("inf")
    return float(inp) + float(outp)


def _by_strength(models: list[dict]) -> list[dict]:
    """Sort by capability desc, then price asc — strongest first."""
    return sorted(models, key=lambda m: (-_capability(m), _price(m)))


def resolve(models: list[dict], tier: Tier) -> tuple[str, str]:
    """Return (strategist_model_name, executor_model_name) for the tier.

    Requires at least one model. Falls back to the single available model for
    both roles when only one is configured.
    """
    if not models:
        raise ValueError("resolve() requires at least one model")
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r}; expected one of {TIERS}")
    if len(models) == 1:
        name = models[0]["name"]
        return name, name

    ranked = _by_strength(models)
    effective = "top" if tier == "auto" else tier
    if effective == "top":
        pick = ranked[0]["name"]
        return pick, pick
    if effective == "balanced":
        executor = ranked[0]["name"]
        strategist = ranked[min(1, len(ranked) - 1)]["name"]
        return strategist, executor
    # draft: cheapest for both roles
    cheap = min(models, key=_price)["name"]
    return cheap, cheap


def estimate_cost(model: dict, input_tokens: int, output_tokens: int) -> float:
    """USD cost estimate from per-1M-token pricing; unknown prices -> 0.0."""
    inp = float(model.get("input_price") or 0.0)
    outp = float(model.get("output_price") or 0.0)
    return inp * input_tokens / 1_000_000 + outp * output_tokens / 1_000_000
