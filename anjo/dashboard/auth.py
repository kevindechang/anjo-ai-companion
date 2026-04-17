"""Multi-user auth: bcrypt passwords, per-user signed cookies."""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import bcrypt as _bcrypt
from fastapi import HTTPException, Request

from anjo.core.crypto import decrypt_db, encrypt_db, hmac_index
from anjo.core.db import get_db
from anjo.core.logger import logger

_EMAIL_RE = re.compile(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$')


def validate_password_strength(password: str) -> str | None:
    """Return an error message if the password is too weak, or None if it passes.

    Policy: minimum 8 characters + at least one digit or non-letter character.
    This blocks the most common word-list passwords (e.g. 'password', 'iloveyou')
    while remaining usable.
    """
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not any(c.isdigit() or not c.isalpha() for c in password):
        return "Password must contain at least one number or symbol."
    return None

# ── Token revocation ──────────────────────────────────────────────────────────
# Stores (signature_segment, exp_timestamp) tuples for revoked tokens.
# In-memory — acceptable for single-process deployment with short-lived tokens.
_revoked_tokens: set[tuple[str, int]] = set()


def load_revoked_tokens_from_db() -> None:
    """Load unexpired revoked tokens from DB into the in-memory set on startup."""
    import time
    now = int(time.time())
    try:
        rows = get_db().execute(
            "SELECT sig, exp FROM revoked_tokens WHERE exp > ?", (now,)
        ).fetchall()
        for row in rows:
            _revoked_tokens.add((row["sig"], row["exp"]))
    except Exception:
        pass


def revoke_token(token: str) -> None:
    """Add a token's signature to the revocation set (memory + DB)."""
    try:
        parts = token.split(".")
        if len(parts) == 4:
            sig = parts[3]
            exp = int(parts[2])
            _revoked_tokens.add((sig, exp))
            try:
                db = get_db()
                db.execute(
                    "INSERT OR IGNORE INTO revoked_tokens (sig, exp) VALUES (?, ?)",
                    (sig, exp),
                )
                db.commit()
            except Exception:
                pass  # in-memory revocation still works
    except Exception:
        pass


def _cleanup_revoked() -> None:
    """Prune expired entries from the revocation set."""
    import time
    now = int(time.time())
    expired = {entry for entry in _revoked_tokens if entry[1] <= now}
    _revoked_tokens.difference_update(expired)

# Pre-hashed dummy password for constant-time login checks.
# Without this, a missing user returns instantly while a found user takes ~200ms
# (bcrypt), leaking which emails are registered via timing.
_DUMMY_HASH = _bcrypt.hashpw(b"dummy_constant_time", _bcrypt.gensalt())

COOKIE_NAME  = "anjo_auth"
_DATA_ROOT   = Path(__file__).parent.parent.parent / "data"


_DEV_SECRET: str | None = None


def _get_secret() -> str:
    secret = os.environ.get("ANJO_SECRET", "")
    if not secret:
        if os.environ.get("ANJO_ENV") != "dev":
            raise RuntimeError(
                "CRITICAL: ANJO_SECRET is not set. Refusing to start in production."
            )
        global _DEV_SECRET
        if _DEV_SECRET is None:
            import secrets as _secrets
            import warnings
            _DEV_SECRET = _secrets.token_hex(32)
            warnings.warn(
                "ANJO_SECRET is not set — generated a random secret for this process. "
                "All sessions will be invalidated on restart. Set ANJO_SECRET in .env before production.",
                stacklevel=2,
            )
        return _DEV_SECRET
    return secret


# ── User registry ─────────────────────────────────────────────────────────────

def has_any_users() -> bool:
    row = get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone()
    return row is not None


def register_user(username: str, password: str, email: str) -> tuple[dict | None, str]:
    """Create a new user. Returns (user dict, '') on success or (None, 'username'/'email') on conflict."""
    email = email.strip().lower()
    if not email or not _EMAIL_RE.match(email):
        return None, "email"

    # Hash password before touching the db — bcrypt is intentionally slow
    hashed = _bcrypt.hashpw(password.encode(), _bcrypt.gensalt()).decode()
    user_id            = str(uuid.uuid4())
    verification_token = str(uuid.uuid4())
    created_at         = datetime.now(timezone.utc).isoformat()

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users "
            "(user_id, username, email, email_hmac, hashed_password, "
            "verification_token, verification_token_hmac, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                user_id, username,
                encrypt_db(email), hmac_index(email),
                hashed,
                encrypt_db(verification_token), hmac_index(verification_token),
                created_at,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        row = db.execute("SELECT 1 FROM users WHERE username = ?", (username,)).fetchone()
        return None, "username" if row else "email"

    return {
        "user_id":            user_id,
        "username":           username,
        "email":              email,
        "email_verified":     False,
        "verification_token": verification_token,
        "created_at":         created_at,
    }, ""


def verify_email_token(token: str) -> str | None:
    """Validate a verification token. Returns user_id if valid, None otherwise."""
    if not token or len(token) < 8:
        return None
    db = get_db()
    row = db.execute(
        "SELECT user_id FROM users WHERE verification_token_hmac = ? AND email_verified = 0",
        (hmac_index(token),),
    ).fetchone()
    if not row:
        return None
    db.execute(
        "UPDATE users SET email_verified = 1, verification_token = '', verification_token_hmac = '' "
        "WHERE user_id = ?",
        (row["user_id"],),
    )
    db.commit()
    return row["user_id"]


def force_verify_email(username: str) -> None:
    """Mark a user's email as verified (fallback when email service is unavailable)."""
    db = get_db()
    db.execute(
        "UPDATE users SET email_verified = 1, verification_token = '', verification_token_hmac = '' "
        "WHERE username = ?",
        (username,),
    )
    db.commit()


def is_email_verified(user_id: str) -> bool:
    row = get_db().execute(
        "SELECT email_verified, verification_token FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return True
    # Legacy users created before email enforcement have an empty verification_token — treat as verified.
    return bool(row["email_verified"]) or not row["verification_token"]


def authenticate_user(username_or_email: str, password: str) -> str | None:
    """Verify credentials. Returns user_id on success, None on failure.
    Supports case-insensitive username (COLLATE NOCASE) or email login."""
    db  = get_db()
    inp = username_or_email.strip()
    row = db.execute(
        "SELECT user_id, hashed_password FROM users WHERE username = ? OR email_hmac = ?",
        (inp, hmac_index(inp)),
    ).fetchone()
    if not row:
        # Run dummy bcrypt to equalise response time — prevents email enumeration via timing.
        _bcrypt.checkpw(password.encode(), _DUMMY_HASH)
        return None
    try:
        if _bcrypt.checkpw(password.encode(), row["hashed_password"].encode()):
            return row["user_id"]
    except Exception:
        pass
    return None


def verify_password(user_id: str, password: str) -> bool:
    row = get_db().execute(
        "SELECT hashed_password FROM users WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return False
    try:
        return _bcrypt.checkpw(password.encode(), row["hashed_password"].encode())
    except Exception:
        return False


def delete_account(user_id: str) -> None:
    """Remove user from db and wipe all their data files."""
    import shutil
    db = get_db()
    for table in ("daily_usage", "facts", "letter_cache", "credits", "subscriptions", "users"):
        db.execute(f"DELETE FROM {table} WHERE user_id = ?", (user_id,))  # noqa: S608
    db.commit()
    user_dir = _DATA_ROOT / "users" / user_id
    if user_dir.exists():
        shutil.rmtree(user_dir)
    # Wipe ChromaDB vectors (global collection, filtered by user_id)
    try:
        from anjo.memory.long_term import _get_collections
        semantic_col, emotional_col = _get_collections()
        for col in (semantic_col, emotional_col):
            try:
                ids = col.get(where={"user_id": user_id}, include=[])["ids"]
                if ids:
                    col.delete(ids=ids)
            except Exception as e:
                logger.warning(f"Could not delete vectors for {user_id} from {col.name}: {e}")
    except Exception as e:
        logger.error(f"ChromaDB cleanup failed on account deletion for {user_id}: {e}")


def generate_reset_token(email: str) -> tuple[str, str] | None:
    """Find user by email, generate a 1-hour reset token. Returns (username, token) or None."""
    from datetime import timedelta
    db  = get_db()
    row = db.execute(
        "SELECT user_id, username FROM users WHERE email_hmac = ?",
        (hmac_index(email.strip()),),
    ).fetchone()
    if not row:
        return None
    token  = str(uuid.uuid4())
    expiry = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    db.execute(
        "UPDATE users SET reset_token = ?, reset_token_hmac = ?, reset_expiry = ? WHERE user_id = ?",
        (encrypt_db(token), hmac_index(token), expiry, row["user_id"]),
    )
    db.commit()
    return row["username"], token


def validate_reset_token(token: str) -> str | None:
    """Return username if token is valid and not expired, else None."""
    row = get_db().execute(
        "SELECT username, reset_expiry FROM users WHERE reset_token_hmac = ?",
        (hmac_index(token),),
    ).fetchone()
    if not row:
        return None
    try:
        if datetime.fromisoformat(row["reset_expiry"]) > datetime.now(timezone.utc):
            return row["username"]
    except Exception:
        pass
    return None


def consume_reset_token(token: str, new_password: str) -> bool:
    """Validate token, update password, clear token. Returns True on success.

    Uses BEGIN IMMEDIATE to acquire a write lock before reading, preventing a
    TOCTOU race where two concurrent requests with the same token both pass
    expiry validation before either UPDATE commits.
    """
    db = get_db()
    # Hash before opening the transaction — bcrypt is slow and we don't want
    # to hold the write lock while doing CPU-intensive work.
    hashed = _bcrypt.hashpw(new_password.encode(), _bcrypt.gensalt()).decode()
    try:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT user_id, reset_expiry FROM users WHERE reset_token_hmac = ?",
            (hmac_index(token),),
        ).fetchone()
        if not row:
            db.execute("ROLLBACK")
            return False
        try:
            if datetime.fromisoformat(row["reset_expiry"]) <= datetime.now(timezone.utc):
                db.execute("ROLLBACK")
                return False
        except Exception:
            db.execute("ROLLBACK")
            return False
        changed_at = datetime.now(timezone.utc).isoformat()
        db.execute(
            "UPDATE users SET hashed_password = ?, reset_token = '', reset_token_hmac = '', "
            "reset_expiry = '', password_changed_at = ? WHERE user_id = ?",
            (hashed, changed_at, row["user_id"]),
        )
        db.commit()
        return True
    except Exception:
        try:
            db.execute("ROLLBACK")
        except Exception:
            pass
        raise


def get_user_info(user_id: str) -> dict | None:
    """Return public profile fields for a user_id."""
    row = get_db().execute(
        "SELECT username, email, email_verified, created_at FROM users WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if not row:
        return None
    return {
        "username":       row["username"],
        "email":          decrypt_db(row["email"]),
        "email_verified": bool(row["email_verified"]),
        "created_at":     row["created_at"],
    }


def update_email(user_id: str, new_email: str) -> tuple[bool | str, str | None]:
    """Returns (result, token).

    result: True on success, 'taken' if email already registered, False on other failure.
    token: the new verification token (UUID) when result is True, else None.
    """
    db = get_db()
    new_email = new_email.strip().lower()
    new_token = str(uuid.uuid4()) if new_email else ""
    try:
        db.execute(
            "UPDATE users SET email = ?, email_hmac = ?, email_verified = 0, "
            "verification_token = ?, verification_token_hmac = ? WHERE user_id = ?",
            (
                encrypt_db(new_email), hmac_index(new_email),
                encrypt_db(new_token), hmac_index(new_token) if new_token else "",
                user_id,
            ),
        )
        db.commit()
    except sqlite3.IntegrityError:
        return "taken", None
    row = db.execute("SELECT changes()").fetchone()
    ok = row[0] > 0 if row is not None else False
    return ok, (new_token if ok else None)


def update_username(user_id: str, new_username: str) -> bool:
    """Rename a user. Returns True on success, False if name already taken."""
    db = get_db()
    try:
        db.execute(
            "UPDATE users SET username = ? WHERE user_id = ?", (new_username, user_id)
        )
        db.commit()
        row = db.execute("SELECT changes()").fetchone()
        return row[0] > 0 if row is not None else False
    except sqlite3.IntegrityError:
        return False


def change_password(user_id: str, new_password: str) -> bool:
    db = get_db()
    hashed = _bcrypt.hashpw(new_password.encode(), _bcrypt.gensalt()).decode()
    changed_at = datetime.now(timezone.utc).isoformat()
    db.execute(
        "UPDATE users SET hashed_password = ?, password_changed_at = ? WHERE user_id = ?",
        (hashed, changed_at, user_id),
    )
    db.commit()
    row = db.execute("SELECT changes()").fetchone()
    return row[0] > 0 if row is not None else False


def list_users() -> list[dict]:
    """Return all users as a list of dicts (for admin use)."""
    rows = get_db().execute(
        "SELECT user_id, username, email, email_verified, created_at FROM users ORDER BY created_at DESC"
    ).fetchall()
    return [
        {**dict(r), "email": decrypt_db(r["email"])}
        for r in rows
    ]


# ── Cookie tokens ─────────────────────────────────────────────────────────────

# Token expires after 7 days
_TOKEN_EXPIRY_SECONDS = 7 * 24 * 60 * 60


def make_token(user_id: str) -> str:
    """Create a signed token with expiration (iat + exp)."""
    import time
    secret = _get_secret()
    now = int(time.time())
    exp = now + _TOKEN_EXPIRY_SECONDS
    # Token format: user_id.iat.exp.signature
    # The signed data includes everything before the signature
    signed_data = f"{user_id}.{now}.{exp}"
    sig = hmac.new(secret.encode(), signed_data.encode(), hashlib.sha256).hexdigest()
    return f"{signed_data}.{sig}"


def verify_token(token: str) -> str | None:
    """Validate token, return user_id or None. Checks expiration."""
    import time
    try:
        parts = token.split(".")
    except (ValueError, AttributeError):
        return None

    # New format: user_id.iat.exp.signature (4 parts)
    if len(parts) == 4:
        user_id, iat_str, exp_str, sig = parts
        try:
            iat = int(iat_str)
            exp = int(exp_str)
        except ValueError:
            return None
        # Check expiration
        now = int(time.time())
        if now > exp:
            return None
        signed_data = f"{user_id}.{iat_str}.{exp_str}"
    # Old format (backward compat): user_id.signature (2 parts) — reject, force re-login
    elif len(parts) == 2:
        # Legacy tokens never expire but are insecure — reject them
        logger.warning("Legacy token rejected - users must re-login")
        return None
    else:
        return None

    secret = _get_secret()
    expected = hmac.new(secret.encode(), signed_data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, expected):
        return None

    # Check revocation list
    _cleanup_revoked()
    if (sig, exp) in _revoked_tokens:
        return None

    # Check user still exists and password_changed_at
    try:
        row = get_db().execute(
            "SELECT password_changed_at FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            # Account deleted — token is no longer valid
            return None
        if row["password_changed_at"]:
            changed_ts = int(datetime.fromisoformat(row["password_changed_at"]).timestamp())
            if iat < changed_ts:
                return None
    except Exception:
        pass

    return user_id


def valid_token(token: str) -> bool:
    return verify_token(token) is not None


def should_skip_auth(path: str) -> bool:
    return (
        path in {"/", "/login", "/logout", "/register", "/verify", "/forgot", "/reset",
                 "/privacy", "/terms", "/refund", "/sw.js",
                 "/api/auth/login", "/api/auth/register", "/api/billing/webhook"}
        or path.startswith("/static")
        or path == "/admin"
        or path.startswith("/api/admin")
    )


def _token_from_request(request: Request) -> str:
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:]
    return request.cookies.get(COOKIE_NAME, "")


# ── FastAPI dependency ────────────────────────────────────────────────────────

def get_current_user_id(request: Request) -> str:
    user_id = verify_token(_token_from_request(request))
    if not user_id:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user_id
