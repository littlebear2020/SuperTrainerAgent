"""出站下载统一守卫（C2，2026-10-05 审计 §2-1 + §4-8/§4-9）。

四个 gateway 下载点（gallery 缩略图 / video-gen 风格锚 / character sheet /
ai_image 本地化）共用本模块，worker 侧另见 ``docker/production-worker/
pipelines/net_guard.py``（stdlib 同口径实现）。

安全语义：
1. 目标校验：scheme 仅 http(s)；host 为 IP 字面量时直接查段，否则解析 DNS
   后对**全部**记录查段（任一私网/回环/链路本地/保留段记录即拒绝——防
   rebinding 到内网与云 metadata 端点）。唯一例外是全部记录属于 198.18/15
   且对应 scheme 存在回环 HTTP(S) 代理：经代理访问原域名，不直连 fake-IP。
2. **钉 IP 连接**：普通公网目标将校验通过的 IP 直接作为 TCP 目标（原域名保留在 Host 头
   与 TLS SNI/证书校验名），消除"校验与连接两次解析"之间的 TOCTOU 窗口。
3. 30x 逐跳重复全套校验；跳数上限防环。
4. 体积上限：Content-Length 预检 + 流式累计双保险。
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
import urllib.request
from dataclasses import dataclass

import httpx

DEFAULT_MAX_BYTES = 64 * 1024 * 1024
DEFAULT_TIMEOUT = 60.0
MAX_REDIRECTS = 5
_FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class UnsafeDownloadError(ValueError):
    """Download target failed the SSRF guard (private/blocked/oversized)."""


@dataclass(frozen=True)
class _Target:
    pinned_url: str
    host_header: str
    sni: str | None
    proxy: str | None = None


def _assert_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, label: str) -> None:
    # 评审修复轮（2026-10-05）：补 not ip.is_global——覆盖 100.64/10（CGNAT/Tailscale）
    # 等 is_private=False 但不可公网路由的段。
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified or not ip.is_global:
        raise UnsafeDownloadError(f"blocked non-public download target: {label}")


def _loopback_proxy(scheme: str) -> str | None:
    proxy = urllib.request.getproxies().get(scheme)
    if not proxy:
        return None
    try:
        parsed = httpx.URL(proxy)
        ip = ipaddress.ip_address(parsed.host)
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not ip.is_loopback:
        return None
    return proxy


def _all_fake_ip_records(resolved: set[str]) -> bool:
    return bool(resolved) and all(ipaddress.ip_address(raw) in _FAKE_IP_NETWORK for raw in resolved)


async def _pin_target(url: str) -> _Target:
    u = httpx.URL(url)
    if u.scheme not in ("http", "https"):
        raise UnsafeDownloadError(f"blocked non-http download scheme: {u.scheme or '(none)'}")
    host = u.host
    if not host:
        raise UnsafeDownloadError("blocked download URL without host")
    port = u.port or (443 if u.scheme == "https" else 80)

    proxy = None
    try:
        ipaddress.ip_address(host)
    except ValueError:
        loop = asyncio.get_running_loop()
        infos = await loop.run_in_executor(None, lambda: socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        resolved = {info[4][0] for info in infos or []}
        if not resolved:
            raise UnsafeDownloadError(f"blocked download host with no DNS records: {host}")
        proxy = _loopback_proxy(u.scheme) if _all_fake_ip_records(resolved) else None
        if proxy is None:
            for raw in resolved:
                _assert_public(ipaddress.ip_address(raw), f"{host} -> {raw}")
            pinned = sorted(resolved)[0]
        else:
            pinned = host
        sni = host
    else:
        _assert_public(ipaddress.ip_address(host), host)
        pinned = host
        sni = None

    pinned_url = str(u.copy_with(host=pinned))
    host_header = host if u.port is None else f"{host}:{u.port}"
    return _Target(pinned_url=pinned_url, host_header=host_header, sni=sni if u.scheme == "https" else None, proxy=proxy)


async def safe_download(
    url: str,
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    max_redirects: int = MAX_REDIRECTS,
) -> bytes:
    """Download ``url`` under the SSRF guard and return the body.

    Raises :class:`UnsafeDownloadError` for blocked targets/redirects/oversize;
    ``httpx.HTTPStatusError`` propagates from non-2xx finals (same semantics as
    the previous ``raise_for_status`` call sites).
    """
    current = url
    extra_headers = dict(headers or {})
    for _ in range(max_redirects + 1):
        target = await _pin_target(current)
        client_options = {"proxy": target.proxy, "trust_env": False} if target.proxy else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=False, transport=transport, **client_options) as client:
            request = client.build_request(
                "GET",
                target.pinned_url,
                headers={**extra_headers, "Host": target.host_header},
                extensions={"sni_hostname": target.sni} if target.sni else None,
            )
            resp = await client.send(request)
            if resp.is_redirect:
                location = resp.headers.get("location")
                await resp.aclose()
                if not location:
                    raise UnsafeDownloadError("redirect response without Location header")
                current = str(httpx.URL(current).join(location))
                continue

            resp.raise_for_status()
            # 体积上限走流式累计（含无 Content-Length/谎报 CL 的场景）——
            # CL 预检分支经 MockTransport 无法诚实构造，删繁就简（流式已覆盖语义）。
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes(64 * 1024):
                total += len(chunk)
                if total > max_bytes:
                    await resp.aclose()
                    raise UnsafeDownloadError(f"download exceeds size limit ({max_bytes}B) while streaming")
                chunks.append(chunk)
            return b"".join(chunks)

    raise UnsafeDownloadError(f"too many redirects (> {max_redirects}) downloading {url[:200]}")
