"""Tests for anjo/core/crypto.py — AES-256-GCM encryption primitives."""

from __future__ import annotations

# ── crypto module ─────────────────────────────────────────────────────────────


def test_encrypt_decrypt_db_roundtrip():
    from anjo.core.crypto import decrypt_db, encrypt_db

    plaintext = "user@example.com"
    enc = encrypt_db(plaintext)
    assert enc.startswith("enc1:")
    assert enc != plaintext
    assert decrypt_db(enc) == plaintext


def test_decrypt_db_legacy_passthrough():
    """Plaintext values without enc1: prefix are returned as-is (migration window)."""
    from anjo.core.crypto import decrypt_db

    assert decrypt_db("legacy@example.com") == "legacy@example.com"
    assert decrypt_db("") == ""


def test_encrypt_db_empty_passthrough():
    """Empty strings are not encrypted (cleared tokens stay empty)."""
    from anjo.core.crypto import encrypt_db

    assert encrypt_db("") == ""


def test_encrypt_db_nonce_randomness():
    """Each encryption call produces a unique ciphertext."""
    from anjo.core.crypto import encrypt_db

    enc1 = encrypt_db("same text")
    enc2 = encrypt_db("same text")
    assert enc1 != enc2  # different nonces


def test_encrypt_decrypt_chroma_roundtrip():
    from anjo.core.crypto import decrypt_chroma, encrypt_chroma

    summary = "User talked about losing their job. Felt anxious and unsupported."
    enc = encrypt_chroma(summary)
    assert enc.startswith("enc1:")
    assert decrypt_chroma(enc) == summary


def test_decrypt_chroma_legacy_passthrough():
    from anjo.core.crypto import decrypt_chroma

    assert decrypt_chroma("plain old summary") == "plain old summary"


def test_write_read_encrypted_file(tmp_path):
    from anjo.core.crypto import _FILE_MAGIC, read_encrypted, write_encrypted

    content = '{"version": 1, "mood": {"valence": 0.5}}'
    path = tmp_path / "current.json"
    path.write_bytes(write_encrypted(content))
    # File starts with magic header
    assert path.read_bytes()[:2] == _FILE_MAGIC
    # Round-trip decrypts correctly
    assert read_encrypted(path) == content


def test_read_encrypted_legacy_plaintext(tmp_path):
    """Legacy plaintext files are returned without decryption."""
    from anjo.core.crypto import read_encrypted

    content = '{"version": 1}'
    path = tmp_path / "old.json"
    path.write_text(content, encoding="utf-8")
    assert read_encrypted(path) == content


def test_hmac_index_deterministic():
    from anjo.core.crypto import hmac_index

    h1 = hmac_index("user@example.com")
    h2 = hmac_index("user@example.com")
    assert h1 == h2
    assert len(h1) == 64  # SHA-256 hex digest


def test_hmac_index_case_insensitive():
    from anjo.core.crypto import hmac_index

    assert hmac_index("User@Example.COM") == hmac_index("user@example.com")


def test_hmac_index_different_values():
    from anjo.core.crypto import hmac_index

    assert hmac_index("a@a.com") != hmac_index("b@b.com")


def test_scrub_pii_email():
    from anjo.core.crypto import scrub_pii

    text = "User mentioned contacting john.doe@gmail.com about their issue."
    result = scrub_pii(text)
    assert "[EMAIL]" in result
    assert "john.doe@gmail.com" not in result


def test_scrub_pii_phone():
    from anjo.core.crypto import scrub_pii

    text = "User gave their number as 555-867-5309."
    result = scrub_pii(text)
    assert "[PHONE]" in result
    assert "867-5309" not in result


def test_scrub_pii_no_pii():
    from anjo.core.crypto import scrub_pii

    text = "User discussed their anxiety about work."
    assert scrub_pii(text) == text


def test_different_domains_produce_different_keys():
    """DB, chroma, and file keys must be distinct to prevent cross-domain attacks."""
    from anjo.core.crypto import _chroma_key, _db_key, _files_key

    assert _db_key() != _chroma_key()
    assert _db_key() != _files_key()
    assert _chroma_key() != _files_key()


def test_keys_are_32_bytes():
    from anjo.core.crypto import _chroma_key, _db_key, _files_key

    assert len(_db_key()) == 32
    assert len(_chroma_key()) == 32
    assert len(_files_key()) == 32


# ── Integration: SelfCore round-trip ─────────────────────────────────────────


def test_selfcore_encrypts_on_save_and_decrypts_on_load(tmp_path, monkeypatch):
    import anjo.core.self_core as _sc
    from anjo.core.crypto import _FILE_MAGIC

    monkeypatch.setattr(_sc, "_DATA_ROOT", tmp_path)
    from anjo.core.self_core import SelfCore

    core = SelfCore.load("test_user")
    core.relationship.stage = "friend"
    core.save()

    path = tmp_path / "users" / "test_user" / "self_core" / "current.json"
    assert path.exists()
    # File should be encrypted binary
    assert path.read_bytes()[:2] == _FILE_MAGIC

    # Reload and verify data survived
    loaded = SelfCore.load("test_user")
    assert loaded.relationship.stage == "friend"


# ── Integration: auth encrypt/decrypt flow ────────────────────────────────────


def test_register_stores_email_encrypted_and_hmac(client):
    """Registration stores an encrypted email and a populated email_hmac."""
    resp = client.post(
        "/register",
        data={
            "username": "cryptouser",
            "password": "securepass99",
            "email": "crypto@test.com",
        },
    )
    assert resp.status_code in (200, 302, 303)

    from anjo.core.crypto import hmac_index
    from anjo.core.db import get_db

    row = (
        get_db()
        .execute("SELECT email, email_hmac FROM users WHERE username = ?", ("cryptouser",))
        .fetchone()
    )
    assert row is not None
    assert row["email"].startswith("enc1:")
    assert row["email_hmac"] == hmac_index("crypto@test.com")


def test_email_stored_encrypted(client):
    """After registration, email column in DB should start with enc1:."""
    client.post(
        "/register",
        data={
            "username": "enctest",
            "password": "testpass99",
            "email": "enc@test.com",
        },
    )
    from anjo.core.db import get_db

    row = get_db().execute("SELECT email FROM users WHERE username = ?", ("enctest",)).fetchone()
    assert row is not None
    assert row["email"].startswith("enc1:")


def test_facts_stored_encrypted(client):
    """Facts written to DB should be encrypted."""
    from anjo.core.db import get_db
    from anjo.core.facts import load_facts, merge_facts

    merge_facts("user_enc_test", ["has a cat named Luna"], [0.9])

    row = (
        get_db()
        .execute("SELECT facts_json FROM facts WHERE user_id = ?", ("user_enc_test",))
        .fetchone()
    )
    assert row is not None
    assert row["facts_json"].startswith("enc1:")

    # But reading back through load_facts gives plaintext
    facts = load_facts("user_enc_test")
    assert any("Luna" in f for f in facts)
