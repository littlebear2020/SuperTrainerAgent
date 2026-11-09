"""Tests for CredentialEncryption."""

import os

import pytest


def test_encrypt_decrypt_roundtrip():
    from app.gateway.auth.crypto import CredentialEncryption

    key = os.urandom(32)
    enc = CredentialEncryption(master_key=key)
    plaintext = '{"app_id": "cli_abc", "app_secret": "secret123"}'
    encrypted = enc.encrypt(plaintext)
    assert encrypted != plaintext
    assert enc.decrypt(encrypted) == plaintext


def test_different_nonces_produce_different_ciphertexts():
    from app.gateway.auth.crypto import CredentialEncryption

    key = os.urandom(32)
    enc = CredentialEncryption(master_key=key)
    assert enc.encrypt("same input") != enc.encrypt("same input")


def test_missing_env_var_raises():
    from app.gateway.auth.crypto import CredentialEncryption

    old = os.environ.pop("CREDENTIAL_ENCRYPTION_KEY", None)
    try:
        with pytest.raises(ValueError, match="CREDENTIAL_ENCRYPTION_KEY"):
            CredentialEncryption()
    finally:
        if old is not None:
            os.environ["CREDENTIAL_ENCRYPTION_KEY"] = old


def test_invalid_key_length_raises():
    from app.gateway.auth.crypto import CredentialEncryption

    with pytest.raises(ValueError, match="32 bytes"):
        CredentialEncryption(master_key=b"short")


def test_decrypt_corrupted_data_raises():
    import base64

    from app.gateway.auth.crypto import CredentialEncryption

    key = os.urandom(32)
    enc = CredentialEncryption(master_key=key)
    corrupted = base64.b64encode(b"tooshort").decode()
    with pytest.raises(ValueError, match="too short"):
        enc.decrypt(corrupted)
