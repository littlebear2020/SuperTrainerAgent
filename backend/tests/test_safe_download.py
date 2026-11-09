"""safe_download（C2，2026-10-05 审计 §2-1/§4-8）：gateway 出站下载统一守卫。

核心语义：
- 私网/回环/链路本地/保留段目标拒绝（URL 字面量与 DNS 解析结果都查）；
- **钉 IP 连接**：解析→校验→连向校验过的 IP（保留 Host 头与 TLS SNI），
  消除解析/连接两次解析的 DNS-rebinding TOCTOU 窗口；
- 30x 每跳同规则复核；
- 体积上限（Content-Length 预检 + 流式累计双保险）。
"""

from unittest.mock import patch

import httpx
import pytest

from app.gateway._shared.safe_download import UnsafeDownloadError, safe_download


def _ga(*ips):
    def fake(host, port, **kw):
        return [(None, None, None, "", (ip, port)) for ip in ips]

    return fake


def _transport(handler):
    return httpx.MockTransport(handler)


class TestTargetValidation:
    @pytest.mark.asyncio
    async def test_private_literal_rejected_pre_connect(self):
        for url in ("http://127.0.0.1/x", "http://10.0.0.1/x", "http://169.254.169.254/meta", "http://[::1]/x", "http://[fc00::1]/x"):

            def handler(request):
                raise AssertionError(f"must not connect to {url}")

            with pytest.raises(UnsafeDownloadError):
                await safe_download(url, transport=_transport(handler))

    @pytest.mark.asyncio
    async def test_non_http_scheme_rejected(self):
        with pytest.raises(UnsafeDownloadError):
            await safe_download("file:///etc/passwd", transport=_transport(lambda r: None))

    @pytest.mark.asyncio
    async def test_dns_to_private_rejected(self):
        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("10.2.3.4")):
            with pytest.raises(UnsafeDownloadError):
                await safe_download("https://internal.example.com/x", transport=_transport(lambda r: None))

    @pytest.mark.asyncio
    async def test_dns_mixed_records_rejected(self):
        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("8.8.8.8", "192.168.1.9")):
            with pytest.raises(UnsafeDownloadError):
                await safe_download("https://mixed.example.com/x", transport=_transport(lambda r: None))


class TestPinnedConnection:
    @pytest.mark.asyncio
    async def test_connects_to_pinned_ip_with_host_header_and_sni(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["url_host"] = request.url.host
            captured["host_header"] = request.headers.get("host")
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, content=b"payload")

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            body = await safe_download("https://cdn.example.com/a.png", transport=_transport(handler))

        assert body == b"payload"
        assert captured["url_host"] == "93.184.216.34", "TCP 目标必须是钉住的公网 IP"
        assert captured["host_header"] == "cdn.example.com", "Host 头保留原域名"
        assert captured["sni"] == "cdn.example.com", "TLS SNI 保留原域名（证书校验同名）"

    @pytest.mark.asyncio
    async def test_http_no_sni_needed(self):
        captured = {}

        def handler(request):
            captured["sni"] = request.extensions.get("sni_hostname")
            return httpx.Response(200, content=b"ok")

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            await safe_download("http://cdn.example.com/a.png", transport=_transport(handler))
        assert captured["sni"] is None


class TestRedirects:
    @pytest.mark.asyncio
    async def test_redirect_to_private_rejected(self):
        def handler(request):
            if request.url.host in ("93.184.216.34", "first.example.com") or request.headers.get("host") == "first.example.com":
                return httpx.Response(302, headers={"Location": "http://127.0.0.1:8001/steal"})
            raise AssertionError("unexpected hop")

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            with pytest.raises(UnsafeDownloadError):
                await safe_download("https://first.example.com/a", transport=_transport(handler))

    @pytest.mark.asyncio
    async def test_redirect_chain_public_ok(self):
        hops = []

        def handler(request):
            host = request.headers.get("host")
            hops.append(host)
            if host == "first.example.com":
                return httpx.Response(302, headers={"Location": "https://second.example.com/b"})
            return httpx.Response(200, content=b"final")

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            body = await safe_download("https://first.example.com/a", transport=_transport(handler))
        assert body == b"final"
        assert hops == ["first.example.com", "second.example.com"]

    @pytest.mark.asyncio
    async def test_redirect_loop_capped(self):
        def handler(request):
            return httpx.Response(302, headers={"Location": request.headers.get("host") + "/next"})

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            with pytest.raises(UnsafeDownloadError, match="redirect"):
                await safe_download("https://loopy.example.com/a", transport=_transport(handler), max_redirects=3)


class TestCgnatRange:
    @pytest.mark.asyncio
    async def test_cgnat_literals_rejected(self):
        """评审修复轮（Minor #5 升级）：100.64/10（CGNAT/Tailscale）不可公网路由。"""
        for url in ("http://100.64.0.1/x", "http://100.127.255.254/x"):
            with pytest.raises(UnsafeDownloadError):
                await safe_download(url, transport=_transport(lambda r: None))


class TestSizeCap:
    @pytest.mark.asyncio
    async def test_streamed_oversize_without_content_length(self):
        def handler(request):
            return httpx.Response(200, content=b"x" * 4096)

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            with pytest.raises(UnsafeDownloadError, match="size"):
                await safe_download("https://sneaky.example.com/x", max_bytes=1024, transport=_transport(handler))

    @pytest.mark.asyncio
    async def test_within_limit_passes(self):
        def handler(request):
            return httpx.Response(200, content=b"x" * 512)

        with patch("app.gateway._shared.safe_download.socket.getaddrinfo", _ga("93.184.216.34")):
            body = await safe_download("https://ok.example.com/x", max_bytes=1024, transport=_transport(handler))
        assert len(body) == 512
