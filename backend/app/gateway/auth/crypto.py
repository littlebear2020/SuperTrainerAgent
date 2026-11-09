"""AES-256-GCM encryption service for sensitive credentials."""

from __future__ import annotations

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class CredentialEncryption:
    """Encrypts/decrypts sensitive data using AES-256-GCM.

    Uses a 256-bit key from CREDENTIAL_ENCRYPTION_KEY environment variable.
    Each encryption uses a fresh 96-bit random nonce.
    Output format: base64(nonce + ciphertext + tag)
    """

    def __init__(self, master_key: bytes | None = None):
        """Initialize with optional master key, otherwise read from env.

        Args:
            master_key: Optional 32-byte key. If None, reads from CREDENTIAL_ENCRYPTION_KEY env var.

        Raises:
            ValueError: If no key is provided and env var is not set.
        """
        if master_key is None:
            key_hex = os.getenv("CREDENTIAL_ENCRYPTION_KEY")
            if not key_hex:
                raise ValueError("CREDENTIAL_ENCRYPTION_KEY must be set. Generate with: python -c 'import os; print(os.urandom(32).hex())'")
            self.key = bytes.fromhex(key_hex)
        else:
            self.key = master_key

        if len(self.key) != 32:
            raise ValueError(f"Key must be 32 bytes (256 bits), got {len(self.key)}")

        self.aesgcm = AESGCM(self.key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt plaintext and return base64-encoded ciphertext.

        Args:
            plaintext: String to encrypt.

        Returns:
            Base64-encoded string containing nonce + ciphertext + tag.
        """
        nonce = os.urandom(12)  # 96-bit nonce (recommended for AES-GCM)
        ciphertext = self.aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, encrypted: str) -> str:
        """Decrypt base64-encoded ciphertext.

        Args:
            encrypted: Base64-encoded string from encrypt().

        Returns:
            Decrypted plaintext string.

        Raises:
            cryptography.exceptions.InvalidTag: If data is corrupted or key is wrong.
        """
        data = base64.b64decode(encrypted)
        if len(data) < 12:
            raise ValueError("Encrypted data too short")

        nonce, ciphertext = data[:12], data[12:]
        plaintext = self.aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext.decode("utf-8")
