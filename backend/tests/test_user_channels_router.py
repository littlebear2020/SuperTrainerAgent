"""Tests for user channel config API router."""

from app.gateway.routers.user_channels import _mask_credential


def test_mask_credential():
    assert _mask_credential("") == "****"
    assert _mask_credential("abc") == "****"
    assert _mask_credential("abcdefgh") == "abcd****efgh"
    assert _mask_credential("a" * 32) == "aaaa****aaaa"


def test_mask_credential_short():
    assert _mask_credential("short") == "****"
