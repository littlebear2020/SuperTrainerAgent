"""Regression tests for provisioner kubeconfig path handling."""

from __future__ import annotations

import pytest


def test_wait_for_kubeconfig_rejects_directory(tmp_path, provisioner_module):
    """Directory mount at kubeconfig path should fail fast with clear error."""
    kubeconfig_dir = tmp_path / "config_dir"
    kubeconfig_dir.mkdir()

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_dir)

    with pytest.raises(RuntimeError, match="directory"):
        provisioner_module._wait_for_kubeconfig(timeout=1)


def test_wait_for_kubeconfig_accepts_file(tmp_path, provisioner_module):
    """Regular file mount should pass readiness wait."""
    kubeconfig_file = tmp_path / "config"
    kubeconfig_file.write_text("apiVersion: v1\n")

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_file)

    # Should return immediately without raising.
    provisioner_module._wait_for_kubeconfig(timeout=1)


def test_init_k8s_client_rejects_directory_path(tmp_path, provisioner_module):
    """KUBECONFIG_PATH that resolves to a directory should be rejected."""
    kubeconfig_dir = tmp_path / "config_dir"
    kubeconfig_dir.mkdir()

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_dir)

    with pytest.raises(RuntimeError, match="expected a file"):
        provisioner_module._init_k8s_client()


def test_init_k8s_client_uses_file_kubeconfig(tmp_path, monkeypatch, provisioner_module):
    """When file exists, provisioner should load kubeconfig file path."""
    kubeconfig_file = tmp_path / "config"
    kubeconfig_file.write_text("apiVersion: v1\n")

    called: dict[str, object] = {}

    def fake_load_kube_config(config_file: str):
        called["config_file"] = config_file

    monkeypatch.setattr(
        provisioner_module.k8s_config,
        "load_kube_config",
        fake_load_kube_config,
    )
    monkeypatch.setattr(
        provisioner_module.k8s_client,
        "CoreV1Api",
        lambda *args, **kwargs: "core-v1",
    )

    provisioner_module.KUBECONFIG_PATH = str(kubeconfig_file)

    result = provisioner_module._init_k8s_client()

    assert called["config_file"] == str(kubeconfig_file)
    assert result == "core-v1"


def test_init_k8s_client_falls_back_to_incluster_when_missing(tmp_path, monkeypatch, provisioner_module):
    """When kubeconfig file is missing, in-cluster config should be attempted."""
    missing_path = tmp_path / "missing-config"

    calls: dict[str, int] = {"incluster": 0}

    def fake_load_incluster_config():
        calls["incluster"] += 1

    monkeypatch.setattr(
        provisioner_module.k8s_config,
        "load_incluster_config",
        fake_load_incluster_config,
    )
    monkeypatch.setattr(
        provisioner_module.k8s_client,
        "CoreV1Api",
        lambda *args, **kwargs: "core-v1",
    )

    provisioner_module.KUBECONFIG_PATH = str(missing_path)

    result = provisioner_module._init_k8s_client()

    assert calls["incluster"] == 1
    assert result == "core-v1"
