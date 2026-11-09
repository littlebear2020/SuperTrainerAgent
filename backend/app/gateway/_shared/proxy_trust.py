"""Trusted-proxy allowlist shared by auth rate limiting and CSRF scheme detection.

SEC-M4 (requirements-v2 review plan 2026-09-09, batch 1 R2): proxy-forwarded
headers (``Forwarded`` / ``X-Forwarded-Proto`` / ``X-Real-IP``) are only
meaningful when the TCP peer is a proxy we operate. ``AUTH_TRUSTED_PROXIES``
(comma-separated CIDR or single IPs) is the single source of truth — the same
trust surface already accepted by the M10 decision (all compose files set
172.0.0.0/8, nginx sets ``X-Forwarded-Proto $scheme`` along the chain).

Extracted from ``routers/auth.py`` so middleware can consult it without
importing a router module (circular-import risk).
"""

from __future__ import annotations

import logging
import os
from ipaddress import ip_address, ip_network

logger = logging.getLogger(__name__)


def trusted_proxies() -> list:
    """Parse ``AUTH_TRUSTED_PROXIES`` env var into a list of ip_network objects.

    Comma-separated CIDR or single-IP entries. Empty / unset = no proxy is
    trusted (direct mode). Invalid entries are skipped with a logger warning.
    Read live so env-var overrides take effect immediately and tests can
    ``monkeypatch.setenv`` without poking a module-level cache.
    """
    raw = os.getenv("AUTH_TRUSTED_PROXIES", "").strip()
    if not raw:
        return []
    nets = []
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        try:
            nets.append(ip_network(entry, strict=False))
        except ValueError:
            logger.warning("AUTH_TRUSTED_PROXIES: ignoring invalid entry %r", entry)
    return nets


def is_trusted_proxy(peer: str | None) -> bool:
    """True when the TCP peer address belongs to ``AUTH_TRUSTED_PROXIES``.

    Non-IP peers (e.g. the ASGI test client's ``"testclient"``) are never
    trusted — they cannot be in any network.
    """
    if not peer:
        return False
    try:
        peer_ip = ip_address(peer)
    except ValueError:
        # peer wasn't a parseable IP (e.g. "unknown") — never trusted
        return False
    return any(peer_ip in net for net in trusted_proxies())


def is_valid_ip(value: str) -> bool:
    """True 当 value 是合法 IP 字面量（v4/v6）。

    条目 32（2026-09-12 缺陷批）：X-Real-IP 值的格式卫生校验——可信网段内容器
    伪造的垃圾串头不得进限流 key 命名空间。主风险（网段内**合法** IP 轮换绕
    per-IP 锁）防不住格式校验，靠条目 8 网段收窄 + 既有 per-account 锁缓解；
    本函数只做卫生加固。
    """
    try:
        ip_address(value)
    except ValueError:
        return False
    return True
