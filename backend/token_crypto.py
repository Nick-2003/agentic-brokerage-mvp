"""App-level encryption for the IBKR Flex token at rest (W4).

The Flex token is read-only but exposes the user's full real holdings + history,
so DECISION_pivot_waitlist.md requires encrypting it at rest. We do it
**app-side** (chosen over Supabase Vault/pgsodium): the backend encrypts before
storing and decrypts on read, so the DB only ever holds ciphertext and the key
lives in the app's secret env — portable, offline-testable, no DB-engine coupling.

Uses **Fernet** (AES-128-CBC + HMAC-SHA256, authenticated) from `cryptography`,
which is ALREADY a dependency (pulled by `pyjwt[crypto]`) — no new package.

Key: `FLEX_TOKEN_ENC_KEY` — a urlsafe-base64 32-byte Fernet key. Mint one with
`python -m token_crypto` (or `Fernet.generate_key()`), put it in the backend
secret store, and NEVER commit it. Rotating the key invalidates existing
ciphertext (re-encryption is a future migration concern; out of W4 scope).

Fail-closed: encrypt/decrypt raise `TokenCryptoError` when the key is missing or
a token can't be decrypted (wrong/rotated key, tampered ciphertext) — never
return a bogus plaintext.
"""
from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class TokenCryptoError(Exception):
    """Key missing/invalid, or ciphertext undecryptable."""

    def __init__(self, message: str, code: str = "token_crypto_error") -> None:
        self.code = code
        super().__init__(message)


def _key() -> bytes:
    k = os.getenv("FLEX_TOKEN_ENC_KEY", "").strip()
    if not k or k.endswith("REPLACE"):
        raise TokenCryptoError("FLEX_TOKEN_ENC_KEY not configured", code="token_crypto_no_key")
    return k.encode()


def token_crypto_configured() -> bool:
    """True when a usable Fernet key is set (shape-validated)."""
    try:
        Fernet(_key())
        return True
    except (TokenCryptoError, ValueError, TypeError):
        return False


def encrypt_token(plaintext: str) -> str:
    """Plaintext Flex token → urlsafe-base64 ciphertext string (for DB storage)."""
    if not plaintext:
        raise TokenCryptoError("refusing to encrypt an empty token", code="token_crypto_empty")
    try:
        return Fernet(_key()).encrypt(plaintext.encode()).decode()
    except (ValueError, TypeError) as e:  # malformed key
        raise TokenCryptoError(f"bad encryption key: {e}", code="token_crypto_bad_key") from e


def decrypt_token(ciphertext: str) -> str:
    """DB ciphertext → plaintext Flex token. Raises on a wrong/rotated key or
    tampered ciphertext (fail-closed — never a bogus token)."""
    try:
        return Fernet(_key()).decrypt(ciphertext.encode()).decode()
    except InvalidToken as e:
        raise TokenCryptoError(
            "could not decrypt Flex token (wrong/rotated key or tampered ciphertext)",
            code="token_crypto_decrypt_failed",
        ) from e
    except (ValueError, TypeError) as e:
        raise TokenCryptoError(f"bad encryption key: {e}", code="token_crypto_bad_key") from e


def generate_key() -> str:
    """Mint a fresh Fernet key (urlsafe-base64 32 bytes)."""
    return Fernet.generate_key().decode()


if __name__ == "__main__":  # `python -m token_crypto` → print a key for setup
    print(generate_key())
