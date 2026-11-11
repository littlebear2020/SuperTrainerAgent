"""出站下载守卫（C2，2026-10-05 审计 §4-8/§4-9）。

worker 侧统一防线：
- 私网/回环/链路本地/保留段 IP 拒绝（URL 字面量与 DNS 解析结果都查——防
  rebinding 到内网与 metadata 端点）；全部 DNS 为 198.18/15 且对应 scheme
  存在回环 HTTP(S) 代理时，保留原域名并由 urllib 经该代理访问；
- 可选域名白名单（REMOTION_ASSET_ALLOWED_DOMAINS，逗号分隔；缺省 = 放行
  任意公网主机——CDN 域名易变，宁宽勿断，见整改计划 D1-D4 裁决口径）；
- 下载体积上限（REMOTION_ASSET_MAX_BYTES，默认 512MiB）。
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.request
from urllib.parse import urlparse

_DEFAULT_MAX_ASSET_BYTES = 512 * 1024 * 1024
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


def _loopback_proxy(scheme: str) -> str | None:
    proxy = urllib.request.getproxies().get(scheme)
    if not proxy:
        return None
    try:
        parsed = urlparse(proxy)
        ip = ipaddress.ip_address(parsed.hostname or "")
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not ip.is_loopback:
        return None
    return proxy


def _all_fake_ip_records(resolved: set[str]) -> bool:
    return bool(resolved) and all(ipaddress.ip_address(raw) in _FAKE_IP_NETWORK for raw in resolved)


def allowed_domains() -> set[str]:
    raw = os.environ.get("REMOTION_ASSET_ALLOWED_DOMAINS", "")
    return {d.strip().lower() for d in raw.split(",") if d.strip()}


def max_asset_bytes() -> int:
    return int(os.environ.get("REMOTION_ASSET_MAX_BYTES", str(_DEFAULT_MAX_ASSET_BYTES)))


def _assert_public_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, label: str) -> None:
    # 评审修复轮（2026-10-05）：补 not ip.is_global——覆盖 100.64/10（CGNAT/
    # Tailscale）等 is_private=False 但不可公网路由的段（正/反两口径双保险）。
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified or not ip.is_global:
        raise ValueError(f"blocked non-public download target: {label}")


def assert_public_http_url(url: str) -> None:
    """Reject URLs whose host is (or resolves to) a non-public address.

    Raises ValueError with a human-readable reason; returns None when the
    target passes. Blocking happens **before** any connection attempt.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked non-http download scheme: {parsed.scheme or '(none)'}")
    host = (parsed.hostname or "").lower()
    if not host:
        raise ValueError("blocked download URL without host")

    domains = allowed_domains()
    if domains and host not in domains:
        raise ValueError(f"blocked host not in REMOTION_ASSET_ALLOWED_DOMAINS: {host}")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        _assert_public_ip(ip, host)
        return

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    resolved = {info[4][0] for info in infos}
    if not resolved:
        raise ValueError(f"blocked download host with no DNS records: {host}")
    if _all_fake_ip_records(resolved) and _loopback_proxy(parsed.scheme):
        if urllib.request.proxy_bypass(host):
            raise ValueError(f"blocked fake-IP download target with proxy bypass: {host}")
        return
    for raw in resolved:
        _assert_public_ip(ipaddress.ip_address(raw), f"{host} -> {raw}")


class GuardedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """30x 每跳复检守卫（评审修复轮 Important #1）。

    修前 urllib 默认跟随重定向不复查——hop-0 公网校验通过后可被 302 引向
    私网/metadata。本 handler 在每次构造重定向请求前对新 URL 全量复检。
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        assert_public_http_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def build_guarded_opener() -> urllib.request.OpenerDirector:
    """出站下载专用 opener：重定向每跳过守卫。"""
    return urllib.request.build_opener(GuardedRedirectHandler)
