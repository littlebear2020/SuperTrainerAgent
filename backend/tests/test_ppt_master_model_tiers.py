"""PPT Master model_tiers 测试（恢复重写）。"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MOD_PATH = REPO_ROOT / "skills/public/ppt-master/scripts/model_tiers.py"


@pytest.fixture()
def mod():
    spec = importlib.util.spec_from_file_location("model_tiers", MOD_PATH)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _model(name, thinking=False, vision=False, inp=None, outp=None):
    return {
        "name": name,
        "supports_thinking": thinking,
        "supports_vision": vision,
        "input_price": inp,
        "output_price": outp,
    }


def test_resolve_top_uses_strongest(mod):
    models = [
        _model("weak", inp=1.0, outp=2.0),
        _model("strong", thinking=True, vision=True, inp=5.0, outp=15.0),
    ]
    strategist, executor = mod.resolve(models, "top")
    assert executor == "strong"
    assert strategist == "strong"


def test_resolve_draft_uses_cheapest(mod):
    models = [
        _model("pricey", thinking=True, vision=True, inp=5.0, outp=15.0),
        _model("cheap", inp=0.5, outp=1.0),
    ]
    _, executor = mod.resolve(models, "draft")
    assert executor == "cheap"


def test_resolve_single_model_fills_both_roles(mod):
    assert mod.resolve([_model("only")], "top") == ("only", "only")


def test_resolve_empty_raises(mod):
    with pytest.raises((RuntimeError, ValueError)):
        mod.resolve([], "top")


def test_resolve_invalid_tier_raises(mod):
    with pytest.raises((RuntimeError, ValueError)):
        mod.resolve([_model("m")], "no-such-tier")


def test_estimate_cost_with_pricing(mod):
    cost = mod.estimate_cost(_model("m", inp=2.0, outp=6.0), 1_000_000, 500_000)
    assert cost == pytest.approx(2.0 * 1 + 6.0 * 0.5)
