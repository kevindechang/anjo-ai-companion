"""Encryption primitives — AES-256-GCM at-rest encryption, blind index, PII scrubbing.

All keys are derived from ANJO_SECRET via HKDF-SHA256. Keys are computed lazily
on first use (not at import time) so the env var is always read after conftest.py
sets it in tests.

Three key domains:
  DB_KEY     — SQLite column values (email, tokens, facts, messages)
  CHROMA_KEY — ChromaDB document strings
  FILES_KEY  — Per-user files (self_core, journal, reflection log, session)

Backward-compat sentinel:
  DB fields  — plaintext is stored as-is; encrypted values start with "enc1:"
  Files      — plaintext files have arbitrary bytes; encrypted files start with b"\\xAE\\x01"
"""
from __future__ import annotations

import base64
import hashlib
import hmac as _hmac
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

_FILE_MAGIC = b"\xAE\x01"
_FIELD_PREFIX = "enc1:"
_NONCE_SIZE = 12

# Cached derived keys — populated on first call, never before
_key_cache: dict[bytes, bytes] = {}


def _get_key(info: bytes) -> bytes:
    """Derive a 32-byte AES key from ANJO_SECRET via HKDF-SHA256. Cached per info."""
    if info not in _key_cache:
        secret = os.environ.get("ANJO_SECRET", "")
        if not secret:
            if os.environ.get("ANJO_ENV", "") != "dev":
                raise RuntimeError(
                    "ANJO_SECRET is not set — cannot derive encryption keys in production. "
                    "Set ANJO_SECRET to a minimum 32-byte random value."
                )
            secret = "dev_fallback_key_not_for_production"
        elif len(secret) < 32:
            import logging
            logging.getLogger(__name__).warning(
                "ANJO_SECRET is short (%d chars). Use ≥ 32 chars for production.",
                len(secret),
            )
        _key_cache[info] = HKDF(
            algorithm=SHA256(),
            length=32,
            salt=None,
            info=info,
        ).derive(secret.encode())
    return _key_cache[info]


def verify_production_key() -> None:
    """Verify ANJO_SECRET is properly configured. Call during application startup.

    Raises RuntimeError if the system would use the dev fallback key in production.
    Warns if the dev fallback key is used while real user data already exists.
    """
    secret = os.environ.get("ANJO_SECRET", "")
    if secret and len(secret) >= 32:
        return  # Properly configured
    if secret and len(secret) < 32:
        import logging
        logging.getLogger(__name__).warning(
            "⚠️  ANJO_SECRET is short (%d chars). Minimum 32 recommended.", len(secret)
        )
        return
    # No secret set
    if os.environ.get("ANJO_ENV", "") != "dev":
        raise RuntimeError(
            "ANJO_SECRET is not set. Required for production."
        )
    # Dev mode — warn if user data already exists
    _users_dir = Path(__file__).parent.parent.parent / "data" / "users"
    if _users_dir.exists() and any(_users_dir.iterdir()):
        import logging
        logging.getLogger(__name__).warning(
            "⚠️  Using dev fallback encryption key with existing user data. "
            "Set ANJO_SECRET for production use."
        )


def _db_key() -> bytes:
    return _get_key(b"anjo-db-aes256gcm-v1")


def _chroma_key() -> bytes:
    return _get_key(b"anjo-chroma-docs-v1")


def _files_key() -> bytes:
    return _get_key(b"anjo-files-aes256gcm-v1")


def _hmac_key() -> bytes:
    return _get_key(b"anjo-blind-index-v1")


# ── DB column encryption ──────────────────────────────────────────────────────

def encrypt_db(plaintext: str) -> str:
    """AES-256-GCM encrypt a string for DB storage.

    Returns "enc1:<base64(12-byte nonce + ciphertext + 16-byte GCM tag)>".
    Empty strings are stored as-is (no encryption needed for cleared tokens).
    """
    if not plaintext:
        return plaintext
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(_db_key()).encrypt(nonce, plaintext.encode(), None)
    return _FIELD_PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_db(value: str) -> str:
    """Decrypt an enc1:-prefixed DB value.

    Returns plaintext unchanged if value is empty or does not start with "enc1:"
    (legacy plaintext — backward-compatible during migration window).
    """
    if not value or not value.startswith(_FIELD_PREFIX):
        return value
    blob = base64.b64decode(value[len(_FIELD_PREFIX):])
    nonce, ct = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(_db_key()).decrypt(nonce, ct, None).decode()


# ── ChromaDB document encryption ─────────────────────────────────────────────

def encrypt_chroma(plaintext: str) -> str:
    """Encrypt a ChromaDB document string using the chroma sub-key."""
    if not plaintext:
        return plaintext
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(_chroma_key()).encrypt(nonce, plaintext.encode(), None)
    return _FIELD_PREFIX + base64.b64encode(nonce + ct).decode()


def decrypt_chroma(value: str) -> str:
    """Decrypt a ChromaDB document string. Returns plaintext if not encrypted (legacy)."""
    if not value or not value.startswith(_FIELD_PREFIX):
        return value
    blob = base64.b64decode(value[len(_FIELD_PREFIX):])
    nonce, ct = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(_chroma_key()).decrypt(nonce, ct, None).decode()


# ── File encryption ───────────────────────────────────────────────────────────

def write_encrypted(content: str) -> bytes:
    """Encrypt content to bytes for file storage.

    Returns: FILE_MAGIC (2 bytes) + nonce (12 bytes) + GCM ciphertext.
    Caller is responsible for writing these bytes to disk.
    """
    nonce = os.urandom(_NONCE_SIZE)
    ct = AESGCM(_files_key()).encrypt(nonce, content.encode(), None)
    return _FILE_MAGIC + nonce + ct


def read_encrypted(path: Path) -> str:
    """Read a file and decrypt it if it starts with FILE_MAGIC.

    Returns UTF-8 text for legacy plaintext files (backward-compat during migration).
    """
    data = path.read_bytes()
    if not data.startswith(_FILE_MAGIC):
        return data.decode("utf-8")
    blob = data[len(_FILE_MAGIC):]
    nonce, ct = blob[:_NONCE_SIZE], blob[_NONCE_SIZE:]
    return AESGCM(_files_key()).decrypt(nonce, ct, None).decode()


# ── Blind index ───────────────────────────────────────────────────────────────

def hmac_index(value: str) -> str:
    """HMAC-SHA256(blind-index key, value.lower()) as hex.

    Deterministic (no nonce) — used for equality lookups on encrypted columns.
    Always lower-cases the input so look-ups are case-insensitive.
    """
    return _hmac.new(_hmac_key(), value.lower().encode(), hashlib.sha256).hexdigest()


# ── PII scrubbing ─────────────────────────────────────────────────────────────

_EMAIL_PAT = re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')
_PHONE_PAT = re.compile(
    r'(?<!\d)(\+?1[\s.\-]?)?(\(?\d{3}\)?[\s.\-]?)\d{3}[\s.\-]?\d{4}(?!\d)'
)


def scrub_pii(text: str) -> str:
    """Redact email addresses and phone numbers from text.

    Used before computing embeddings so PII is not encoded into vectors.
    """
    text = _EMAIL_PAT.sub('[EMAIL]', text)
    text = _PHONE_PAT.sub('[PHONE]', text)
    return text
