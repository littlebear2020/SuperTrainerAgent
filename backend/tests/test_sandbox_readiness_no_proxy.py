"""P2-10: sandbox readiness probe must not honor HTTP(S)_PROXY env vars.

The sandbox is reached on an internal/local address. ``requests`` reads proxy
env vars by default, so a deployment that sets HTTPS_PROXY (common in 国内离线
builds, where an egress proxy is plonked in front of everything) would route the
readiness probe to that proxy and time out — the sandbox looks "never ready"
even though it is up. The probe uses a ``trust_env=False`` session to bypass all
env-derived config (proxies, .netrc, auth helpers).
"""

from __future__ import annotations

import requests

from deerflow.community.aio_sandbox.backend import wait_for_sandbox_ready


class _FakeOkResponse:
    status_code = 200


def test_readiness_probe_ignores_proxy_env(monkeypatch):
    """With proxy env vars set, the probe session must still bypass them."""
    # A proxy that would blackhole any real connection if honored.
    for var in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, "http://evil-proxy.invalid:9999")

    captured: dict = {}

    class _ProbingSession(requests.Session):
        def get(self, url, **kwargs):
            # trust_env is what makes requests consult the proxy env vars above;
            # the probe must have flipped it to False before the first request.
            captured["trust_env"] = self.trust_env
            captured["url"] = url
            return _FakeOkResponse()

    monkeypatch.setattr(requests, "Session", _ProbingSession)

    assert wait_for_sandbox_ready("http://sandbox.internal:30001", timeout=2) is True
    assert captured.get("trust_env") is False, "readiness probe must bypass env proxy config"
    assert captured.get("url") == "http://sandbox.internal:30001/v1/sandbox"
