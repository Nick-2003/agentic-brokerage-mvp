"""Versioned application-level encryption for per-user brokerage secrets.

SnapTrade's ``userSecret`` is encrypted before it reaches Postgres.  The ciphertext
format carries a version prefix so a future key-rotation migration can distinguish
old records instead of guessing how they were encrypted.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


_VERSION = "fernet-v1"


class BrokerSecretCryptoError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def _key() -> bytes:
    value = os.getenv("BROKER_SECRET_ENC_KEY", "").strip()
    if not value or value.endswith("REPLACE"):
        raise BrokerSecretCryptoError(
            "broker_secret_no_key", "BROKER_SECRET_ENC_KEY is not configured"
        )
    return value.encode()


def broker_secret_crypto_configured() -> bool:
    try:
        Fernet(_key())
        return True
    except (BrokerSecretCryptoError, ValueError, TypeError):
        return False


def encrypt_broker_secret(plaintext: str) -> str:
    if not plaintext:
        raise BrokerSecretCryptoError(
            "broker_secret_empty", "refusing to encrypt an empty brokerage secret"
        )
    try:
        ciphertext = Fernet(_key()).encrypt(plaintext.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise BrokerSecretCryptoError(
            "broker_secret_bad_key", f"invalid brokerage encryption key: {exc}"
        ) from exc
    return f"{_VERSION}:{ciphertext}"


def decrypt_broker_secret(encoded: str) -> str:
    try:
        version, ciphertext = encoded.split(":", 1)
    except (AttributeError, ValueError) as exc:
        raise BrokerSecretCryptoError(
            "broker_secret_bad_format", "brokerage secret has no supported version"
        ) from exc
    if version != _VERSION:
        raise BrokerSecretCryptoError(
            "broker_secret_bad_version", f"unsupported brokerage secret version: {version}"
        )
    try:
        return Fernet(_key()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as exc:
        raise BrokerSecretCryptoError(
            "broker_secret_decrypt_failed",
            "brokerage secret could not be decrypted",
        ) from exc
    except (ValueError, TypeError) as exc:
        raise BrokerSecretCryptoError(
            "broker_secret_bad_key", f"invalid brokerage encryption key: {exc}"
        ) from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()


if __name__ == "__main__":
    print(generate_key())
