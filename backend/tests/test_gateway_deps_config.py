"""Tests for deps.get_config hot-reload behavior (R-07, 2026-08-13)."""

from __future__ import annotations

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.config.sandbox_config import SandboxConfig


def _make_app():
    app = FastAPI()

    @app.get("/probe")
    def probe(cfg: AppConfig = Depends(get_config)):
        return {"log_level": cfg.log_level}

    return app


def test_get_config_routes_through_get_app_config(monkeypatch):
    """The dependency must read the hot-reloading get_app_config(), not the
    app.state startup snapshot (R-07)."""
    config = AppConfig(sandbox=SandboxConfig(use="test"), log_level="info")
    monkeypatch.setattr("deerflow.config.get_app_config", lambda: config)

    app = _make_app()
    app.state.config = config.model_copy(update={"log_level": "stale-snapshot"})

    client = TestClient(app)
    response = client.get("/probe")

    assert response.status_code == 200
    assert response.json() == {"log_level": "info"}


def test_get_config_sees_reloaded_values_without_restart(monkeypatch):
    """A fresh get_app_config() result (config.yaml edit + reload) must be
    visible on the next request without swapping app.state or restarting."""
    current = {"cfg": AppConfig(sandbox=SandboxConfig(use="test"), log_level="info")}

    def _fresh():
        return current["cfg"]

    monkeypatch.setattr("deerflow.config.get_app_config", _fresh)

    app = _make_app()
    app.state.config = current["cfg"]
    client = TestClient(app)

    assert client.get("/probe").json()["log_level"] == "info"

    # Simulate a config.yaml edit + reload: get_app_config now returns a new object.
    current["cfg"] = current["cfg"].model_copy(update={"log_level": "debug"})
    assert client.get("/probe").json()["log_level"] == "debug"


def test_get_config_returns_503_when_config_unavailable(monkeypatch):
    """A config load failure at request time must surface as 503, not a crash."""

    def _raise():
        raise ValueError("config.yaml unreadable")

    monkeypatch.setattr("deerflow.config.get_app_config", _raise)

    app = _make_app()
    client = TestClient(app)
    response = client.get("/probe")

    assert response.status_code == 503
    assert "Configuration not available" in response.json()["detail"]
