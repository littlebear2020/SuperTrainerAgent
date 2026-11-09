"""Unit tests for the trusted-proxy allowlist (``app.gateway._shared.proxy_trust``).

This module decides whether proxy-forwarded headers (``Forwarded`` /
``X-Forwarded-Proto`` / ``X-Real-IP``) may influence rate-limit identity and
CSRF scheme detection, so its failure modes are security-relevant and pinned
here:

- unset/empty ``AUTH_TRUSTED_PROXIES`` means *no proxy is trusted*
  (default-deny);
- malformed allowlist entries are dropped with a warning — a parse failure
  must never widen trust;
- unparseable TCP peers (e.g. the ASGI test client's ``"testclient"``) are
  never trusted;
- ``is_valid_ip`` accepts only bare IP literals — CIDRs and padded strings
  must not leak into the rate-limit key namespace.
"""

from __future__ import annotations

import pytest

from app.gateway._shared.proxy_trust import is_trusted_proxy, is_valid_ip, trusted_proxies

ENV_VAR = "AUTH_TRUSTED_PROXIES"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch):
    """Guarantee a hermetic baseline even if the host exports the real var."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    return monkeypatch


class TestTrustedProxiesParsing:
    def test_unset_env_returns_empty_list(self) -> None:
        assert trusted_proxies() == []

    def test_blank_env_returns_empty_list(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "   ")
        assert trusted_proxies() == []

    def test_single_cidr(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "10.0.0.0/8")
        nets = trusted_proxies()
        assert [str(n) for n in nets] == ["10.0.0.0/8"]

    def test_bare_ipv4_becomes_host_route(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "192.168.1.10")
        assert [str(n) for n in trusted_proxies()] == ["192.168.1.10/32"]

    def test_bare_ipv6_and_ipv6_cidr(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "::1, fd00::/8")
        assert [str(n) for n in trusted_proxies()] == ["::1/128", "fd00::/8"]

    def test_comma_separated_entries_tolerate_spaces_and_empty_slots(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, " 10.0.0.0/8 ,, 172.16.0.0/12 ,")
        assert [str(n) for n in trusted_proxies()] == ["10.0.0.0/8", "172.16.0.0/12"]

    def test_invalid_entries_are_skipped_without_widening_trust(self, _clean_env) -> None:
        """Garbage, out-of-range octets and impossible prefix lengths drop out; valid entries survive."""
        _clean_env.setenv(ENV_VAR, "garbage,999.1.1.1,10.0.0.0/33,10.0.0.0/8")
        assert [str(n) for n in trusted_proxies()] == ["10.0.0.0/8"]

    def test_host_bits_set_is_normalized_to_network(self, _clean_env) -> None:
        """``strict=False`` pins normalization: 10.1.2.3/24 means 10.1.2.0/24, not an error."""
        _clean_env.setenv(ENV_VAR, "10.1.2.3/24")
        assert [str(n) for n in trusted_proxies()] == ["10.1.2.0/24"]

    def test_env_is_read_live_on_every_call(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "10.0.0.0/8")
        assert len(trusted_proxies()) == 1
        _clean_env.delenv(ENV_VAR)
        assert trusted_proxies() == []


class TestIsTrustedProxy:
    def test_unset_allowlist_denies_everything(self) -> None:
        """Default-deny: with no allowlist even a private-range peer is untrusted."""
        assert is_trusted_proxy("10.0.0.1") is False
        assert is_trusted_proxy("172.16.0.1") is False

    def test_none_and_empty_peer_are_never_trusted(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "0.0.0.0/0")
        assert is_trusted_proxy(None) is False
        assert is_trusted_proxy("") is False

    def test_unparseable_peer_is_never_trusted(self, _clean_env) -> None:
        """Even against an allow-all network, a non-IP peer string must not match."""
        _clean_env.setenv(ENV_VAR, "0.0.0.0/0")
        assert is_trusted_proxy("testclient") is False
        assert is_trusted_proxy("unknown") is False

    def test_ipv4_peer_inside_range(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "172.0.0.0/8")
        assert is_trusted_proxy("172.16.0.1") is True
        assert is_trusted_proxy("172.255.255.254") is True

    def test_ipv4_peer_outside_range(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "172.0.0.0/8")
        assert is_trusted_proxy("173.0.0.1") is False
        assert is_trusted_proxy("8.8.8.8") is False

    def test_boundary_ips_first_and_last_in_range(self, _clean_env) -> None:
        """/30 pins both edges: network and broadcast addresses are members, .4 is not."""
        _clean_env.setenv(ENV_VAR, "10.0.0.0/30")
        assert is_trusted_proxy("10.0.0.0") is True
        assert is_trusted_proxy("10.0.0.3") is True
        assert is_trusted_proxy("10.0.0.4") is False

    def test_single_ip_allowlist_matches_only_that_host(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "192.168.1.10")
        assert is_trusted_proxy("192.168.1.10") is True
        assert is_trusted_proxy("192.168.1.11") is False

    def test_ipv6_range_boundaries(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "2001:db8::/32")
        assert is_trusted_proxy("2001:db8::1") is True
        assert is_trusted_proxy("2001:db9::1") is False

    def test_cross_address_family_never_matches(self, _clean_env) -> None:
        """An IPv4 peer must not match an IPv6-only allowlist and vice versa."""
        _clean_env.setenv(ENV_VAR, "2001:db8::/32")
        assert is_trusted_proxy("10.0.0.1") is False
        _clean_env.setenv(ENV_VAR, "10.0.0.0/8")
        assert is_trusted_proxy("::1") is False

    def test_malformed_allowlist_entry_does_not_widen_trust(self, _clean_env) -> None:
        _clean_env.setenv(ENV_VAR, "not-an-ip")
        assert is_trusted_proxy("10.0.0.1") is False


class TestIsValidIp:
    @pytest.mark.parametrize("value", ["1.2.3.4", "0.0.0.0", "255.255.255.255", "::1", "2001:db8::1", "fe80::a634:d9ff:fe5b:81e5"])
    def test_valid_literals(self, value: str) -> None:
        assert is_valid_ip(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "not-an-ip",
            "",
            "1.2.3.256",
            "10.0.0.0/8",  # CIDR notation is a network, not a literal — must be rejected
            " 1.2.3.4",  # surrounding whitespace is not hygienic
            "1.2.3.4 ",
            "testclient",
        ],
    )
    def test_rejects_non_literals(self, value: str) -> None:
        assert is_valid_ip(value) is False
