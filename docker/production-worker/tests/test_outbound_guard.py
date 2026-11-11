"""C2（2026-10-05 审计 §4-8/§4-9）：worker 出站下载守卫 + still 路径包含断言。

覆盖：
- 私网/回环/链路本地/保留段 IP 字面量与 DNS 解析结果拒绝（SSRF 面）
- 全部 DNS 为 198.18/15 且存在回环 HTTP(S) 代理时的本地 fake-IP 兼容
- REMOTION_ASSET_ALLOWED_DOMAINS 域名白名单（可选，宁宽勿断默认全放公网）
- _download_asset 体积上限
- _render_still 对 source_task_dir/still_output 的 storage 根包含断言
"""

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import compose_remotion as cr  # noqa: E402
from pipelines import net_guard  # noqa: E402


def _ga(*ips):
    """Fake getaddrinfo returning the given address strings."""
    def fake(host, port, **kw):
        return [(None, None, None, "", (ip, port)) for ip in ips]

    return fake


class TestPublicUrlGuard:
    def test_private_literals_rejected(self):
        for url in (
            "http://127.0.0.1/x",
            "http://10.1.2.3/x",
            "http://192.168.1.1/x",
            "http://172.16.0.9/x",
            "http://169.254.169.254/latest/meta-data",
            "http://[::1]/x",
            "http://[fc00::1]/x",
        ):
            try:
                net_guard.assert_public_http_url(url)
            except ValueError:
                continue
            raise AssertionError(f"private literal must be blocked: {url}")

    def test_non_http_scheme_rejected(self):
        for url in ("file:///etc/passwd", "ftp://example.com/x", "gopher://x"):
            try:
                net_guard.assert_public_http_url(url)
            except ValueError:
                continue
            raise AssertionError(f"non-http scheme must be blocked: {url}")

    def test_public_literal_allowed(self):
        net_guard.assert_public_http_url("https://8.8.8.8/x")  # 不 raise

    def test_dns_resolving_to_private_rejected(self):
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("10.0.0.5")):
            try:
                net_guard.assert_public_http_url("https://internal.example.com/x")
            except ValueError as e:
                assert "internal.example.com" in str(e) or "10.0.0.5" in str(e)
            else:
                raise AssertionError("private DNS resolution must be blocked")

    def test_dns_mixed_records_rejected(self):
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("8.8.8.8", "192.168.0.2")):
            try:
                net_guard.assert_public_http_url("https://mixed.example.com/x")
            except ValueError:
                pass
            else:
                raise AssertionError("any private A record must block the host")

    def test_dns_public_allowed(self):
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("8.8.4.4")):
            net_guard.assert_public_http_url("https://cdn.example.com/x")

    def test_fake_ip_dns_requires_loopback_proxy(self):
        with (
            patch("pipelines.net_guard.socket.getaddrinfo", _ga("198.18.0.143")),
            patch("pipelines.net_guard.urllib.request.getproxies", return_value={}),
        ):
            try:
                net_guard.assert_public_http_url("https://cdn.example.com/x")
            except ValueError as e:
                assert "non-public" in str(e)
            else:
                raise AssertionError("fake-IP DNS without a proxy must be blocked")

    def test_fake_ip_dns_with_loopback_proxy_allowed(self):
        with (
            patch("pipelines.net_guard.socket.getaddrinfo", _ga("198.18.0.143", "198.19.255.254")),
            patch("pipelines.net_guard.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7897"}),
        ):
            net_guard.assert_public_http_url("https://cdn.example.com/x")

    def test_fake_ip_proxy_bypass_rejected(self):
        with (
            patch("pipelines.net_guard.socket.getaddrinfo", _ga("198.18.0.143")),
            patch("pipelines.net_guard.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7897"}),
            patch("pipelines.net_guard.urllib.request.proxy_bypass", return_value=True),
        ):
            try:
                net_guard.assert_public_http_url("https://cdn.example.com/x")
            except ValueError as e:
                assert "proxy bypass" in str(e)
            else:
                raise AssertionError("fake-IP must not be reached directly through NO_PROXY bypass")

    def test_fake_ip_mixed_records_and_non_loopback_proxy_rejected(self):
        for records, proxies in (
            (("198.18.0.143", "8.8.8.8"), {"https": "http://127.0.0.1:7897"}),
            (("198.18.0.143",), {"https": "http://192.0.2.10:8080"}),
        ):
            with (
                patch("pipelines.net_guard.socket.getaddrinfo", _ga(*records)),
                patch("pipelines.net_guard.urllib.request.getproxies", return_value=proxies),
            ):
                try:
                    net_guard.assert_public_http_url("https://cdn.example.com/x")
                except ValueError:
                    pass
                else:
                    raise AssertionError("mixed DNS or a non-loopback proxy must be blocked")

    def test_fake_ip_literal_rejected(self):
        with patch("pipelines.net_guard.urllib.request.getproxies", return_value={"https": "http://127.0.0.1:7897"}):
            try:
                net_guard.assert_public_http_url("https://198.18.0.143/x")
            except ValueError:
                pass
            else:
                raise AssertionError("fake-IP literals must remain blocked")

    def test_domain_allowlist_env(self, monkeypatch):
        monkeypatch.setenv("REMOTION_ASSET_ALLOWED_DOMAINS", "cdn.example.com")
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("8.8.4.4")):
            net_guard.assert_public_http_url("https://cdn.example.com/x")  # 在白名单
            try:
                net_guard.assert_public_http_url("https://other.example.com/x")
            except ValueError as e:
                assert "ALLOWED_DOMAINS" in str(e)
            else:
                raise AssertionError("host outside allowlist must be blocked")

    def test_domain_allowlist_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("REMOTION_ASSET_ALLOWED_DOMAINS", raising=False)
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("9.9.9.9")):
            net_guard.assert_public_http_url("https://any.example.com/x")


class _Resp:
    """无限流响应：每次 read 返 1MiB，模拟超限下载。"""

    def __init__(self, status=200):
        self.status = status

    def read(self, size=-1):
        return b"x" * (1 << 20)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class TestDownloadAssetGuard:
    def test_size_cap_enforced(self, tmp_path, monkeypatch):
        monkeypatch.setenv("REMOTION_ASSET_MAX_BYTES", str(2 * 1024 * 1024))  # 2MiB
        with patch.object(cr.opener, "open", return_value=_Resp()):
            try:
                cr._download_asset("https://8.8.4.4/big.bin", tmp_path / "big.bin")
            except cr.RemotionAssetError as e:
                assert "超限" in str(e) or "size" in str(e).lower()
            else:
                raise AssertionError("oversized download must fail")
        assert not (tmp_path / "big.bin").exists()

    def test_private_url_rejected_before_connect(self, tmp_path):
        def _fail(*a, **k):
            raise AssertionError("must not open a connection to a private target")

        with patch.object(cr.opener, "open", _fail):
            try:
                cr._download_asset("http://127.0.0.1:8001/secret", tmp_path / "x")
            except ValueError:
                pass
            else:
                raise AssertionError("private URL must be blocked pre-connect")


class TestRenderStillContainment:
    def _payload(self, still_output="still_preview/preview.png", source=None):
        return {
            "remotion_props": {"scenes": [{"frames": []}]},
            "still_output": still_output,
            **({"source_task_dir": source} if source else {}),
        }

    def test_still_output_escape_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_STORAGE_DIR", str(tmp_path))
        task_dir = tmp_path / "tasks" / "t1"
        task_dir.mkdir(parents=True)
        try:
            cr._render_still(task_dir, self._payload(still_output="../../escape.png"))
        except RuntimeError as e:
            assert "still_output" in str(e) or "escape" in str(e)
        else:
            raise AssertionError("still_output escaping the task dir must be rejected")

    def test_source_task_dir_outside_storage_rejected(self, tmp_path, monkeypatch):
        storage = tmp_path / "storage"
        storage.mkdir()
        monkeypatch.setenv("VIDEO_GEN_STORAGE_DIR", str(storage))
        outside = tmp_path / "outside-storage"  # 与 storage 根同级——在根外
        outside.mkdir()
        task_dir = storage / "tasks" / "t1"
        task_dir.mkdir(parents=True)
        try:
            cr._render_still(task_dir, self._payload(source=str(outside)))
        except RuntimeError as e:
            assert "storage" in str(e).lower() or "source_task_dir" in str(e)
        else:
            raise AssertionError("source_task_dir outside storage root must be rejected")


class TestRedirectHopsGuard:
    """评审修复轮（2026-10-05 Important #1）：30x 每跳复检守卫。

    修前 urllib 默认 HTTPRedirectHandler 跟随重定向不复查——hop-0 公网校验
    通过后可被 302 引向私网/metadata。修法 = GuardedRedirectHandler 每跳过
    assert_public_http_url。
    """

    def test_handler_blocks_private_redirect_location(self):
        import urllib.request

        handler = net_guard.GuardedRedirectHandler()
        req = urllib.request.Request("https://hop0.example.com/a.png")
        try:
            handler.redirect_request(req, None, 302, "Found", {"Location": "http://127.0.0.1/x"}, "http://127.0.0.1/x")
        except ValueError:
            pass  # 私网跳转被拦
        else:
            raise AssertionError("redirect to private location must raise")

    def test_handler_blocks_private_redirect_via_dns(self):
        import urllib.request

        handler = net_guard.GuardedRedirectHandler()
        req = urllib.request.Request("https://hop0.example.com/a.png")
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("10.9.9.9")):
            try:
                handler.redirect_request(req, None, 302, "Found", {}, "https://rebind.example.net/x")
            except ValueError:
                pass
            else:
                raise AssertionError("redirect resolving to private must raise")

    def test_handler_delegates_public_redirect(self):
        import urllib.request

        handler = net_guard.GuardedRedirectHandler()
        req = urllib.request.Request("https://hop0.example.com/a.png")
        with patch("pipelines.net_guard.socket.getaddrinfo", _ga("8.8.4.4")):
            new_req = handler.redirect_request(req, None, 302, "Found", {}, "https://cdn2.example.com/b.png")
        assert new_req is not None and "cdn2.example.com" in new_req.full_url

    def test_compose_remotion_uses_guarded_opener(self):
        """compose_remotion 的下载器必须经守卫 opener（防回归直连 urlopen）。"""
        assert any(
            getattr(h, "__class__", None).__name__ == "GuardedRedirectHandler" or isinstance(h, net_guard.GuardedRedirectHandler)
            for h in cr.opener.handlers
        ), "_download_asset must go through the guarded opener"


class TestMixedDownloaderGuard:
    """评审修复轮（Important #2）：第三个下载器 video_gen_mixed 此前漏接守卫。"""

    def test_mixed_downloader_rejects_private_url(self, tmp_path):
        from pipelines import video_gen_mixed as vm

        def _fail(*a, **k):
            raise AssertionError("must not open a connection to a private target")

        with patch.object(vm.opener, "open", _fail):
            try:
                vm._download_asset("http://169.254.169.254/meta", tmp_path / "x")
            except ValueError:
                pass
            else:
                raise AssertionError("private URL must be blocked pre-connect in mixed pipeline")


class TestCgnatRangeBlocked:
    """评审修复轮（Minor #5 升级处置）：100.64/10（CGNAT/Tailscale）段拒绝。"""

    def test_cgnat_literal_rejected(self):
        for url in ("http://100.64.0.1/x", "http://100.127.255.254/x"):
            try:
                net_guard.assert_public_http_url(url)
            except ValueError:
                continue
            raise AssertionError(f"CGNAT range must be blocked: {url}")
